from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping, Sequence, Tuple

from ..domain.agent_research import ResearchToolCall
from ..domain.financial_facts import (
    CASH_CONVERSION_ANALYSIS_VERSION,
    CashConversionAssessment,
    CashConversionComponent,
    CashConversionPeriodBridge,
    CashConversionRecurrenceBridge,
    FinancialFactObservation,
    FinancialFactSeriesPort,
    FinancialFactSeriesQuery,
    PeriodAlignedFinancialSeries,
)
from ..models import EvidenceNode, NormalizedSourceRecord


_CONCEPTS: Mapping[str, Tuple[Tuple[str, str], ...]] = {
    "net_income": (
        ("us-gaap", "NetIncomeLoss"), ("us-gaap", "ProfitLoss"),
        ("ifrs-full", "ProfitLoss"),
    ),
    "operating_cash_flow": (
        ("us-gaap", "NetCashProvidedByUsedInOperatingActivities"),
        ("us-gaap", "CashFlowsFromUsedInOperatingActivities"),
        ("ifrs-full", "CashFlowsFromUsedInOperatingActivities"),
    ),
    "capital_expenditure": (
        ("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment"),
        ("us-gaap", "CapitalExpenditures"),
        ("us-gaap", "PaymentsToAcquireProductiveAssets"),
        ("ifrs-full", "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"),
    ),
    "depreciation_amortization": (
        ("us-gaap", "DepreciationDepletionAndAmortization"),
        ("us-gaap", "DepreciationDepletionAndAmortizationPropertyPlantAndEquipment"),
    ),
    "stock_compensation": (("us-gaap", "ShareBasedCompensation"),),
    "deferred_taxes": (
        ("us-gaap", "DeferredIncomeTaxExpenseBenefit"),
        ("us-gaap", "DeferredTaxExpenseBenefit"),
    ),
    "working_capital_accounts_receivable": (
        ("us-gaap", "IncreaseDecreaseInAccountsReceivable"),
    ),
    "working_capital_inventory": (
        ("us-gaap", "IncreaseDecreaseInInventories"),
    ),
    "working_capital_accounts_payable": (
        ("us-gaap", "IncreaseDecreaseInAccountsPayable"),
    ),
    "working_capital_deferred_revenue": (
        ("us-gaap", "IncreaseDecreaseInDeferredRevenue"),
        ("us-gaap", "IncreaseDecreaseInContractWithCustomerLiability"),
    ),
    "working_capital_other_assets": (
        ("us-gaap", "IncreaseDecreaseInOtherOperatingAssets"),
        ("us-gaap", "IncreaseDecreaseInOtherReceivables"),
    ),
    "working_capital_other_liabilities": (
        ("us-gaap", "IncreaseDecreaseInOtherOperatingLiabilities"),
    ),
    "cash_taxes": (("us-gaap", "IncomeTaxesPaidNet"),),
    "debt_issued_long_term": (("us-gaap", "ProceedsFromIssuanceOfLongTermDebt"),),
    "debt_issued_short_term": (
        ("us-gaap", "ProceedsFromOtherShortTermDebt"),
        ("us-gaap", "ProceedsFromShortTermDebtMaturingInMoreThanThreeMonths"),
    ),
    "debt_repaid_long_term": (("us-gaap", "RepaymentsOfLongTermDebt"),),
    "debt_repaid_short_term": (
        ("us-gaap", "RepaymentsOfOtherShortTermDebt"),
        ("us-gaap", "RepaymentsOfShortTermDebtMaturingInMoreThanThreeMonths"),
    ),
    "dividends": (
        ("us-gaap", "PaymentsOfDividends"),
        ("us-gaap", "PaymentsOfDividendsCommonStock"),
    ),
    "repurchases": (
        ("us-gaap", "PaymentsForRepurchaseOfCommonStock"),
        ("us-gaap", "PaymentsForRepurchaseOfCommonShares"),
    ),
}

_WORKING_CAPITAL_KEYS = (
    "working_capital_accounts_receivable",
    "working_capital_inventory",
    "working_capital_accounts_payable",
    "working_capital_deferred_revenue",
    "working_capital_other_assets",
    "working_capital_other_liabilities",
)
_WORKING_CAPITAL_CASH_EFFECT_SIGN = {
    # SEC IncreaseDecrease concepts report the balance movement. Increases in
    # operating assets consume cash; increases in operating liabilities
    # provide cash. Preserve negative reported movements before applying the
    # cash-effect sign rather than treating missing rows as zero.
    "working_capital_accounts_receivable": -1.0,
    "working_capital_inventory": -1.0,
    "working_capital_other_assets": -1.0,
    "working_capital_accounts_payable": 1.0,
    "working_capital_deferred_revenue": 1.0,
    "working_capital_other_liabilities": 1.0,
}
_DEBT_ISSUED_KEYS = ("debt_issued_long_term", "debt_issued_short_term")
_DEBT_REPAID_KEYS = ("debt_repaid_long_term", "debt_repaid_short_term")
_CORE_KEYS = ("net_income", "operating_cash_flow", "capital_expenditure")


