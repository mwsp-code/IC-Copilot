"""Canonical issuer, mechanism, lens, and analog-scope research contracts.

This module is intentionally free of presentation, persistence, provider, and
network dependencies.  Application code resolves one profile and then passes
that immutable context through the rest of the research workflow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any, Iterable


ONTOLOGY_SCHEMA_VERSION = "research-ontology-v1"
ANALOG_SCOPE_POLICY_VERSION = "analog-scope-v1"


class BusinessArchetype(str, Enum):
    ASSET_MANAGER = "asset_manager"
    CHINA_INTERNET_PLATFORM = "china_internet_platform"
    OPERATING_COMPANY = "operating_company"
    BANK_OR_BROKER = "bank_or_broker"
    INSURER = "insurer"
    REIT = "reit"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SecurityResearchProfile:
    profile_id: str
    stable_issuer_id: str
    ticker: str
    business_archetype: BusinessArchetype
    reporting_regime: str
    valuation_family: str
    credit_analysis_policy: str
    applicable_mechanisms: tuple[str, ...] = ()
    metric_aliases: tuple[tuple[str, str], ...] = ()
    required_financial_paths: tuple[str, ...] = ()
    required_source_families: tuple[str, ...] = ()
    resolution_source: str = "unknown"
    confidence: str = "Low"
    schema_version: str = ONTOLOGY_SCHEMA_VERSION


@dataclass(frozen=True)
class ResearchMechanism:
    mechanism_id: str
    label: str
    archetypes: tuple[BusinessArchetype, ...]
    causal_chain: tuple[str, ...]
    decision_outputs: tuple[str, ...]
    transfer_dimensions: tuple[str, ...] = ()


@dataclass(frozen=True)
class MetricMechanismDefinition:
    mechanism_id: str
    metric_key: str
    aliases: tuple[str, ...]
    driver_family: str
    interpretation: str
    required_evidence: tuple[str, ...]
    valuation_effects: tuple[str, ...]
    credit_effects: tuple[str, ...]
    falsification_tests: tuple[str, ...]
    archetypes: tuple[BusinessArchetype, ...]
    default_polarity: str = "neutral"


class LensStatus(str, Enum):
    SUPPORTED = "Supported"
    PARTIAL = "Partial"
    CONTRADICTED = "Contradicted"
    UNAVAILABLE = "Unavailable"
    NOT_MATERIAL = "Not material"


@dataclass(frozen=True)
class LensAssessment:
    lens_id: str
    status: LensStatus
    direct_conclusion: str
    observed_components: tuple[str, ...] = ()
    strongest_support: str = ""
    strongest_limitation: str = ""
    confidence: str = "Low"
    evidence_ids: tuple[str, ...] = ()
    next_action: str = ""


@dataclass(frozen=True)
class CausalBridgeAssessment:
    status: LensStatus
    mechanism_id: str
    direct_conclusion: str
    observed_components: tuple[str, ...] = ()
    strongest_support: str = ""
    strongest_limitation: str = ""
    confidence: str = "Low"
    evidence_ids: tuple[str, ...] = ()
    next_action: str = ""


@dataclass(frozen=True)
class LensInputSnapshot:
    ticker: str
    profile_id: str
    mechanism_ids: tuple[str, ...]
    structured_metric_names: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    corporate_credit_metric_names: tuple[str, ...] = ()


class AnalogResearchState(str, Enum):
    DISCOVERED_CANDIDATE = "discovered_candidate"
    SCOPE_REJECTED = "scope_rejected"
    RESEARCH_REQUESTED = "research_requested"
    EVIDENCE_RETRIEVED = "evidence_retrieved"
    INVESTIGATED_DRAFT = "investigated_draft"
    AUDIT_PENDING = "audit_pending"
    AUDITED_ANALOG = "audited_analog"
    DOWNGRADED_CANDIDATE = "downgraded_candidate"
    REJECTED = "rejected"


@dataclass(frozen=True)
class AnalogMatchDimensions:
    discovery_relevance: int = 0
    mechanism_fit: int = 0
    business_model_fit: int = 0
    regime_fit: int = 0
    source_relevance: int = 0
    provenance_independence: int = 0
    audited_similarity: int | None = None


@dataclass(frozen=True)
class AnalogEligibilityDecision:
    eligible: bool
    code: str
    summary: str
    current_mechanism_id: str = ""
    transfer_dimension: str = ""
    policy_version: str = ANALOG_SCOPE_POLICY_VERSION


LOW_INFORMATION_ANALOG_WORDS = frozenset({
    "management", "asset", "investment", "market", "revenue", "growth",
    "margin", "capital", "services", "rate",
})


RESEARCH_MECHANISMS: dict[str, ResearchMechanism] = {
    "aum_fee_earnings_conversion": ResearchMechanism(
        mechanism_id="aum_fee_earnings_conversion",
        label="AUM to fee earnings conversion",
        archetypes=(BusinessArchetype.ASSET_MANAGER,),
        causal_chain=(
            "Beginning AUM", "Net flows", "Market and FX effects",
            "Acquisitions and other changes", "Fee-earning AUM", "Fee rate and mix",
            "Revenue", "Expenses and margin", "Cash generation and per-share value",
        ),
        decision_outputs=("fee earnings", "normalized margin", "FCF", "per-share value"),
        transfer_dimensions=("flow durability", "fee realization", "operating leverage"),
    ),
    "organic_flow_generation": ResearchMechanism(
        "organic_flow_generation", "Organic flow generation",
        (BusinessArchetype.ASSET_MANAGER,),
        ("Client demand", "Net inflows", "Organic AUM growth", "Base-fee growth"),
        ("revenue growth", "earnings durability"), ("flow durability",),
    ),
    "fee_realization": ResearchMechanism(
        "fee_realization", "Fee realization",
        (BusinessArchetype.ASSET_MANAGER,),
        ("Fee-earning AUM", "Product mix", "Fee rate", "Base-fee revenue"),
        ("revenue", "margin"), ("fee mix",),
    ),
    "asset_manager_operating_leverage": ResearchMechanism(
        "asset_manager_operating_leverage", "Asset-manager operating leverage",
        (BusinessArchetype.ASSET_MANAGER,),
        ("Fee revenue", "Compensation and platform costs", "Operating margin", "Earnings"),
        ("earnings", "FCF"), ("operating leverage",),
    ),
    "per_share_capital_allocation": ResearchMechanism(
        "per_share_capital_allocation", "Per-share capital allocation",
        (BusinessArchetype.ASSET_MANAGER, BusinessArchetype.OPERATING_COMPANY,
         BusinessArchetype.CHINA_INTERNET_PLATFORM),
        ("Cash generation", "Dividends and repurchases", "Dilution", "Per-share value"),
        ("shares", "per-share value"), ("capital allocation",),
    ),
    "cloud_ai_investment_cycle": ResearchMechanism(
        "cloud_ai_investment_cycle", "Cloud and AI investment cycle",
        (BusinessArchetype.CHINA_INTERNET_PLATFORM, BusinessArchetype.OPERATING_COMPANY),
        ("Cloud and AI investment", "Capacity and utilization", "Demand and monetization",
         "Segment revenue and EBITA", "Capex and depreciation", "Free cash flow"),
        ("cloud revenue", "segment EBITA", "FCF"),
        ("capacity utilization", "cloud monetization", "capex-to-FCF conversion"),
    ),
    "accelerated_compute_capacity_cycle": ResearchMechanism(
        "accelerated_compute_capacity_cycle", "Accelerated-compute capacity cycle",
        (BusinessArchetype.OPERATING_COMPANY,),
        ("Demand and backlog", "Supplier commitments and inventory", "Capacity",
         "Utilization and product mix", "Revenue and margin", "Cash conversion"),
        ("revenue conversion", "margin", "FCF"),
        ("commitment conversion", "capacity utilization", "inventory risk"),
    ),
    "acquisition_integration": ResearchMechanism(
        "acquisition_integration", "Acquisition and integration economics",
        (BusinessArchetype.OPERATING_COMPANY, BusinessArchetype.ASSET_MANAGER,
         BusinessArchetype.CHINA_INTERNET_PLATFORM),
        ("Purchase consideration", "Goodwill and acquired intangibles", "Integration",
         "Revenue and cost synergies", "Impairment risk", "Per-share value"),
        ("earnings", "FCF", "per-share value"),
        ("integration", "synergy realization", "impairment risk"),
    ),
    "revenue_demand_conversion": ResearchMechanism(
        "revenue_demand_conversion", "Revenue and demand conversion",
        (BusinessArchetype.OPERATING_COMPANY,),
        ("Demand", "Volume, price, and mix", "Revenue", "Margin", "Cash generation"),
        ("revenue", "margin", "FCF"), ("demand conversion",),
    ),
    "operating_margin_conversion": ResearchMechanism(
        "operating_margin_conversion", "Operating-margin conversion",
        (BusinessArchetype.OPERATING_COMPANY,),
        ("Price, volume, and mix", "Cost of revenue", "Gross margin", "Operating costs",
         "Operating margin", "Cash generation"),
        ("margin", "earnings", "FCF"), ("margin durability",),
    ),
    "operating_cash_conversion": ResearchMechanism(
        "operating_cash_conversion", "Operating cash conversion",
        (BusinessArchetype.OPERATING_COMPANY,),
        ("Net income", "Non-cash adjustments", "Working capital", "Operating cash flow",
         "Capex", "Free cash flow", "Capital returns"),
        ("FCF", "liquidity", "per-share value"), ("cash conversion",),
    ),
    "balance_sheet_financing": ResearchMechanism(
        "balance_sheet_financing", "Balance-sheet financing",
        (BusinessArchetype.OPERATING_COMPANY,),
        ("Operating cash", "Debt issuance and repayment", "Liquidity", "Coverage",
         "Capital returns"),
        ("liquidity", "cost of capital", "per-share value"), ("financing resilience",),
    ),
}


METRIC_MECHANISMS: tuple[MetricMechanismDefinition, ...] = (
    MetricMechanismDefinition(
        "aum_fee_earnings_conversion", "Assets Under Management",
        ("assets under management", "aum", "ending aum", "fee earning aum"),
        "aum_fee_earnings_conversion", "AUM, flows, market/FX, and fee-rate bridge",
        ("Beginning and ending AUM", "Net flows", "Market/FX effects", "Fee rate and revenue mix"),
        ("fee earnings", "normalized margin", "per-share value"),
        ("corporate liquidity", "acquisition funding"),
        ("AUM growth does not convert into organic base-fee growth or earnings."),
        (BusinessArchetype.ASSET_MANAGER,),
    ),
    MetricMechanismDefinition(
        "organic_flow_generation", "Net Inflows", ("net inflows", "net flows", "organic flows"),
        "organic_flow_generation", "Client demand to organic AUM and fee growth",
        ("Long-term net flows", "Organic AUM growth", "Product and channel mix"),
        ("organic fee growth",), ("liquidity stability",),
        ("Reported AUM growth is primarily market appreciation rather than client flows."),
        (BusinessArchetype.ASSET_MANAGER,),
    ),
    MetricMechanismDefinition(
        "fee_realization", "Organic Base Fee Growth",
        ("organic base fee growth", "base fee growth", "fee rate", "fee realization"),
        "fee_realization", "Fee-earning AUM and mix to base-fee revenue",
        ("Fee-earning AUM", "Organic base fees", "Product mix and effective fee rate"),
        ("revenue", "margin"), (),
        ("Mix shift or fee compression prevents AUM growth from converting to revenue."),
        (BusinessArchetype.ASSET_MANAGER,),
    ),
    MetricMechanismDefinition(
        "asset_manager_operating_leverage", "Operating Margin",
        ("operating margin", "adjusted operating margin", "operating income"),
        "asset_manager_operating_leverage", "Fee revenue to expenses and margin",
        ("Revenue", "Compensation and platform expenses", "Adjusted operating margin"),
        ("earnings", "FCF"), ("interest coverage",),
        ("Expense growth absorbs organic fee growth."),
        (BusinessArchetype.ASSET_MANAGER,),
    ),
    MetricMechanismDefinition(
        "per_share_capital_allocation", "Shares and Repurchases",
        ("diluted shares", "share count", "repurchases", "share repurchases"),
        "per_share_capital_allocation", "Cash returns and dilution to per-share value",
        ("Repurchases", "Dividends", "Diluted share count"),
        ("per-share value",), ("cash uses",),
        ("Repurchases fail to offset dilution or destroy value at excessive prices."),
        (BusinessArchetype.ASSET_MANAGER, BusinessArchetype.OPERATING_COMPANY,
         BusinessArchetype.CHINA_INTERNET_PLATFORM),
    ),
    MetricMechanismDefinition(
        "cloud_ai_investment_cycle", "Cloud and AI Investment",
        ("cloud and ai investment", "cloud / ai investment", "cloud investment",
         "ai investment", "cloud capex", "cloud revenue", "cloud segment revenue",
         "cloud mix", "cloud backlog", "data center capex", "data center capacity"),
        "cloud_ai_investment_cycle", "Capacity and utilization to cloud monetization and FCF",
        ("Cloud capex", "Capacity and utilization", "Cloud revenue", "Segment EBITA", "Depreciation", "FCF"),
        ("cloud revenue", "segment EBITA", "FCF"), ("liquidity consumption",),
        ("Utilization, monetization, or segment returns fail to cover the investment cycle."),
        (BusinessArchetype.CHINA_INTERNET_PLATFORM, BusinessArchetype.OPERATING_COMPANY),
    ),
    MetricMechanismDefinition(
        "accelerated_compute_capacity_cycle", "Capital Expenditure",
        ("capital expenditure", "capital expenditures", "capex", "supplier commitment",
         "purchase obligation", "inventory"),
        "accelerated_compute_capacity_cycle",
        "Demand and commitments to capacity, utilization, margin, and cash conversion",
        ("Demand or backlog", "Supplier commitments", "Inventory", "Capacity and utilization",
         "Revenue", "Gross margin", "FCF"),
        ("revenue conversion", "margin", "FCF"), ("commitments", "liquidity consumption"),
        ("Capacity, commitments, or inventory grow without durable demand conversion."),
        (BusinessArchetype.OPERATING_COMPANY,),
    ),
    MetricMechanismDefinition(
        "acquisition_integration", "Goodwill",
        ("goodwill", "acquired intangible", "business combination", "acquisition"),
        "acquisition_integration",
        "Acquisition consideration and goodwill to integration, cash returns, and impairment risk",
        ("Purchase consideration", "Goodwill", "Acquired intangibles", "Synergies", "Impairment tests"),
        ("earnings", "FCF", "per-share value"), ("acquisition funding",),
        ("Expected synergies fail to materialize or the acquired assets are impaired."),
        (BusinessArchetype.OPERATING_COMPANY, BusinessArchetype.ASSET_MANAGER,
         BusinessArchetype.CHINA_INTERNET_PLATFORM),
    ),
    MetricMechanismDefinition(
        "revenue_demand_conversion", "Revenue growth / demand",
        ("revenue", "sales", "bookings", "backlog"), "revenue_demand_conversion",
        "Demand, price, volume, and mix to revenue and cash conversion",
        ("Revenue", "Volume", "Price or ASP", "Mix", "Margin", "FCF"),
        ("revenue", "margin", "FCF"), ("coverage",),
        ("Reported growth does not persist or convert into margin and cash generation."),
        (BusinessArchetype.OPERATING_COMPANY,),
    ),
    MetricMechanismDefinition(
        "operating_margin_conversion", "Gross margin / mix",
        ("gross margin", "gross profit", "cost of revenue", "operating margin",
         "operating income"), "operating_margin_conversion",
        "Price, volume, mix, and costs to operating margin and cash earnings",
        ("Revenue mix", "Cost of revenue", "Gross margin", "Operating expenses", "Operating margin"),
        ("margin", "earnings", "FCF"), ("coverage",),
        ("Temporary mix or cost benefits fail to persist in operating cash generation."),
        (BusinessArchetype.OPERATING_COMPANY,),
    ),
    MetricMechanismDefinition(
        "operating_cash_conversion", "Cash generation / liquidity",
        ("operating cash flow", "cash from operations", "free cash flow", "cash conversion"),
        "operating_cash_conversion",
        "Net income and working capital to operating cash, reinvestment, and capital returns",
        ("Net income", "Non-cash adjustments", "Working capital", "Operating cash flow",
         "Capex", "Dividends", "Repurchases"),
        ("FCF", "per-share value"), ("liquidity", "debt coverage"),
        ("Cash conversion weakens after working capital, capex, taxes, financing, and returns."),
        (BusinessArchetype.OPERATING_COMPANY,),
    ),
    MetricMechanismDefinition(
        "balance_sheet_financing", "Balance sheet / liquidity",
        ("long term debt", "current debt", "debt issued", "debt repaid", "liquidity"),
        "balance_sheet_financing",
        "Operating cash and financing flows to corporate liquidity and coverage",
        ("Corporate cash", "Debt", "Maturities", "Interest", "Operating cash flow"),
        ("cost of capital", "per-share value"), ("liquidity", "coverage"),
        ("Financing needs or capital returns weaken corporate liquidity and coverage."),
        (BusinessArchetype.OPERATING_COMPANY,),
    ),
)


class SecurityResearchProfileResolver:
    """Resolve an issuer without silently defaulting unknown issuers."""

    _CIK_PROFILES = {
        "320193": BusinessArchetype.OPERATING_COMPANY,
        "1045810": BusinessArchetype.OPERATING_COMPANY,
        "1341439": BusinessArchetype.OPERATING_COMPANY,
        "1318605": BusinessArchetype.OPERATING_COMPANY,
        "2012383": BusinessArchetype.ASSET_MANAGER,
        "1577552": BusinessArchetype.CHINA_INTERNET_PLATFORM,
        "886982": BusinessArchetype.BANK_OR_BROKER,
    }
    _TICKER_PROFILES = {
        "AAPL": BusinessArchetype.OPERATING_COMPANY,
        "NVDA": BusinessArchetype.OPERATING_COMPANY,
        "ORCL": BusinessArchetype.OPERATING_COMPANY,
        "TSLA": BusinessArchetype.OPERATING_COMPANY,
        "GS": BusinessArchetype.BANK_OR_BROKER,
        "BLK": BusinessArchetype.ASSET_MANAGER,
        "BABA": BusinessArchetype.CHINA_INTERNET_PLATFORM,
    }

    def resolve(self, identity: Any) -> SecurityResearchProfile:
        ticker = str(getattr(identity, "ticker", "") or "").upper()
        cik = str(getattr(identity, "cik", "") or "").lstrip("0")
        name = str(getattr(identity, "name", "") or "")
        sic = str(getattr(identity, "sic", "") or "")
        description = str(getattr(identity, "sic_description", "") or "")
        haystack = f"{name} {description}".casefold()
        archetype = self._CIK_PROFILES.get(cik) or self._TICKER_PROFILES.get(ticker)
        source = "validated_issuer_registry" if archetype else ""
        confidence = "High" if archetype else "Low"
        if archetype is None and (
            sic in {"6211", "6282"}
            or any(phrase in haystack for phrase in ("asset management", "investment management"))
        ):
            archetype = BusinessArchetype.ASSET_MANAGER
            source = "sec_sic_and_business_description"
            confidence = "Medium"
        if archetype is None:
            archetype = BusinessArchetype.UNKNOWN
            source = "unresolved"
        stable = f"cik:{cik}" if cik else f"exchange:{getattr(identity, 'exchange', 'US')}:{ticker}"
        return _profile_for(archetype, ticker, stable, source, confidence)


def _profile_for(
    archetype: BusinessArchetype,
    ticker: str,
    stable_id: str,
    source: str,
    confidence: str,
) -> SecurityResearchProfile:
    if archetype == BusinessArchetype.ASSET_MANAGER:
        return SecurityResearchProfile(
            "asset-manager-v1", stable_id, ticker, archetype, "us_gaap",
            "asset_manager_fee_earnings", "corporate_only_exclude_client_fund_balances",
            ("aum_fee_earnings_conversion", "organic_flow_generation", "fee_realization",
             "asset_manager_operating_leverage", "per_share_capital_allocation"),
            tuple((item.aliases[0], item.mechanism_id) for item in METRIC_MECHANISMS
                  if archetype in item.archetypes),
            ("fee_earning_aum", "net_flows", "base_fee_growth", "operating_margin",
             "corporate_cash", "corporate_debt", "shares"),
            ("sec_filing", "issuer_ir_report", "earnings_transcript", "market_data"),
            source, confidence,
        )
    if archetype == BusinessArchetype.CHINA_INTERNET_PLATFORM:
        return SecurityResearchProfile(
            "china-internet-platform-v1", stable_id, ticker, archetype, "ifrs_fpi_adr",
            "segment_sotp_and_fcf", "corporate_and_vie_scoped",
            ("cloud_ai_investment_cycle", "per_share_capital_allocation"), (),
            ("segment_revenue", "segment_ebita", "capex", "fcf", "corporate_cash",
             "corporate_debt", "shares"),
            ("foreign_issuer_filing", "issuer_ir_report", "market_data", "regulator"),
            source, confidence,
        )
    if archetype == BusinessArchetype.OPERATING_COMPANY:
        return SecurityResearchProfile(
            "operating-company-v1", stable_id, ticker, archetype, "us_gaap",
            "operating_fcf_dcf", "corporate_balance_sheet",
            ("revenue_demand_conversion", "operating_margin_conversion",
             "operating_cash_conversion", "balance_sheet_financing",
             "accelerated_compute_capacity_cycle", "acquisition_integration",
             "cloud_ai_investment_cycle", "per_share_capital_allocation"), (),
            ("revenue", "margin", "operating_cash_flow", "capex", "corporate_cash",
             "corporate_debt", "shares"),
            ("sec_filing", "issuer_ir_report", "earnings_transcript", "market_data"),
            source, confidence,
        )
    return SecurityResearchProfile(
        f"{archetype.value}-v1", stable_id, ticker, archetype, "unknown",
        "unresolved", "unresolved", (), (), (), (), source, confidence,
    )


def resolve_metric_mechanism(
    profile: SecurityResearchProfile,
    *,
    metric_name: str = "",
    title: str = "",
    category: str = "",
    economic_driver: str = "",
    explicit_family: str = "",
) -> MetricMechanismDefinition | None:
    fields = tuple(
        _normalize_text(item)
        for item in (metric_name, title, category, economic_driver)
        if str(item or "").strip()
    )
    explicit = str(explicit_family or "").strip()
    if explicit and explicit not in {"unmapped", "disclosure"}:
        direct = next((item for item in METRIC_MECHANISMS if item.mechanism_id == explicit), None)
        if direct and profile.business_archetype in direct.archetypes:
            return direct
    for definition in METRIC_MECHANISMS:
        if profile.business_archetype not in definition.archetypes:
            continue
        aliases = tuple(_normalize_text(alias) for alias in definition.aliases)
        if any(alias and _contains_ordered_phrase(field, alias) for field in fields for alias in aliases):
            return definition
    return None


class LensInputAssembler:
    """Build one typed snapshot from admitted structured inputs."""

    _NON_CORPORATE_CREDIT_TERMS = (
        "client", "fund", "custodial", "assets under management", "aum",
        "consolidated vie", "non-recourse",
    )

    def assemble(
        self,
        profile: SecurityResearchProfile,
        metrics: Iterable[Any],
        events: Iterable[Any],
    ) -> LensInputSnapshot:
        metric_names = tuple(dict.fromkeys(
            str(getattr(item, "name", "") or "") for item in metrics
            if str(getattr(item, "name", "") or "").strip()
        ))
        mechanisms: list[str] = []
        evidence_ids: list[str] = []
        for event in events:
            payload = getattr(event, "metrics", {}) or {}
            mechanism = str(payload.get("research_mechanism_id") or payload.get("driver_family") or "")
            if mechanism in RESEARCH_MECHANISMS and mechanism not in mechanisms:
                mechanisms.append(mechanism)
            for citation in list(getattr(event, "citations", []) or []):
                identifier = str(getattr(citation, "accession", "") or getattr(citation, "url", "") or "")
                if identifier and identifier not in evidence_ids:
                    evidence_ids.append(identifier)
        corporate_credit = tuple(
            name for name in metric_names
            if not any(term in name.casefold() for term in self._NON_CORPORATE_CREDIT_TERMS)
        )
        return LensInputSnapshot(
            ticker=profile.ticker,
            profile_id=profile.profile_id,
            mechanism_ids=tuple(mechanisms),
            structured_metric_names=metric_names,
            evidence_ids=tuple(evidence_ids),
            corporate_credit_metric_names=corporate_credit,
        )


def build_research_assessments(
    profile: SecurityResearchProfile,
    metrics: Iterable[Any],
    events: Iterable[Any],
    *,
    one_pager: Any | None = None,
    credit_lens: Any | None = None,
) -> tuple[CausalBridgeAssessment, LensAssessment, LensAssessment]:
    """Build truthful, typed lens summaries from the same evidence snapshot."""

    event_rows = list(events)
    snapshot = LensInputAssembler().assemble(profile, metrics, event_rows)
    mechanism_id = snapshot.mechanism_ids[0] if snapshot.mechanism_ids else ""
    mechanism = RESEARCH_MECHANISMS.get(mechanism_id)
    metric_names = {item.casefold() for item in snapshot.structured_metric_names}
    if mechanism is None:
        causal = CausalBridgeAssessment(
            LensStatus.UNAVAILABLE,
            "",
            "No canonical operating or accounting mechanism has been mapped.",
            strongest_limitation="Map the core signal before directional interpretation.",
            evidence_ids=snapshot.evidence_ids,
            next_action="Define and validate the core research mechanism.",
        )
    else:
        component_hits = tuple(
            component for component in mechanism.causal_chain
            if any(token in component.casefold() for name in metric_names for token in name.split())
        )
        causal = CausalBridgeAssessment(
            LensStatus.PARTIAL,
            mechanism.mechanism_id,
            f"Mapped to {mechanism.label}; the remaining bridge components must be reconciled before directional use.",
            observed_components=component_hits or (mechanism.causal_chain[0],),
            strongest_support=(
                f"The current evidence is explicitly scoped to {mechanism.label}."
            ),
            strongest_limitation=_mechanism_limitation(profile, mechanism_id),
            confidence="Medium" if snapshot.evidence_ids else "Low",
            evidence_ids=snapshot.evidence_ids,
            next_action=_mechanism_next_action(profile, mechanism_id),
        )

    if profile.business_archetype == BusinessArchetype.ASSET_MANAGER:
        equity = LensAssessment(
            "equity",
            LensStatus.PARTIAL if mechanism_id else LensStatus.UNAVAILABLE,
            (
                "Separate organic flows from market/FX AUM effects, then test fee realization, "
                "margin conversion, dilution, and per-share value."
            ),
            observed_components=tuple(
                item for item in (
                    "AUM", "organic flows", "fee realization", "operating margin", "per-share value"
                ) if any(item.casefold().replace("aum", "assets under management") in name
                         or item.casefold() in name for name in metric_names)
            ),
            strongest_support=(
                f"{mechanism.label} is mapped from the current issuer evidence."
                if mechanism else "No asset-manager mechanism is currently mapped."
            ),
            strongest_limitation=(
                "AUM alone cannot distinguish organic flows, market appreciation, acquisitions, or fee-rate mix."
            ),
            confidence="Medium" if mechanism else "Low",
            evidence_ids=snapshot.evidence_ids,
            next_action=(
                "Reconcile beginning AUM, net flows, market/FX, acquisitions, base fees, margin, and diluted shares."
            ),
        )
        credit_names = {item.casefold() for item in snapshot.corporate_credit_metric_names}
        corporate_inputs = tuple(
            item for item in snapshot.corporate_credit_metric_names
            if any(term in item.casefold() for term in ("cash", "debt", "interest", "matur", "commit", "operating cash"))
        )
        credit_status = LensStatus.PARTIAL if corporate_inputs else LensStatus.UNAVAILABLE
        credit = LensAssessment(
            "credit",
            credit_status,
            (
                "Corporate liquidity and obligations are assessed separately from client, fund, "
                "custodial, consolidated-VIE, and non-recourse balances."
            ),
            observed_components=corporate_inputs,
            strongest_support=(
                "Corporate cash or debt evidence is available."
                if corporate_inputs else "No eligible corporate credit metric is available in this snapshot."
            ),
            strongest_limitation=(
                "Corporate cash, debt maturities, interest coverage, commitments, and acquisition funding are incomplete."
                if credit_status == LensStatus.PARTIAL else
                "Client and fund balances cannot substitute for corporate liquidity and debt evidence."
            ),
            confidence="Low",
            evidence_ids=tuple(snapshot.evidence_ids if corporate_inputs else ()),
            next_action=(
                "Retrieve corporate cash, debt maturities, interest coverage, commitments, acquisition funding, and capital returns."
            ),
        )
        return causal, equity, credit

    pager_equity = str(getattr(one_pager, "equity_lens", "") or "")
    pager_credit = str(getattr(one_pager, "credit_lens", "") or "")
    credit_available = str(getattr(credit_lens, "status", "") or "").casefold() not in {"", "unavailable"}
    equity = LensAssessment(
        "equity", LensStatus.PARTIAL if pager_equity else LensStatus.UNAVAILABLE,
        pager_equity or "Equity implications are not established from the current evidence.",
        strongest_limitation="Complete the mechanism-to-earnings, cash-flow, valuation, and per-share bridge.",
        evidence_ids=snapshot.evidence_ids,
        next_action="Complete the archetype-specific equity bridge.",
    )
    credit = LensAssessment(
        "credit", LensStatus.PARTIAL if credit_available else LensStatus.UNAVAILABLE,
        pager_credit or "Credit implications are not established from the current evidence.",
        strongest_limitation=(
            "The current credit lens contains unresolved structured-data gaps."
            if credit_available else "No decision-useful credit evidence is available."
        ),
        evidence_ids=snapshot.evidence_ids if credit_available else (),
        next_action="Retrieve the archetype-appropriate liquidity, obligation, and coverage evidence.",
    )
    return causal, equity, credit


def _mechanism_limitation(profile: SecurityResearchProfile, mechanism_id: str) -> str:
    if profile.business_archetype == BusinessArchetype.ASSET_MANAGER:
        return "Beginning AUM, flows, market/FX, acquisitions, fee realization, and margin are not fully reconciled."
    if mechanism_id == "cloud_ai_investment_cycle":
        return "Capacity, utilization, segment monetization, depreciation, and FCF are not fully reconciled."
    return "The complete operating and accounting bridge is not yet reconciled."


def _mechanism_next_action(profile: SecurityResearchProfile, mechanism_id: str) -> str:
    if profile.business_archetype == BusinessArchetype.ASSET_MANAGER:
        return "Complete the AUM-to-fee-earnings and per-share value bridge."
    if mechanism_id == "cloud_ai_investment_cycle":
        return "Test cloud utilization, monetization, segment EBITA, depreciation, capex intensity, and FCF."
    return "Complete the canonical mechanism bridge with admitted issuer evidence."


class AnalogScopePolicy:
    """Evaluate hard scope eligibility before any discovery ranking."""

    def evaluate(
        self,
        *,
        current_profile: SecurityResearchProfile,
        current_mechanism_id: str,
        current_ticker: str,
        reference_tickers: Iterable[str],
        transfer_role: str,
        allowed_archetypes: Iterable[str] = (),
        episode_mechanisms: Iterable[str] = (),
        transfer_dimension: str = "",
        registered_peers: Iterable[str] = (),
    ) -> AnalogEligibilityDecision:
        references = {str(item).upper() for item in reference_tickers if str(item).strip()}
        role = str(transfer_role or "")
        if role.startswith("same_issuer"):
            if current_ticker.upper() not in references:
                return AnalogEligibilityDecision(False, "issuer_scope_violation", "Same-issuer history belongs to another issuer.", current_mechanism_id)
            return AnalogEligibilityDecision(True, "same_issuer", "Same stable issuer identity.", current_mechanism_id, "issuer history")
        if current_profile.business_archetype == BusinessArchetype.UNKNOWN:
            return AnalogEligibilityDecision(False, "security_profile_unresolved", "The current issuer archetype is unresolved.", current_mechanism_id)
        if not current_mechanism_id:
            return AnalogEligibilityDecision(False, "core_mechanism_unresolved", "No canonical current mechanism is available for transfer testing.")
        if role == "peer_cycle" and not (references & {str(item).upper() for item in registered_peers}):
            return AnalogEligibilityDecision(False, "peer_scope_violation", "The episode is not a registered peer cycle.", current_mechanism_id)
        allowed = {str(item) for item in allowed_archetypes if str(item)}
        if role.startswith("same_industry") and current_profile.business_archetype.value not in allowed:
            return AnalogEligibilityDecision(False, "archetype_scope_violation", "The episode business archetype is incompatible.", current_mechanism_id)
        mechanisms = {str(item) for item in episode_mechanisms if str(item)}
        if current_mechanism_id not in mechanisms:
            return AnalogEligibilityDecision(False, "mechanism_scope_violation", "No shared canonical mechanism was established.", current_mechanism_id)
        if role.startswith("cross_industry") or role in {
            "industry_boundary_case", "business_model_counterexample",
            "adjacent_demand_capacity_cycle", "adjacent_product_cycle",
        }:
            if not transfer_dimension:
                return AnalogEligibilityDecision(False, "transfer_dimension_missing", "Cross-company transfer dimension is not defined.", current_mechanism_id)
        return AnalogEligibilityDecision(True, "mechanism_eligible", "Canonical mechanism and transfer dimension are compatible.", current_mechanism_id, transfer_dimension)


def _contains_ordered_phrase(text: str, phrase: str, max_window: int = 2) -> bool:
    """Match a phrase in order inside one field; never as an unordered token set."""

    text_tokens = _tokens(text)
    phrase_tokens = _tokens(phrase)
    if not text_tokens or not phrase_tokens:
        return False
    if len(phrase_tokens) == 1:
        return phrase_tokens[0] in text_tokens
    for start, token in enumerate(text_tokens):
        if token != phrase_tokens[0]:
            continue
        cursor = start
        matched = True
        for wanted in phrase_tokens[1:]:
            upper = min(len(text_tokens), cursor + max_window + 2)
            try:
                cursor = next(index for index in range(cursor + 1, upper) if text_tokens[index] == wanted)
            except StopIteration:
                matched = False
                break
        if matched:
            return True
    return False


def phrase_matches_field(text: str, phrase: str) -> bool:
    normalized_phrase = _normalize_text(phrase)
    phrase_tokens = _tokens(normalized_phrase)
    if len(phrase_tokens) == 1 and phrase_tokens[0] in LOW_INFORMATION_ANALOG_WORDS:
        return False
    # Compound concepts made entirely from low-information words (for example
    # ``asset management``) must be contiguous.  Otherwise text such as
    # ``asset-light ... customer management`` manufactures a false concept.
    max_window = 0 if phrase_tokens and all(
        token in LOW_INFORMATION_ANALOG_WORDS for token in phrase_tokens
    ) else 2
    return _contains_ordered_phrase(
        _normalize_text(text), normalized_phrase, max_window=max_window
    )


def _normalize_text(value: str) -> str:
    return " ".join(_tokens(str(value or "")))


def _tokens(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(value or "").casefold())