def normalize_financial_fact_metric(value: str) -> str:
    """Resolve model/user metric wording to the registered financial concept key."""

    text = str(value or "").strip()
    if not text:
        return ""
    snake = re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_")
    if snake in _CONCEPTS:
        return snake
    compact = re.sub(r"[^a-z0-9]", "", text.casefold())
    aliases: dict[str, str] = {}
    for normalized, concepts in _CONCEPTS.items():
        aliases[re.sub(r"[^a-z0-9]", "", normalized.casefold())] = normalized
        aliases[re.sub(r"[^a-z0-9]", "", normalized.replace("_", " ").casefold())] = normalized
        for _, concept in concepts:
            aliases[re.sub(r"[^a-z0-9]", "", concept.casefold())] = normalized
    aliases.update({
        "operatingcashflow": "operating_cash_flow",
        "cashflowfromoperations": "operating_cash_flow",
        "capitalexpenditure": "capital_expenditure",
        "capitalexpenditures": "capital_expenditure",
        "capex": "capital_expenditure",
        "dividendspaid": "dividends",
        "commonstockrepurchases": "repurchases",
        "sharebuybacks": "repurchases",
        "longtermdebtrepayments": "debt_repaid_long_term",
        "longtermdebtissuance": "debt_issued_long_term",
    })
    return aliases.get(compact, snake)


class SecCompanyFactsFinancialFactSeries(FinancialFactSeriesPort):
    """Read a cutoff-safe, period-aware series from one SEC Company Facts snapshot."""

    def __init__(
        self,
        company_facts: Mapping[str, Any] | None,
        *,
        ticker: str,
        security_key: str,
        company_name: str = "",
    ) -> None:
        self.company_facts = dict(company_facts or {})
        self.ticker = ticker.upper()
        self.security_key = security_key
        self.company_name = company_name or self.ticker
        self.source_snapshot_hash = hashlib.sha256(
            json.dumps(self.company_facts, sort_keys=True, separators=(",", ":"), default=str)
            .encode("utf-8")
        ).hexdigest()

    def query(self, query: FinancialFactSeriesQuery) -> PeriodAlignedFinancialSeries:
        requested = tuple(query.normalized_metrics) or tuple(_CONCEPTS)
        observations: list[FinancialFactObservation] = []
        for metric in requested:
            observations.extend(self._metric_observations(metric, query))
        observations = _dedupe_observations(observations)
        gaps = tuple(
            f"No cutoff-safe SEC fact series was available for {metric.replace('_', ' ')}."
            for metric in requested
            if not any(item.normalized_metric == metric for item in observations)
        )
        return PeriodAlignedFinancialSeries(
            ticker=query.ticker.upper(),
            data_cutoff=query.data_cutoff,
            observations=tuple(observations),
            source_snapshot_hash=self.source_snapshot_hash,
            status="available" if observations else "unavailable",
            gaps=gaps,
        )

    def source_records(
        self,
        series: PeriodAlignedFinancialSeries,
    ) -> tuple[NormalizedSourceRecord, ...]:
        return tuple(
            NormalizedSourceRecord(
                source_id=item.source_id,
                ticker=item.ticker,
                entity_name=self.company_name,
                security_id=self.security_key or item.ticker,
                source_family="primary_company",
                source_type="sec_filing",
                document_id=item.accession or f"{self.security_key}:{item.concept}",
                source_url=item.source_url,
                publication_date=item.filed,
                reporting_period=item.period_end,
                section="Consolidated statements of cash flows / SEC XBRL",
                page_or_table="SEC Company Facts",
                exact_excerpt=(
                    f"{item.normalized_metric.replace('_', ' ').title()}: "
                    f"{item.value:g} {item.unit}; {item.period_start or 'instant'} "
                    f"through {item.period_end}."
                ),
                metric_or_claim=item.normalized_metric.replace("_", " ").title(),
                value=item.value,
                unit=item.unit,
                currency=item.currency,
                source_tier=1,
                origin_group="sec_companyfacts",
                licensing_policy="public_filing_excerpt",
                retrieval_status="cached_document" if item.no_lookahead_safe else "fetched_live",
                parser_status="parsed",
                validation_status="validated" if item.no_lookahead_safe else "lookahead_rejected",
                citation_id="citation-" + item.source_id,
                accession=item.accession,
                form=item.form,
                content_hash=_stable_hash(asdict(item)),
                observed_at=_utc_now(),
                period_start=item.period_start,
                duration_days=item.duration_days,
                fiscal_period=item.fiscal_period,
                fiscal_year=item.fiscal_year,
            )
            for item in series.observations
            if item.no_lookahead_safe
        )

    def _metric_observations(
        self,
        normalized_metric: str,
        query: FinancialFactSeriesQuery,
    ) -> list[FinancialFactObservation]:
        facts = self.company_facts.get("facts", {})
        result: list[tuple[int, FinancialFactObservation]] = []
        for rank, (taxonomy, concept) in enumerate(_CONCEPTS.get(normalized_metric, ())):
            concept_data = facts.get(taxonomy, {}).get(concept, {})
            for unit, rows in dict(concept_data.get("units", {}) or {}).items():
                if not _unit_is_eligible(unit):
                    continue
                for row in list(rows or []):
                    if str(row.get("form") or "").upper() not in set(query.forms):
                        continue
                    if row.get("val") is None or not row.get("end"):
                        continue
                    filed = str(row.get("filed") or "")
                    cutoff_safe = not query.data_cutoff or not filed or filed <= query.data_cutoff
                    if not cutoff_safe:
                        continue
                    try:
                        value = float(row["val"])
                    except (TypeError, ValueError, OverflowError):
                        continue
                    start = str(row.get("start") or "")
                    end = str(row.get("end") or "")
                    accession = str(row.get("accn") or "")
                    source_id = "sec-fact-" + _stable_hash((
                        self.ticker, taxonomy, concept, start, end, filed,
                        accession, unit, value,
                    ))[:24]
                    result.append((rank, FinancialFactObservation(
                        source_id=source_id,
                        ticker=self.ticker,
                        normalized_metric=normalized_metric,
                        taxonomy=taxonomy,
                        concept=concept,
                        value=value,
                        unit=unit,
                        currency=unit if len(unit) == 3 else "",
                        period_start=start,
                        period_end=end,
                        duration_days=_duration_days(start, end),
                        fiscal_period=str(row.get("fp") or ""),
                        fiscal_year=_optional_int(row.get("fy")),
                        form=str(row.get("form") or ""),
                        filed=filed,
                        accession=accession,
                        source_url=(
                            "https://data.sec.gov/api/xbrl/companyfacts/"
                            + _companyfacts_cik(self.company_facts)
                            + ".json"
                        ),
                        source_snapshot_hash=self.source_snapshot_hash,
                        no_lookahead_safe=cutoff_safe,
                    )))
        # Prefer the highest-priority concept and latest cutoff-safe filing for
        # each accounting context. Comparative facts repeated in a later filing
        # therefore remain one observation rather than inflating coverage.
        selected: dict[tuple[str, str, str, str, str], tuple[int, FinancialFactObservation]] = {}
        for rank, item in result:
            key = (
                item.normalized_metric, item.period_start, item.period_end,
                item.fiscal_period, item.unit,
            )
            current = selected.get(key)
            if current is None or rank < current[0] or (
                rank == current[0] and item.filed > current[1].filed
            ):
                selected[key] = (rank, item)
        return [value[1] for value in selected.values()]


class CashConversionRecurrenceService:
    def build(
        self,
        series: PeriodAlignedFinancialSeries,
        *,
        comparison_periods: int = 2,
        intent_id: str = "",
        qa_freshness: str = "not_reviewed",
    ) -> CashConversionRecurrenceBridge:
        by_metric = _by_metric(series.observations)
        anchors = _comparison_anchors(
            by_metric.get("operating_cash_flow", ()),
            comparison_periods=max(1, comparison_periods),
        )
        periods = tuple(
            value for value in (
                self._build_period(anchor, by_metric)
                for anchor in anchors
            ) if value is not None
        )
        assessment = _assess(periods, comparison_periods)
        evidence_ids = sorted({
            evidence_id for period in periods for evidence_id in period.evidence_ids
        })
        return CashConversionRecurrenceBridge(
            ticker=series.ticker,
            status=(
                "answered" if len(periods) >= comparison_periods
                else "partially_answered" if periods else "unavailable"
            ),
            periods=periods,
            assessment=assessment,
            data_cutoff=series.data_cutoff,
            source_snapshot_hash=series.source_snapshot_hash,
            evidence_hash=_stable_hash(evidence_ids),
            intent_id=intent_id,
            qa_freshness=qa_freshness,
        )


    def _build_period(
        self,
        anchor: FinancialFactObservation,
        by_metric: Mapping[str, Sequence[FinancialFactObservation]],
    ) -> CashConversionPeriodBridge | None:
        values: dict[str, float | None] = {}
        evidence: dict[str, tuple[str, ...]] = {}
        formulas: dict[str, str] = {}
        for key, rows in by_metric.items():
            value, ids, formula = _ttm_value(anchor, rows)
            values[key] = value
            evidence[key] = ids
            formulas[key] = formula
        if not all(values.get(key) is not None for key in _CORE_KEYS):
            return None

        working_values = [
            (
                float(values[key]) * _WORKING_CAPITAL_CASH_EFFECT_SIGN[key]
                if values.get(key) is not None else None
            )
            for key in _WORKING_CAPITAL_KEYS
        ]
        working_capital = (
            sum(float(value) for value in working_values if value is not None)
            if any(value is not None for value in working_values) else None
        )
        debt_issued = _sum_available(values, _DEBT_ISSUED_KEYS)
        debt_repaid = _sum_available(values, _DEBT_REPAID_KEYS)
        net_income = _number(values.get("net_income"))
        ocf = _number(values.get("operating_cash_flow"))
        capex = _number(values.get("capital_expenditure"))
        capex = abs(capex) if capex is not None else None
        fcf = ocf - capex if ocf is not None and capex is not None else None
        dividends = _number(values.get("dividends"))
        repurchases = _number(values.get("repurchases"))
        residual = (
            fcf - sum(abs(value) for value in (dividends, repurchases) if value is not None)
            if fcf is not None and dividends is not None and repurchases is not None
            else None
        )

        grouped = {
            "working_capital": working_capital,
            "cash_taxes": values.get("cash_taxes"),
            "debt_issued": debt_issued,
            "debt_repaid": debt_repaid,
            "dividends": dividends,
            "repurchases": repurchases,
        }
        components: list[CashConversionComponent] = []
        for key in (
            "depreciation_amortization", "stock_compensation", "deferred_taxes",
            *_WORKING_CAPITAL_KEYS, "cash_taxes", *_DEBT_ISSUED_KEYS,
            *_DEBT_REPAID_KEYS, "dividends", "repurchases",
        ):
            component_value = _number(values.get(key))
            if key in _WORKING_CAPITAL_CASH_EFFECT_SIGN and component_value is not None:
                component_value *= _WORKING_CAPITAL_CASH_EFFECT_SIGN[key]
            components.append(CashConversionComponent(
                key=key,
                label=key.replace("_", " ").title(),
                value=component_value,
                status="available" if values.get(key) is not None else "not_disclosed",
                evidence_ids=evidence.get(key, ()),
                formula=formulas.get(key, ""),
            ))
        coverage = tuple(key for key, value in grouped.items() if value is not None)
        missing = tuple(key for key, value in grouped.items() if value is None)
        all_ids = tuple(sorted({
            value for ids in evidence.values() for value in ids if value
        }))
        return CashConversionPeriodBridge(
            period_start=_ttm_start(anchor),
            period_end=anchor.period_end,
            duration_days=365,
            net_income=net_income,
            operating_cash_flow=ocf,
            operating_cash_flow_to_net_income=(
                ocf / net_income if ocf is not None and net_income not in {None, 0} else None
            ),
            working_capital_change=working_capital,
            cash_taxes=_number(values.get("cash_taxes")),
            capital_expenditure=capex,
            free_cash_flow=fcf,
            free_cash_flow_to_net_income=(
                fcf / net_income if fcf is not None and net_income not in {None, 0} else None
            ),
            debt_issued=debt_issued,
            debt_repaid=debt_repaid,
            dividends=dividends,
            repurchases=repurchases,
            cash_after_dividends_and_repurchases=residual,
            components=tuple(components),
            component_coverage=coverage,
            missing_components=missing,
            evidence_ids=all_ids,
            formula=(
                f"Net income: {formulas.get('net_income', 'unavailable')} "
                f"Operating cash flow: {formulas.get('operating_cash_flow', 'unavailable')} "
                f"Capital expenditure: {formulas.get('capital_expenditure', 'unavailable')} "
                "FCF = operating cash flow - abs(capital expenditure)."
            ),
        )


@dataclass(frozen=True)
class CashConversionBridgeValidation:
    """Whether a recurrence bridge is safe to present as a two-period answer."""

    valid: bool
    reasons: tuple[str, ...] = ()


def validate_cash_conversion_recurrence_bridge(
    bridge: CashConversionRecurrenceBridge | None,
    *,
    expected_cutoff: str = "",
) -> CashConversionBridgeValidation:
    """Require two distinct, duration-aligned TTM core bridges.

    Saved artifacts may advertise an analysis version while retaining only a
    quarterly or one-period summary.  The UI may call a cash answer complete
    only after this independent validation passes.
    """

    if bridge is None:
        return CashConversionBridgeValidation(False, ("No recurrence bridge was saved.",))
    reasons: list[str] = []
    if str(getattr(bridge, "analysis_version", "") or "") != CASH_CONVERSION_ANALYSIS_VERSION:
        reasons.append("The saved bridge uses an older analysis version.")
    if str(getattr(bridge, "status", "") or "").casefold() != "answered":
        reasons.append("The saved bridge is not marked as a completed answer.")
    expected = str(expected_cutoff or "")[:10]
    actual = str(getattr(bridge, "data_cutoff", "") or "")[:10]
    if expected and actual != expected:
        reasons.append("The bridge was calculated for a different research cutoff.")
    periods = tuple(getattr(bridge, "periods", ()) or ())
    if len(periods) != 2:
        reasons.append("Two comparable TTM periods are required.")
        return CashConversionBridgeValidation(False, tuple(reasons))
    ends = [str(getattr(period, "period_end", "") or "")[:10] for period in periods]
    if not all(ends) or len(set(ends)) != 2:
        reasons.append("The comparison periods must have distinct end dates.")
    durations = [getattr(period, "duration_days", None) for period in periods]
    if any(value is None or int(value) < 330 or int(value) > 400 for value in durations):
        reasons.append("Each comparison period must be a duration-aligned TTM, not a quarter or YTD fact.")
    if all(value is not None for value in durations) and abs(int(durations[0]) - int(durations[1])) > 31:
        reasons.append("The comparison periods have materially different durations.")
    for index, period in enumerate(periods, start=1):
        missing = [
            name.replace("_", " ")
            for name in ("net_income", "operating_cash_flow", "capital_expenditure")
            if getattr(period, name, None) is None
        ]
        if missing:
            reasons.append(f"Period {index} is missing core metrics: {', '.join(missing)}.")
        if not tuple(getattr(period, "evidence_ids", ()) or ()) and not str(
            getattr(period, "formula", "") or ""
        ):
            reasons.append(f"Period {index} has no source lineage.")
        units = {
            str(getattr(component, "unit", "") or "").upper()
            for component in tuple(getattr(period, "components", ()) or ())
            if getattr(component, "value", None) is not None
        }
        if len(units) > 1:
            reasons.append(f"Period {index} contains mixed component units.")
    return CashConversionBridgeValidation(not reasons, tuple(dict.fromkeys(reasons)))


@dataclass(frozen=True)
class DeterministicEvidenceRefreshCommand:
    parent_artifact_id: str
    ticker: str
    intent_id: str
    data_cutoff: str
    analysis_version: str = CASH_CONVERSION_ANALYSIS_VERSION
    comparison_periods: int = 2
    idempotency_key: str = ""

    def __post_init__(self) -> None:
        if not self.idempotency_key:
            object.__setattr__(self, "idempotency_key", _stable_hash((
                self.parent_artifact_id, self.ticker.upper(), self.intent_id,
                self.data_cutoff, self.analysis_version, self.comparison_periods,
            ))[:24])


@dataclass(frozen=True)
class DeterministicEvidenceRefreshResult:
    command: DeterministicEvidenceRefreshCommand
    status: str
    summary: str
    result: Any | None = None
    bridge: CashConversionRecurrenceBridge | None = None
    evidence_hash: str = ""
    failure_reason: str = ""


class DeterministicCashConversionRefreshService:
    """Refresh one saved answer through the app-owned tool gateway, never a model."""

    def refresh(
        self,
        command: DeterministicEvidenceRefreshCommand,
        *,
        result: Any,
        gateway: Any,
    ) -> DeterministicEvidenceRefreshResult:
        observation = gateway.execute(ResearchToolCall(
            call_id="saved-refresh-" + command.idempotency_key,
            tool_name="compute_financial_bridge",
            question_id=command.intent_id or "cash_generation_capital_allocation",
            objective="Refresh the comparable TTM cash-conversion bridge.",
            arguments={
                "bridge_kind": "cash_conversion_recurrence",
                "comparison_periods": command.comparison_periods,
                "period_basis": "comparable_ttm",
                "component_detail": "full",
                "data_cutoff": command.data_cutoff,
            },
        ))
        if not observation.content:
            return DeterministicEvidenceRefreshResult(
                command=command,
                status="unavailable",
                summary="Historical SEC series could not be completed.",
                failure_reason=observation.failure_code or observation.summary,
            )
        bridge = cash_conversion_bridge_from_dict(observation.content[0])
        derived = copy.deepcopy(result)
        derived.cash_conversion_recurrence = bridge
        # A derived evidence revision cannot inherit a publication/closure
        # decision that covered the parent evidence hash.
        if hasattr(derived, "demo_freeze_decision"):
            derived.demo_freeze_decision = None
        hybrid = getattr(derived, "hybrid_research", None)
        if hybrid is not None:
            hybrid.cash_conversion_recurrence = bridge
            hybrid.evidence_hash = str(gateway.context.get("evidence_hash") or hybrid.evidence_hash)
            hybrid.lifecycle_state = "closure_pending"
            hybrid.closure_completed_at = ""
            hybrid.publishable_at = ""
            hybrid.published_at = ""
            hybrid.demo_freeze_decision = None
            hybrid.generation_provenance["cash_conversion_refresh"] = command.analysis_version
            hybrid.generation_provenance["cash_conversion_qa_freshness"] = "pending"
        qa = getattr(derived, "independent_research_qa", None)
        if qa is not None:
            qa.status = "evidence_refreshed_qa_pending"
            qa.closure_result = None
            qa.completed_at = ""
            qa.evidence_hash = str(
                gateway.context.get("evidence_hash") or qa.evidence_hash
            )
            if hasattr(qa, "data_gaps"):
                qa.data_gaps = list(dict.fromkeys([
                    *list(getattr(qa, "data_gaps", ()) or ()),
                    "Cash-conversion evidence changed after the prior closure snapshot.",
                ]))
        graph = getattr(derived, "evidence_graph", None)
        admitted = list(gateway.context.get("claim_eligible_evidence_ids", ()) or ())
        if graph is not None:
            graph.evidence_hash = str(gateway.context.get("evidence_hash") or graph.evidence_hash)
            graph.admitted_evidence_ids = sorted({*graph.admitted_evidence_ids, *admitted})
            existing = {item.node_id for item in graph.nodes}
            for item in gateway.context.get("searchable_evidence_catalog", ()):
                evidence_id = str(item.get("evidence_id") or "")
                if not evidence_id or evidence_id in existing or evidence_id not in admitted:
                    continue
                graph.nodes.append(EvidenceNode(
                    node_id=evidence_id,
                    node_type="financial_fact",
                    entity=command.ticker.upper(),
                    statement=str(item.get("summary") or item.get("metric") or "SEC financial fact"),
                    source=str(item.get("source_url") or "SEC Company Facts"),
                    citation=str(item.get("citation_id") or ""),
                    publication_timestamp=str(item.get("publication_date") or ""),
                    reporting_period=str(item.get("period") or ""),
                    metric=str(item.get("metric") or ""),
                    value=item.get("value"),
                    unit=str(item.get("unit") or ""),
                    currency=str(item.get("currency") or ""),
                    licensing_classification=str(item.get("licensing_policy") or "public_filing_excerpt"),
                    provider="sec.gov",
                    source_policy_admission_state="admitted",
                    no_lookahead_status="passed",
                    confidence="High",
                    validation_state="validated",
                ))
        normalized = []
        for item in gateway.context.get("normalized_sources", ()):
            try:
                normalized.append(NormalizedSourceRecord(**dict(item)))
            except (TypeError, ValueError):
                continue
        if normalized:
            derived.normalized_sources = normalized
            if hybrid is not None:
                hybrid.normalized_sources = normalized
        return DeterministicEvidenceRefreshResult(
            command=command,
            status="refreshed",
            summary="Comparable historical SEC evidence was refreshed; Independent QA is pending.",
            result=derived,
            bridge=bridge,
            evidence_hash=str(gateway.context.get("evidence_hash") or bridge.evidence_hash),
        )


def admitted_series(
    series: PeriodAlignedFinancialSeries,
    evidence_items: Iterable[Mapping[str, Any]],
) -> PeriodAlignedFinancialSeries:
    admitted = {
        str(item.get("evidence_id") or "")
        for item in evidence_items
        if str(item.get("validation_status") or "").casefold() == "validated"
    }
    return replace(series, observations=tuple(
        replace(item, evidence_id=item.source_id)
        if item.source_id in admitted else item
        for item in series.observations
    ))


def cash_conversion_bridge_from_dict(payload: Mapping[str, Any]) -> CashConversionRecurrenceBridge:
    assessment = CashConversionAssessment(**dict(payload.get("assessment") or {}))
    periods = []
    for raw in payload.get("periods", ()):
        item = dict(raw)
        item["components"] = tuple(
            CashConversionComponent(**dict(value))
            for value in item.get("components", ())
        )
        for key in ("component_coverage", "missing_components", "evidence_ids"):
            item[key] = tuple(item.get(key, ()) or ())
        periods.append(CashConversionPeriodBridge(**item))
    values = dict(payload)
    values["assessment"] = assessment
    values["periods"] = tuple(periods)
    return CashConversionRecurrenceBridge(**values)


def cash_conversion_intent_id(ticker: str, *, cik: str = "", exchange: str = "") -> str:
    from ..domain.research_intents import ResearchIntentResolver

    mapped = ResearchIntentResolver().resolve_legacy(
        ticker=ticker,
        cik=cik,
        exchange=exchange,
        question_key="cash_generation_capital_allocation",
        mechanism_key="recurring_cash_conversion",
        target_type="issuer",
        target_id=("cik:" + cik if cik else ticker.upper()),
        period_scope="comparable_ttm",
        decision_dependency="thesis_quality",
    )
    return mapped.intent.intent_id if mapped.intent is not None else ""


def _by_metric(
    observations: Sequence[FinancialFactObservation],
) -> dict[str, tuple[FinancialFactObservation, ...]]:
    grouped: dict[str, list[FinancialFactObservation]] = {}
    for item in observations:
        grouped.setdefault(item.normalized_metric, []).append(item)
    return {
        key: tuple(sorted(values, key=lambda value: (value.period_end, value.filed)))
        for key, values in grouped.items()
    }


def _comparison_anchors(
    observations: Sequence[FinancialFactObservation],
    *,
    comparison_periods: int,
) -> tuple[FinancialFactObservation, ...]:
    if not observations:
        return ()
    ordered = sorted(observations, key=lambda item: item.period_end, reverse=True)
    latest = ordered[0]
    annual = _is_annual(latest)
    result = []
    for item in ordered:
        if _is_annual(item) != annual:
            continue
        if not annual and (
            item.fiscal_period.casefold() != latest.fiscal_period.casefold()
            and abs((item.duration_days or 0) - (latest.duration_days or 0)) > 10
        ):
            continue
        if any(value.period_end == item.period_end for value in result):
            continue
        result.append(item)
        if len(result) >= comparison_periods:
            break
    return tuple(result)


def _ttm_value(
    anchor: FinancialFactObservation,
    observations: Sequence[FinancialFactObservation],
) -> tuple[float | None, tuple[str, ...], str]:
    current_candidates = [
        item for item in observations
        if item.period_end == anchor.period_end
        and abs((item.duration_days or 0) - (anchor.duration_days or 0)) <= 10
        and _is_annual(item) == _is_annual(anchor)
    ]
    if not current_candidates:
        return None, (), "Current comparable-period fact unavailable."
    current = max(current_candidates, key=lambda item: (item.filed, item.source_id))
    if _is_annual(anchor):
        return current.value, _ids(current), "Reported annual value."
    comparable = [
        item for item in observations
        if item.period_end < anchor.period_end
        and not _is_annual(item)
        and abs((item.duration_days or 0) - (anchor.duration_days or 0)) <= 10
        and _approximately_one_year(item.period_end, anchor.period_end)
    ]
    if not comparable:
        return None, (), "Prior comparable YTD fact unavailable."
    prior = max(comparable, key=lambda item: item.period_end)
    annuals = [
        item for item in observations
        if _is_annual(item)
        and prior.period_end < item.period_end < anchor.period_end
    ]
    if not annuals:
        return None, (), "Intervening annual fact unavailable."
    annual = max(annuals, key=lambda item: item.period_end)
    return (
        annual.value + current.value - prior.value,
        tuple(dict.fromkeys((*_ids(annual), *_ids(current), *_ids(prior)))),
        (
            f"TTM ending {anchor.period_end} = annual ending {annual.period_end} "
            f"+ YTD ending {anchor.period_end} - comparable YTD ending {prior.period_end}."
        ),
    )


def _assess(
    periods: Sequence[CashConversionPeriodBridge],
    required_periods: int,
) -> CashConversionAssessment:
    gaps = tuple(dict.fromkeys(
        value.replace("_", " ")
        for period in periods for value in period.missing_components
    ))
    coverage_denominator = max(1, len(periods) * 6)
    coverage = sum(len(period.component_coverage) for period in periods) / coverage_denominator
    ratios = [
        period.operating_cash_flow_to_net_income
        for period in periods
        if period.operating_cash_flow_to_net_income is not None
    ]
    positive_fcf = all(
        period.free_cash_flow is not None and period.free_cash_flow > 0
        for period in periods
    ) if periods else False
    if len(periods) < required_periods:
        assessment = "insufficient_history"
    elif len(ratios) >= required_periods and all(value >= 0.9 for value in ratios) and positive_fcf:
        current = periods[0]
        assessment = (
            "recurring_working_capital_aided"
            if current.working_capital_change is not None
            and current.operating_cash_flow not in {None, 0}
            and current.working_capital_change > abs(current.operating_cash_flow) * 0.15
            else "recurring"
        )
    elif len(ratios) >= 2 and ratios[0] < ratios[1] * 0.85:
        assessment = "deteriorating"
    else:
        assessment = "mixed"
    confidence = (
        "High"
        if len(periods) >= required_periods and coverage >= 0.75 and not gaps
        else "Medium" if len(periods) >= required_periods
        else "Low"
    )
    readable = {
        "recurring": "Cash conversion appears recurring across the comparable TTM periods.",
        "recurring_working_capital_aided": (
            "Cash conversion remained strong, but the current period was materially aided by working capital."
        ),
        "mixed": "Cash conversion was mixed across the comparable TTM periods.",
        "deteriorating": "Cash conversion deteriorated versus the prior comparable TTM period.",
        "insufficient_history": "The available cutoff-safe history is insufficient to establish recurrence.",
    }[assessment]
    support = (
        "Comparable OCF/net-income conversion was "
        + " versus ".join(f"{value * 100:.1f}%" for value in ratios[:2])
        + "."
        if ratios else "No complete OCF/net-income comparison was available."
    )
    limitation = (
        "Missing component coverage: " + ", ".join(gaps) + "."
        if gaps else "No material component-coverage gap was identified."
    )
    return CashConversionAssessment(
        assessment=assessment,
        confidence=confidence,
        direct_answer=readable,
        strongest_support=support,
        strongest_limitation=limitation,
        comparison_periods=len(periods),
        coverage_ratio=round(min(1.0, coverage), 3),
        material_gaps=gaps,
    )


def _dedupe_observations(
    observations: Iterable[FinancialFactObservation],
) -> list[FinancialFactObservation]:
    by_id = {item.source_id: item for item in observations}
    return sorted(by_id.values(), key=lambda item: (
        item.normalized_metric, item.period_end, item.filed, item.source_id,
    ))


def _ids(item: FinancialFactObservation) -> tuple[str, ...]:
    return (item.evidence_id,) if item.evidence_id else ()


def _sum_available(values: Mapping[str, float | None], keys: Sequence[str]) -> float | None:
    selected = [float(values[key]) for key in keys if values.get(key) is not None]
    return sum(selected) if selected else None


def _is_annual(item: FinancialFactObservation) -> bool:
    return item.fiscal_period.casefold() == "fy" or (item.duration_days or 0) >= 300


def _approximately_one_year(first: str, second: str) -> bool:
    try:
        delta = (date.fromisoformat(second) - date.fromisoformat(first)).days
    except ValueError:
        return first[:4] != second[:4]
    return 350 <= delta <= 380


def _ttm_start(anchor: FinancialFactObservation) -> str:
    try:
        value = date.fromisoformat(anchor.period_end)
        return date(value.year - 1, value.month, value.day).isoformat()
    except (ValueError, TypeError):
        return ""


def _duration_days(start: str, end: str) -> int | None:
    if not start or not end:
        return None
    try:
        return (date.fromisoformat(end) - date.fromisoformat(start)).days
    except ValueError:
        return None


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _unit_is_eligible(unit: str) -> bool:
    normalized = str(unit or "").casefold()
    return normalized in {"usd", "shares", "pure"} or len(normalized) == 3


def _companyfacts_cik(company_facts: Mapping[str, Any]) -> str:
    value = str(company_facts.get("cik") or "").zfill(10)
    return "CIK" + value


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
        .encode("utf-8")
    ).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
