from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from datetime import datetime, timezone
from typing import Any, Iterable, Protocol

from ..auditable_retrieval import (
    semantic_evidence_hash,
    source_record_to_evidence,
)
from ..domain.agent_research import (
    ResearchObservation,
    ResearchToolCall,
)
from ..domain.financial_facts import FinancialFactSeriesQuery
from ..evidence_inputs import (
    evidence_catalog_metadata,
    evidence_is_claim_eligible,
    search_evidence_catalog,
)
from ..market_implied import build_market_implied_expectations
from ..source_planner import SOURCE_REGISTRY_VERSION
from ..secret_safety import redact_sensitive_text, redact_sensitive_value
from .source_capabilities import SourceCapabilityRegistry
from .cash_conversion import (
    CashConversionRecurrenceService,
    SecCompanyFactsFinancialFactSeries,
    admitted_series,
    cash_conversion_intent_id,
    normalize_financial_fact_metric,
)


AGENT_RESEARCH_TOOL_VERSION = "agent-research-tools-v1"


class ResearchToolGateway(Protocol):
    def describe_tools(self) -> list[dict[str, Any]]: ...

    def execute(self, call: ResearchToolCall) -> ResearchObservation: ...


class ContextResearchToolGateway:
    """Application-owned tools over the run's normalized research context.

    This boundary deliberately owns provider selection, retrieval, admission,
    and calculations. Models receive compact observations and admitted IDs;
    they never receive credentials or connector objects.
    """

    def __init__(
        self,
        context: dict[str, Any],
        capabilities: SourceCapabilityRegistry,
        *,
        retrieval_engine: Any | None = None,
    ) -> None:
        self.context = context
        self.capabilities = capabilities
        self.retrieval_engine = (
            retrieval_engine
            if retrieval_engine is not None
            else context.get("_retrieval_engine")
        )
        self._notes: list[dict[str, Any]] = []
        self._submitted_answers: dict[str, dict[str, Any]] = {}

    def describe_tools(self) -> list[dict[str, Any]]:
        return [
            _tool("list_source_capabilities", "List secret-free source capabilities.", {}),
            _tool("search_sources", "Search one or more registered source families.", {
                "query": "string",
                "source_families": "list[string]",
                "period": "string (optional)",
                "result_limit": "integer (optional)",
                "confirming_evidence": "list[string] (optional)",
                "contradicting_evidence": "list[string] (optional)",
            }),
            _tool("open_source", "Open an admitted or normalized source by ID.", {
                "source_id": "string",
            }),
            _tool("find_in_source", "Find terms in a retrieved source excerpt.", {
                "source_id": "string",
                "terms": "list[string]",
            }),
            _tool("extract_table", "Extract structured facts from a source record.", {
                "source_id": "string",
                "fields": "list[string] (optional)",
            }),
            _tool("query_sec_facts", "Query period-aware SEC/XBRL financial facts.", {
                "metrics": "list[string]",
                "period": "string (optional)",
            }),
            _tool("query_sec_fact_series", "Query the full cutoff-safe SEC financial-fact history.", {
                "metrics": "list[string] (optional)",
                "data_cutoff": "ISO date (optional)",
                "comparison_periods": "integer (optional)",
            }),
            _tool("query_market_data", "Read normalized price and market data.", {
                "period": "string (optional)",
                "tickers": "list[string] (optional)",
            }),
            _tool("query_consensus_history", "Read dated consensus and revision history.", {
                "metric": "string (optional)",
                "period": "string (optional)",
            }),
            _tool("query_macro_series", "Read material normalized macro observations.", {
                "series": "list[string] (optional)",
                "period": "string (optional)",
            }),
            _tool("compare_periods", "Compare normalized observations by period.", {
                "metric": "string",
                "periods": "list[string] (optional)",
            }),
            _tool("compute_financial_bridge", "Compute a reproducible financial bridge.", {
                "bridge_kind": "cash_conversion|cash_conversion_recurrence|margin|liquidity",
                "period": "string (optional)",
            }),
            _tool("compute_reverse_valuation", "Read or recompute market-implied expectations.", {
                "assumption_overrides": "object (optional)",
            }),
            _tool("compute_event_attribution", "Read reproducible event-reaction attribution.", {
                "event_id": "string (optional)",
                "event_date": "string (optional)",
            }),
            _tool("propose_evidence", "Propose already-admitted IDs for a claim; creates no evidence.", {
                "claim": "string",
                "evidence_ids": "list[string]",
                "contrary_evidence_ids": "list[string] (optional)",
            }),
            _tool("record_research_note", "Record a provisional notebook note.", {
                "entry_type": "hypothesis|lead|interpretation|rejection|next_search",
                "summary": "string",
                "observation_ids": "list[string] (optional)",
            }),
            _tool("submit_research_answer", "Submit a calibrated answer using admitted evidence.", {
                "answer": "string",
                "status": "answered|partially_answered|blocked_by_source_access|blocked_by_provider_failure|cancelled",
                "confidence": "High|Medium|Low|Unknown",
                "evidence_ids": "list[string]",
                "contrary_evidence_ids": "list[string] (optional)",
                "remaining_work": "list[string] (optional)",
            }),
        ]

    def execute(self, call: ResearchToolCall) -> ResearchObservation:
        handlers = {
            "list_source_capabilities": self._list_source_capabilities,
            "search_sources": self._search_sources,
            "open_source": self._open_source,
            "find_in_source": self._find_in_source,
            "extract_table": self._extract_table,
            "query_sec_facts": self._query_sec_facts,
            "query_sec_fact_series": self._query_sec_fact_series,
            "query_market_data": self._query_market_data,
            "query_consensus_history": self._query_consensus_history,
            "query_macro_series": self._query_macro_series,
            "compare_periods": self._compare_periods,
            "compute_financial_bridge": self._compute_financial_bridge,
            "compute_reverse_valuation": self._compute_reverse_valuation,
            "compute_event_attribution": self._compute_event_attribution,
            "propose_evidence": self._propose_evidence,
            "record_research_note": self._record_research_note,
            "submit_research_answer": self._submit_research_answer,
        }
        handler = handlers.get(call.tool_name)
        if handler is None:
            return self._observation(
                call,
                status="failed",
                summary=f"Unknown research tool: {call.tool_name}.",
                failure_code="unknown_tool",
            )
        try:
            return handler(call)
        except Exception as exc:
            return self._observation(
                call,
                status="failed",
                summary=(
                    "The research tool could not complete: "
                    f"{_safe_text(redact_sensitive_text(exc), 320)}"
                ),
                failure_code="tool_execution_failed",
            )

    def submitted_answer(self, question_id: str) -> dict[str, Any] | None:
        value = self._submitted_answers.get(question_id)
        return dict(value) if value else None

    @property
    def notes(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(item) for item in self._notes)

    def _list_source_capabilities(self, call: ResearchToolCall) -> ResearchObservation:
        content = self.capabilities.prompt_catalog()
        ready = sum(
            item.get("status") in {"ready", "cached_only"}
            for item in content
        )
        return self._observation(
            call,
            status="completed",
            summary=f"{ready} of {len(content)} source capabilities are usable for this run.",
            content=content,
        )

    def _search_sources(self, call: ResearchToolCall) -> ResearchObservation:
        arguments = call.arguments
        query = str(arguments.get("query") or call.objective or "").strip()
        families = _string_list(
            arguments.get("source_families")
            or arguments.get("required_source_families")
        )
        if not families:
            families = _string_list(
                capability.source_families
                for capability in self.capabilities.available()
            )[:6]
        period = str(arguments.get("period") or "")
        limit = max(1, min(20, _integer(arguments.get("result_limit"), 8)))
        confirming = _string_list(arguments.get("confirming_evidence"))
        contradicting = _string_list(arguments.get("contradicting_evidence"))
        complete_query = " ".join(
            value for value in (
                query,
                "Confirming evidence: " + "; ".join(confirming) if confirming else "",
                "Contradicting evidence: " + "; ".join(contradicting) if contradicting else "",
            ) if value
        )
        all_records: list[Any] = []
        attempts: list[dict[str, Any]] = []
        unavailable_families: list[str] = []
        for index, family in enumerate(dict.fromkeys(families)):
            family_capabilities = self.capabilities.for_source_family(family)
            eligible = [
                item for item in family_capabilities
                if item.status in {"ready", "cached_only"}
            ]
            if not eligible:
                unavailable_families.append(family)
            records: list[Any] = []
            lifecycle: Any | None = None
            if self.retrieval_engine is not None:
                records, lifecycle = self.retrieval_engine.retrieve(
                    request_id=f"{call.call_id}:{index}",
                    source_type=family,
                    query=complete_query,
                    period=period,
                    result_limit=limit,
                )
                all_records.extend(records)
                attempts.append({
                    "source_family": family,
                    "capabilities": [item.capability_id for item in eligible],
                    "status": str(getattr(lifecycle, "status", "unavailable") or "unavailable"),
                    "failure_code": str(getattr(lifecycle, "failure_code", "") or ""),
                    "returned": len(records),
                    "message": _safe_text(getattr(lifecycle, "message", ""), 300),
                })
                self.context.setdefault("_retrieval_lifecycles", []).append(
                    asdict(lifecycle)
                )
            if not records:
                cached = search_evidence_catalog(
                    self.context.get("searchable_evidence_catalog", []),
                    complete_query,
                    source_type=family,
                    period=period,
                    limit=limit,
                )
                attempts.append({
                    "source_family": family,
                    "capabilities": [item.capability_id for item in eligible],
                    "status": "catalog_match" if cached else "unavailable",
                    "failure_code": "" if cached else "no_matching_admitted_record",
                    "returned": len(cached),
                    "message": "Searched the normalized run catalog.",
                })
        evidence_items = self._merge_records(all_records)
        if not evidence_items:
            seen_ids: set[str] = set()
            for family in dict.fromkeys(families):
                for item in search_evidence_catalog(
                    self.context.get("searchable_evidence_catalog", []),
                    complete_query,
                    source_type=family,
                    period=period,
                    limit=limit,
                ):
                    evidence_id = str(item.get("evidence_id") or "")
                    if not evidence_id or evidence_id in seen_ids:
                        continue
                    seen_ids.add(evidence_id)
                    evidence_items.append(dict(item))
        evidence_ids = [
            str(item.get("evidence_id") or "")
            for item in evidence_items
            if item.get("evidence_id") and evidence_is_claim_eligible(item)
        ]
        self.context["_opened_evidence_ids"] = sorted({
            *self.context.get("_opened_evidence_ids", []),
            *evidence_ids,
        })
        content = [
            {
                "evidence_id": str(item.get("evidence_id") or ""),
                "source_type": str(item.get("source_type") or ""),
                "source_family": str(item.get("source_family") or ""),
                "document": str(item.get("form") or item.get("document_id") or ""),
                "date": str(item.get("publication_date") or item.get("period") or ""),
                "section": str(item.get("section") or ""),
                "excerpt": _safe_text(
                    item.get("exact_excerpt") or item.get("summary") or "",
                    1_500,
                ),
                "claim_eligible": evidence_is_claim_eligible(item),
                "metadata_only": not evidence_is_claim_eligible(item),
            }
            for item in evidence_items[:limit]
        ]
        discovery_matches = _search_discovery_metadata(
            self.context.get("news_discovery_catalog", []),
            complete_query,
            families,
            limit=max(0, limit - len(content)),
        )
        content.extend(discovery_matches)
        content.append({
            "connector_attempts": attempts,
            "unavailable_source_families": unavailable_families,
        })
        return self._observation(
            call,
            status=("completed" if evidence_items or discovery_matches else "no_result"),
            summary=(
                f"Searched {len(set(families))} source families; "
                f"{len(evidence_ids)} claim-eligible item(s) are available."
                if evidence_items else
                f"Found {len(discovery_matches)} quarantined discovery lead(s); registered full-content retrieval is still required."
                if discovery_matches else
                f"No usable source matched after {len(attempts)} registered attempts."
            ),
            source_family=", ".join(dict.fromkeys(families)),
            source_ids=[
                str(item.get("source_id") or item.get("evidence_id") or "")
                for item in evidence_items if item.get("source_id") or item.get("evidence_id")
            ],
            evidence_ids=evidence_ids,
            content=content,
            failure_code=(
                "" if evidence_items
                else "discovery_metadata_only" if discovery_matches
                else "no_usable_source"
            ),
            decision_eligible=bool(evidence_ids),
        )

    def _open_source(self, call: ResearchToolCall) -> ResearchObservation:
        source_id = str(
            call.arguments.get("source_id")
            or call.arguments.get("evidence_id")
            or ""
        )
        item = self._find_source(source_id)
        if item is None:
            return self._observation(
                call,
                status="no_result",
                summary="The requested source is not present in the admitted run catalog.",
                failure_code="source_not_found",
            )
        eligible = evidence_is_claim_eligible(item)
        return self._observation(
            call,
            status="completed",
            summary=(
                "Opened an admitted source record."
                if eligible else
                "Opened source metadata; it is not eligible to support a conclusion."
            ),
            source_family=str(item.get("source_family") or item.get("source_type") or ""),
            source_ids=[source_id],
            evidence_ids=[source_id] if eligible else [],
            content=[_readable_source(item)],
            decision_eligible=eligible,
        )

    def _find_in_source(self, call: ResearchToolCall) -> ResearchObservation:
        source_id = str(call.arguments.get("source_id") or "")
        item = self._find_source(source_id)
        if item is None:
            return self._observation(call, status="no_result", summary="Source not found.", failure_code="source_not_found")
        terms = [value.casefold() for value in _string_list(call.arguments.get("terms"))]
        text = str(item.get("exact_excerpt") or item.get("summary") or "")
        matches = [term for term in terms if term in text.casefold()]
        eligible = evidence_is_claim_eligible(item)
        return self._observation(
            call,
            status="completed",
            summary=f"Matched {len(matches)} of {len(terms)} requested term(s).",
            source_ids=[source_id],
            evidence_ids=[source_id] if eligible else [],
            content=[{"matched_terms": matches, "excerpt": _safe_text(text, 2_000)}],
            decision_eligible=eligible,
        )

    def _extract_table(self, call: ResearchToolCall) -> ResearchObservation:
        source_id = str(call.arguments.get("source_id") or "")
        item = self._find_source(source_id)
        if item is None:
            return self._observation(call, status="no_result", summary="Source not found.", failure_code="source_not_found")
        fields = _string_list(call.arguments.get("fields"))
        structured = {
            key: item.get(key)
            for key in (
                "metric", "value", "unit", "currency", "period", "period_start",
                "duration_days", "fiscal_period", "fiscal_year", "form", "section",
                "page_or_table", "formula",
            )
            if (not fields or key in fields) and item.get(key) not in {None, ""}
        }
        eligible = evidence_is_claim_eligible(item)
        return self._observation(
            call,
            status="completed" if structured else "no_result",
            summary=("Extracted structured source fields." if structured else "No structured fields were available."),
            source_ids=[source_id],
            evidence_ids=[source_id] if eligible else [],
            content=[structured] if structured else [],
            decision_eligible=eligible and bool(structured),
        )

    def _query_sec_facts(self, call: ResearchToolCall) -> ResearchObservation:
        requested = _string_list(call.arguments.get("metrics"))
        period = str(call.arguments.get("period") or "")
        metrics = list(getattr(self.retrieval_engine, "metrics", ()) or ())
        rows: list[dict[str, Any]] = []
        evidence_ids: list[str] = []
        for metric in sorted(metrics, key=lambda item: str(getattr(item, "period_end", "")), reverse=True):
            name = str(getattr(metric, "name", "") or "")
            if requested and not any(_matches_metric(name, value) for value in requested):
                continue
            if period and period not in " ".join((
                str(getattr(metric, "period_end", "") or ""),
                str(getattr(metric, "fiscal_period", "") or ""),
                str(getattr(metric, "fiscal_year", "") or ""),
            )):
                continue
            source_id = self._metric_evidence_id(metric)
            if source_id:
                evidence_ids.append(source_id)
            rows.append({
                "metric": name,
                "value": getattr(metric, "value", None),
                "unit": str(getattr(metric, "unit", "") or ""),
                "period_start": str(getattr(metric, "period_start", "") or ""),
                "period_end": str(getattr(metric, "period_end", "") or ""),
                "duration_days": getattr(metric, "duration_days", None),
                "fiscal_period": str(getattr(metric, "fiscal_period", "") or ""),
                "fiscal_year": getattr(metric, "fiscal_year", None),
                "form": str(getattr(metric, "form", "") or ""),
                "filed": str(getattr(metric, "filed", "") or ""),
                "evidence_id": source_id,
            })
            if len(rows) >= 80:
                break
        return self._observation(
            call,
            status="completed" if rows else "no_result",
            summary=f"Returned {len(rows)} period-aware filing fact(s)." if rows else "No matching admitted filing facts were available.",
            source_family="sec_filing",
            evidence_ids=list(dict.fromkeys(evidence_ids)),
            content=rows,
            failure_code="" if rows else "fact_not_available",
            decision_eligible=bool(evidence_ids),
        )

    def _query_market_data(self, call: ResearchToolCall) -> ResearchObservation:
        items = self._catalog_for_type("quantitative_historical_price")
        decision = self.context.get("decision_context") or {}
        market_implied = decision.get("market_implied_expectations") or {}
        content = [
            {
                "evidence_id": item.get("evidence_id"),
                "date": item.get("publication_date") or item.get("period"),
                "value": item.get("value"),
                "unit": item.get("unit"),
                "source": item.get("origin_group") or item.get("source_type"),
            }
            for item in items[:100]
        ]
        if isinstance(market_implied, dict):
            content.insert(0, {
                "current_price": market_implied.get("current_price"),
                "price_as_of": market_implied.get("price_as_of"),
                "currency": market_implied.get("currency"),
                "price_source": market_implied.get("price_source"),
            })
        evidence_ids = [
            str(item.get("evidence_id")) for item in items
            if item.get("evidence_id") and evidence_is_claim_eligible(item)
        ]
        return self._observation(
            call,
            status="completed" if content else "no_result",
            summary=f"Returned {len(items)} admitted market observation(s).",
            source_family="quantitative_historical_price",
            evidence_ids=evidence_ids,
            content=content,
            decision_eligible=bool(evidence_ids),
        )

    def _query_consensus_history(self, call: ResearchToolCall) -> ResearchObservation:
        decision = self.context.get("decision_context") or {}
        payload = _first_mapping(
            decision.get("consensus"),
            decision.get("expectations_bridge"),
            decision.get("market_capture_readiness"),
        )
        items = self._catalog_for_type("consensus_manual")
        evidence_ids = [
            str(item.get("evidence_id")) for item in items
            if item.get("evidence_id") and evidence_is_claim_eligible(item)
        ]
        content = [_safe_mapping(payload, 30_000)] if payload else []
        content.extend(_readable_source(item) for item in items[:30])
        return self._observation(
            call,
            status="completed" if content else "no_result",
            summary=(f"Returned dated consensus context and {len(items)} admitted source item(s)." if content else "Point-in-time consensus is unavailable for this run."),
            source_family="consensus_manual",
            evidence_ids=evidence_ids,
            content=content,
            failure_code="" if content else "consensus_unavailable",
            decision_eligible=bool(evidence_ids),
        )

    def _query_macro_series(self, call: ResearchToolCall) -> ResearchObservation:
        decision = self.context.get("decision_context") or {}
        payload = _first_mapping(
            decision.get("adaptive_macro_plan"),
            decision.get("external_evidence_status"),
            decision.get("macro"),
        )
        items = self._catalog_for_type("macro_market")
        evidence_ids = [
            str(item.get("evidence_id")) for item in items
            if item.get("evidence_id") and evidence_is_claim_eligible(item)
        ]
        content = [_safe_mapping(payload, 20_000)] if payload else []
        content.extend(_readable_source(item) for item in items[:30])
        return self._observation(
            call,
            status="completed" if content else "no_result",
            summary=f"Returned {len(items)} admitted macro observation(s)." if content else "No decision-relevant macro series is available.",
            source_family="macro_market",
            evidence_ids=evidence_ids,
            content=content,
            failure_code="" if content else "macro_unavailable",
            decision_eligible=bool(evidence_ids),
        )

    def _compare_periods(self, call: ResearchToolCall) -> ResearchObservation:
        metric = str(call.arguments.get("metric") or "")
        requested_periods = set(_string_list(call.arguments.get("periods")))
        metrics = [
            item for item in list(getattr(self.retrieval_engine, "metrics", ()) or ())
            if _matches_metric(str(getattr(item, "name", "") or ""), metric)
        ]
        if requested_periods:
            metrics = [
                item for item in metrics
                if str(getattr(item, "period_end", "") or "") in requested_periods
            ]
        metrics.sort(key=lambda item: str(getattr(item, "period_end", "") or ""))
        rows = []
        prior: Any | None = None
        evidence_ids: list[str] = []
        for item in metrics[-12:]:
            value = _number(getattr(item, "value", None))
            previous = _number(getattr(prior, "value", None)) if prior else None
            source_id = self._metric_evidence_id(item)
            evidence_ids.extend([source_id] if source_id else [])
            rows.append({
                "period_end": str(getattr(item, "period_end", "") or ""),
                "period_start": str(getattr(item, "period_start", "") or ""),
                "duration_days": getattr(item, "duration_days", None),
                "value": value,
                "unit": str(getattr(item, "unit", "") or ""),
                "absolute_change": value - previous if value is not None and previous is not None else None,
                "percent_change": ((value / previous - 1) * 100 if value is not None and previous not in {None, 0} else None),
                "evidence_id": source_id,
            })
            prior = item
        return self._observation(
            call,
            status="completed" if rows else "no_result",
            summary=f"Compared {len(rows)} period-aligned {metric or 'metric'} observation(s)." if rows else "No comparable periods were available.",
            evidence_ids=list(dict.fromkeys(evidence_ids)),
            content=rows,
            failure_code="" if rows else "comparable_period_unavailable",
            decision_eligible=bool(evidence_ids),
        )

    def _compute_financial_bridge(self, call: ResearchToolCall) -> ResearchObservation:
        bridge_kind = str(call.arguments.get("bridge_kind") or "cash_conversion").casefold()
        if bridge_kind == "cash_conversion" and _requests_recurrence_bridge(call):
            bridge_kind = "cash_conversion_recurrence"
        if bridge_kind == "cash_conversion_recurrence":
            return self._cash_conversion_recurrence_bridge(call)
        if bridge_kind == "cash_conversion":
            return self._cash_conversion_bridge(call)
        if bridge_kind == "margin":
            return self._margin_bridge(call)
        if bridge_kind == "liquidity":
            return self._liquidity_bridge(call)
        return self._observation(
            call,
            status="no_result",
            summary=f"The {bridge_kind} bridge is not available as a registered deterministic calculation.",
            failure_code="calculation_not_available",
        )

    def _financial_fact_series(self, call: ResearchToolCall):
        retriever = self.retrieval_engine
        identity = getattr(retriever, "identity", None)
        ticker = str(
            getattr(identity, "ticker", "")
            or self.context.get("ticker")
            or self.context.get("symbol")
            or ""
        ).upper()
        cik = str(getattr(identity, "cik", "") or "")
        exchange = str(getattr(identity, "exchange", "") or "")
        security_key = str(self.context.get("security_key") or ("cik:" + cik if cik else ticker))
        provider = SecCompanyFactsFinancialFactSeries(
            getattr(retriever, "company_facts", None),
            ticker=ticker,
            security_key=security_key,
            company_name=str(getattr(identity, "company_name", "") or self.context.get("company") or ticker),
        )
        metrics = tuple(dict.fromkeys(
            normalize_financial_fact_metric(str(item))
            for item in call.arguments.get("metrics", ())
            if str(item).strip()
        ))
        series = provider.query(FinancialFactSeriesQuery(
            ticker=ticker,
            security_key=security_key,
            normalized_metrics=metrics,
            data_cutoff=str(call.arguments.get("data_cutoff") or self.context.get("data_cutoff") or ""),
            comparison_periods=max(1, int(call.arguments.get("comparison_periods") or 2)),
        ))
        return provider, series, cash_conversion_intent_id(
            ticker,
            cik=cik,
            exchange=exchange,
        )

    def _query_sec_fact_series(self, call: ResearchToolCall) -> ResearchObservation:
        provider, series, _ = self._financial_fact_series(call)
        total_count = len(series.observations)
        requested = tuple(
            normalize_financial_fact_metric(str(item))
            for item in call.arguments.get("metrics", ())
            if str(item).strip()
        )
        selected = _select_financial_fact_observations(
            series.observations,
            requested_metrics=requested,
            comparison_periods=max(
                1, int(call.arguments.get("comparison_periods") or 2)
            ),
        )
        bounded_series = replace(series, observations=selected)
        admitted = self._merge_records(provider.source_records(bounded_series))
        series = admitted_series(bounded_series, admitted)
        content = [{
            "series_summary": {
                "ticker": series.ticker,
                "data_cutoff": series.data_cutoff,
                "total_matching_observations": total_count,
                "returned_observations": len(series.observations),
                "metrics": list(dict.fromkeys(
                    item.normalized_metric for item in series.observations
                )),
                "gaps": list(series.gaps),
                "selection": "latest comparable interim and annual contexts",
            }
        }, *[asdict(item) for item in series.observations]]
        return self._observation(
            call,
            status="completed" if series.observations else "no_result",
            summary=(
                f"Retrieved {len(series.observations)} cutoff-safe historical SEC fact observations."
                if series.observations else
                "No cutoff-safe historical SEC fact observations were available."
            ),
            evidence_ids=[
                str(item.get("evidence_id") or "") for item in admitted
                if item.get("evidence_id")
            ],
            content=content,
            failure_code="" if series.observations else "historical_sec_series_unavailable",
            decision_eligible=bool(admitted),
        )

    def _cash_conversion_recurrence_bridge(self, call: ResearchToolCall) -> ResearchObservation:
        archetype = str(
            call.arguments.get("security_archetype")
            or self.context.get("security_archetype")
            or "operating_company"
        ).casefold().replace(" ", "_")
        if archetype in {"bank", "insurer", "bank_or_insurer"}:
            return self._observation(
                call,
                status="no_result",
                summary=(
                    "The operating-company cash-conversion contract is not applicable; "
                    "use the capital, liquidity, credit-loss, and distributable-capital lens."
                ),
                failure_code="security_archetype_lens_required",
            )
        if archetype in {"reit", "real_estate_investment_trust"}:
            return self._observation(
                call,
                status="no_result",
                summary=(
                    "The operating-company cash-conversion contract is not applicable; "
                    "use FFO/AFFO, property cash flow, leverage, and distribution coverage."
                ),
                failure_code="security_archetype_lens_required",
            )
        provider, series, intent_id = self._financial_fact_series(call)
        comparison_periods = max(1, int(call.arguments.get("comparison_periods") or 2))
        # Calculate once with provisional source identities so only facts that
        # actually enter a displayed formula are sent through admission.  This
        # avoids adding the issuer's entire Company Facts history to one focused
        # answer while preserving identical validation for every admitted fact.
        staged = replace(series, observations=tuple(
            replace(item, evidence_id=item.source_id)
            for item in series.observations if item.no_lookahead_safe
        ))
        staged_bridge = CashConversionRecurrenceService().build(
            staged,
            comparison_periods=comparison_periods,
            intent_id=intent_id or call.question_id,
        )
        used_ids = {
            evidence_id
            for period in staged_bridge.periods
            for evidence_id in period.evidence_ids
        }
        admitted = self._merge_records(
            record for record in provider.source_records(series)
            if record.source_id in used_ids
        )
        bridge = CashConversionRecurrenceService().build(
            admitted_series(series, admitted),
            comparison_periods=comparison_periods,
            intent_id=intent_id or call.question_id,
        )
        return self._observation(
            call,
            status="completed" if bridge.periods else "no_result",
            summary=bridge.assessment.direct_answer,
            evidence_ids=[
                str(item.get("evidence_id") or "") for item in admitted
                if item.get("evidence_id")
            ],
            content=[asdict(bridge)],
            failure_code="" if bridge.periods else "comparable_ttm_bridge_unavailable",
            decision_eligible=len(bridge.periods) >= comparison_periods,
        )

    def _cash_conversion_bridge(self, call: ResearchToolCall) -> ResearchObservation:
        metric_aliases = {
            "net_income": ("net income", "net earnings"),
            "operating_cash_flow": ("operating cash flow", "net cash provided by operating activities"),
            "capital_expenditure": ("capital expenditure", "payments to acquire property plant and equipment"),
            "working_capital": ("working capital change", "change in working capital", "changes in operating assets and liabilities"),
            "cash_taxes": ("cash taxes", "cash paid for income taxes", "income taxes paid"),
            "debt_issued": ("debt issued", "proceeds from issuance of debt"),
            "debt_repaid": ("debt repaid", "repayments of debt", "repayments of long term debt"),
            "dividends": ("payments of dividends", "dividends paid"),
            "repurchases": ("repurchase of common stock", "repurchases"),
        }
        metrics = list(getattr(self.retrieval_engine, "metrics", ()) or ())
        grouped: dict[str, list[Any]] = {}
        for key, aliases in metric_aliases.items():
            grouped[key] = sorted(
                [
                    item for item in metrics
                    if any(_matches_metric(str(getattr(item, "name", "") or ""), alias) for alias in aliases)
                ],
                key=lambda item: str(getattr(item, "period_end", "") or ""),
                reverse=True,
            )
        period_keys = _candidate_period_keys(grouped)
        bridges: list[dict[str, Any]] = []
        evidence_ids: list[str] = []
        for period, target_duration in period_keys[:12]:
            selected: dict[str, Any] = {}
            for key, rows in grouped.items():
                match = _select_period_metric(
                    rows,
                    period=period,
                    target_duration=target_duration,
                )
                if match is not None:
                    selected[key] = match
            ocf = _number(getattr(selected.get("operating_cash_flow"), "value", None))
            net_income = _number(getattr(selected.get("net_income"), "value", None))
            capex_raw = _number(getattr(selected.get("capital_expenditure"), "value", None))
            capex = abs(capex_raw) if capex_raw is not None else None
            fcf = ocf - capex if ocf is not None and capex is not None else None
            dividends = _number(
                getattr(selected.get("dividends"), "value", None)
            )
            repurchases = _number(
                getattr(selected.get("repurchases"), "value", None)
            )
            capital_returns = sum(
                abs(value) for value in (dividends, repurchases)
                if value is not None
            )
            bridge_ids = [self._metric_evidence_id(item) for item in selected.values()]
            bridge_ids = [value for value in bridge_ids if value]
            evidence_ids.extend(bridge_ids)
            bridges.append({
                "period_end": period,
                "duration_days": (
                    _common_duration(selected.values())
                    if target_duration is None else target_duration
                ),
                "period_type": _period_type(target_duration),
                "net_income": net_income,
                "operating_cash_flow": ocf,
                "cash_conversion_ratio": ocf / net_income if ocf is not None and net_income not in {None, 0} else None,
                "capital_expenditure": capex,
                "free_cash_flow": fcf,
                "working_capital_change": _number(getattr(selected.get("working_capital"), "value", None)),
                "cash_taxes": _number(getattr(selected.get("cash_taxes"), "value", None)),
                "debt_issued": _number(getattr(selected.get("debt_issued"), "value", None)),
                "debt_repaid": _number(getattr(selected.get("debt_repaid"), "value", None)),
                "dividends": dividends,
                "repurchases": repurchases,
                "cash_after_dividends_and_repurchases": (
                    fcf - capital_returns if fcf is not None else None
                ),
                "component_coverage": sorted(selected),
                "missing_components": [
                    key for key in (
                        "working_capital", "cash_taxes", "debt_issued",
                        "debt_repaid", "dividends", "repurchases",
                    ) if key not in selected
                ],
                "formula": "FCF = operating cash flow - abs(capital expenditure); cash conversion = operating cash flow / net income",
                "evidence_ids": bridge_ids,
            })
        complete = [
            item for item in bridges
            if item["net_income"] is not None
            and item["operating_cash_flow"] is not None
            and item["capital_expenditure"] is not None
        ]
        positive_fcf_periods = sum(
            _number(item.get("free_cash_flow")) is not None
            and float(item["free_cash_flow"]) > 0
            for item in complete
        )
        recurring_periods = sum(
            _number(item.get("cash_conversion_ratio")) is not None
            and float(item["cash_conversion_ratio"]) >= 0.9
            and _number(item.get("free_cash_flow")) is not None
            and float(item["free_cash_flow"]) > 0
            for item in complete
        )
        recurrence_assessment = (
            "supported"
            if len(complete) >= 3 and recurring_periods >= max(2, len(complete) - 1)
            else "mixed"
            if len(complete) >= 2
            else "insufficient_history"
        )
        for item in bridges:
            item["recurrence_analysis"] = {
                "assessment": recurrence_assessment,
                "complete_periods": len(complete),
                "positive_free_cash_flow_periods": positive_fcf_periods,
                "strong_conversion_periods": recurring_periods,
                "high_confidence_requires": (
                    "At least three comparable periods plus working-capital, cash-tax, financing, and capital-return coverage."
                ),
            }
        status = "completed" if complete else "partial" if bridges else "no_result"
        return self._observation(
            call,
            status=status,
            summary=(
                f"Built {len(complete)} complete and {len(bridges) - len(complete)} partial cash-conversion period bridge(s)."
                if bridges else
                "No period-aligned cash-flow facts were available for the bridge."
            ),
            source_family="sec_filing",
            evidence_ids=list(dict.fromkeys(evidence_ids)),
            content=bridges,
            failure_code="" if complete else "cash_bridge_incomplete",
            decision_eligible=bool(complete and evidence_ids),
        )

    def _margin_bridge(self, call: ResearchToolCall) -> ResearchObservation:
        aliases = {
            "revenue": ("revenue", "sales"),
            "gross_profit": ("gross profit",),
            "operating_income": ("operating income", "income from operations"),
        }
        grouped = self._group_metrics(aliases)
        rows: list[dict[str, Any]] = []
        evidence_ids: list[str] = []
        for period, target_duration in _candidate_period_keys(grouped)[:12]:
            selected = {
                key: match
                for key, values in grouped.items()
                if (match := _select_period_metric(
                    values,
                    period=period,
                    target_duration=target_duration,
                )) is not None
            }
            revenue = _number(getattr(selected.get("revenue"), "value", None))
            gross_profit = _number(
                getattr(selected.get("gross_profit"), "value", None)
            )
            operating_income = _number(
                getattr(selected.get("operating_income"), "value", None)
            )
            row_ids = [
                self._metric_evidence_id(item) for item in selected.values()
            ]
            row_ids = [value for value in row_ids if value]
            evidence_ids.extend(row_ids)
            rows.append({
                "period_end": period,
                "duration_days": target_duration,
                "period_type": _period_type(target_duration),
                "revenue": revenue,
                "gross_profit": gross_profit,
                "operating_income": operating_income,
                "gross_margin_pct": (
                    gross_profit / revenue * 100
                    if gross_profit is not None and revenue not in {None, 0}
                    else None
                ),
                "operating_margin_pct": (
                    operating_income / revenue * 100
                    if operating_income is not None and revenue not in {None, 0}
                    else None
                ),
                "missing_components": [
                    key for key in aliases if key not in selected
                ],
                "formula": (
                    "Gross margin = gross profit / revenue; operating margin "
                    "= operating income / revenue"
                ),
                "evidence_ids": row_ids,
            })
        complete = [
            item for item in rows
            if item["revenue"] is not None
            and (
                item["gross_profit"] is not None
                or item["operating_income"] is not None
            )
        ]
        return self._observation(
            call,
            status="completed" if complete else "partial" if rows else "no_result",
            summary=(
                f"Built {len(complete)} comparable operating-margin period bridge(s)."
                if rows else
                "No period-aligned revenue and profit facts were available."
            ),
            source_family="sec_filing",
            evidence_ids=list(dict.fromkeys(evidence_ids)),
            content=rows,
            failure_code="" if complete else "margin_bridge_incomplete",
            decision_eligible=bool(complete and evidence_ids),
        )

    def _liquidity_bridge(self, call: ResearchToolCall) -> ResearchObservation:
        aliases = {
            "cash": (
                "cash", "cash and cash equivalents",
                "cash cash equivalents restricted cash",
            ),
            "current_assets": ("current assets",),
            "current_liabilities": ("current liabilities",),
            "current_debt": ("current debt", "short term debt"),
            "long_term_debt": ("long term debt", "long-term debt"),
        }
        grouped = self._group_metrics(aliases)
        periods = sorted({
            str(getattr(item, "period_end", "") or "")
            for values in grouped.values() for item in values
            if str(getattr(item, "period_end", "") or "")
        }, reverse=True)
        rows: list[dict[str, Any]] = []
        evidence_ids: list[str] = []
        for period in periods[:12]:
            selected: dict[str, Any] = {}
            for key, values in grouped.items():
                match = next((
                    item for item in values
                    if str(getattr(item, "period_end", "") or "") == period
                    and getattr(item, "duration_days", None) in {None, 0}
                ), None)
                if match is None:
                    match = next((
                        item for item in values
                        if str(getattr(item, "period_end", "") or "") == period
                    ), None)
                if match is not None:
                    selected[key] = match
            values = {
                key: _number(getattr(value, "value", None))
                for key, value in selected.items()
            }
            debt_values = [
                values[key] for key in ("current_debt", "long_term_debt")
                if values.get(key) is not None
            ]
            total_debt = sum(debt_values) if debt_values else None
            cash = values.get("cash")
            current_assets = values.get("current_assets")
            current_liabilities = values.get("current_liabilities")
            row_ids = [
                self._metric_evidence_id(item) for item in selected.values()
            ]
            row_ids = [value for value in row_ids if value]
            evidence_ids.extend(row_ids)
            rows.append({
                "period_end": period,
                "cash": cash,
                "current_assets": current_assets,
                "current_liabilities": current_liabilities,
                "current_debt": values.get("current_debt"),
                "long_term_debt": values.get("long_term_debt"),
                "total_debt": total_debt,
                "net_debt": (
                    total_debt - cash
                    if total_debt is not None and cash is not None else None
                ),
                "current_ratio": (
                    current_assets / current_liabilities
                    if current_assets is not None
                    and current_liabilities not in {None, 0}
                    else None
                ),
                "missing_components": [
                    key for key in aliases if key not in selected
                ],
                "formula": (
                    "Net debt = current debt + long-term debt - cash; "
                    "current ratio = current assets / current liabilities"
                ),
                "evidence_ids": row_ids,
            })
        complete = [
            item for item in rows
            if item["cash"] is not None and item["total_debt"] is not None
        ]
        return self._observation(
            call,
            status="completed" if complete else "partial" if rows else "no_result",
            summary=(
                f"Built {len(complete)} comparable liquidity and net-debt snapshot(s)."
                if rows else
                "No period-aligned liquidity facts were available."
            ),
            source_family="sec_filing",
            evidence_ids=list(dict.fromkeys(evidence_ids)),
            content=rows,
            failure_code="" if complete else "liquidity_bridge_incomplete",
            decision_eligible=bool(complete and evidence_ids),
        )

    def _group_metrics(
        self,
        aliases: dict[str, tuple[str, ...]],
    ) -> dict[str, list[Any]]:
        metrics = list(getattr(self.retrieval_engine, "metrics", ()) or ())
        return {
            key: sorted(
                [
                    item for item in metrics
                    if _matches_any_metric(
                        str(getattr(item, "name", "") or ""), names
                    )
                ],
                key=lambda item: str(getattr(item, "period_end", "") or ""),
                reverse=True,
            )
            for key, names in aliases.items()
        }

    def _compute_reverse_valuation(self, call: ResearchToolCall) -> ResearchObservation:
        decision = self.context.get("decision_context") or {}
        market_implied = decision.get("market_implied_expectations") or {}
        valuation = decision.get("valuation") or {}
        assumptions = dict(call.arguments.get("assumption_overrides") or {})
        calculation_policy = (
            "Existing deterministic reverse-valuation output is returned."
        )
        inputs = self.context.get("_market_implied_inputs") or {}
        if assumptions and isinstance(inputs, dict):
            required = {
                "identity", "metrics", "current_price", "valuation",
                "company_model",
            }
            if required <= set(inputs):
                recomputed = build_market_implied_expectations(
                    inputs["identity"],
                    list(inputs["metrics"]),
                    inputs["current_price"],
                    inputs["valuation"],
                    inputs["company_model"],
                    price_source=str(inputs.get("price_source") or ""),
                    price_as_of=str(inputs.get("price_as_of") or "") or None,
                    assumption_overrides=assumptions,
                )
                market_implied = asdict(recomputed)
                calculation_policy = (
                    "Recomputed with the existing deterministic reverse-valuation "
                    "engine. Overrides remain explicit assumptions, not evidence."
                )
        content = {
            "market_implied_expectations": _safe_mapping(market_implied, 25_000),
            "internal_valuation": _safe_mapping(valuation, 25_000),
            "user_assumption_overrides": assumptions,
            "calculation_policy": calculation_policy,
        }
        relevant = [
            item for item in self.context.get("searchable_evidence_catalog", [])
            if str(item.get("source_type") or "") in {
                "quantitative_historical_price", "sec_filing", "consensus_manual"
            } and evidence_is_claim_eligible(item)
        ]
        evidence_ids = [str(item.get("evidence_id")) for item in relevant if item.get("evidence_id")]
        available = bool(market_implied or valuation)
        return self._observation(
            call,
            status="completed" if available else "no_result",
            summary="Returned reproducible reverse-valuation and internal scenario context." if available else "Reverse valuation inputs are unavailable.",
            evidence_ids=evidence_ids[:30],
            content=[content] if available else [],
            failure_code="" if available else "reverse_valuation_unavailable",
            decision_eligible=available and bool(evidence_ids),
        )

    def _compute_event_attribution(self, call: ResearchToolCall) -> ResearchObservation:
        decision = self.context.get("decision_context") or {}
        payload = _first_mapping(
            decision.get("market_capture_readiness"),
            decision.get("event_window_reactions"),
            decision.get("event_attribution"),
        )
        items = self._catalog_for_type("quantitative_historical_price")
        evidence_ids = [
            str(item.get("evidence_id")) for item in items
            if item.get("evidence_id") and evidence_is_claim_eligible(item)
        ]
        return self._observation(
            call,
            status="completed" if payload else "no_result",
            summary="Returned market, sector, and beta-adjusted event attribution." if payload else "Event attribution is unavailable or the forward window is incomplete.",
            source_family="quantitative_historical_price",
            evidence_ids=evidence_ids,
            content=[_safe_mapping(payload, 30_000)] if payload else [],
            failure_code="" if payload else "event_attribution_unavailable",
            decision_eligible=bool(payload and evidence_ids),
        )

    def _propose_evidence(self, call: ResearchToolCall) -> ResearchObservation:
        requested = _string_list(call.arguments.get("evidence_ids"))
        requested += _string_list(call.arguments.get("contrary_evidence_ids"))
        eligible = set(self.context.get("claim_eligible_evidence_ids", []))
        accepted = [value for value in dict.fromkeys(requested) if value in eligible]
        rejected = [value for value in dict.fromkeys(requested) if value not in eligible]
        return self._observation(
            call,
            status="completed" if accepted else "no_result",
            summary=(
                f"Validated {len(accepted)} admitted evidence reference(s); "
                f"rejected {len(rejected)} non-admitted reference(s)."
            ),
            evidence_ids=accepted,
            content=[{
                "claim": _safe_text(call.arguments.get("claim"), 1_000),
                "accepted_evidence_ids": accepted,
                "rejected_reference_count": len(rejected),
                "evidence_creation": "No evidence ID was created by this tool.",
            }],
            failure_code="" if accepted else "no_admitted_evidence",
            decision_eligible=bool(accepted),
        )

    def _record_research_note(self, call: ResearchToolCall) -> ResearchObservation:
        note = {
            "entry_type": str(call.arguments.get("entry_type") or "interpretation"),
            "summary": _safe_text(call.arguments.get("summary"), 2_000),
            "observation_ids": _string_list(call.arguments.get("observation_ids")),
            "question_id": call.question_id,
            "recorded_at": _utc_now(),
        }
        self._notes.append(note)
        return self._observation(
            call,
            status="completed",
            summary="Recorded a provisional exploration note; it is not decision evidence.",
            content=[{"entry_type": note["entry_type"], "summary": note["summary"], "decision_eligible": False}],
        )

    def _submit_research_answer(self, call: ResearchToolCall) -> ResearchObservation:
        status = str(call.arguments.get("status") or "partially_answered")
        permitted_statuses = {
            "answered", "partially_answered", "blocked_by_source_access",
            "blocked_by_provider_failure", "cancelled",
        }
        if status not in permitted_statuses:
            status = "partially_answered"
        eligible = set(self.context.get("claim_eligible_evidence_ids", []))
        proposed = _string_list(call.arguments.get("evidence_ids"))
        contrary = _string_list(call.arguments.get("contrary_evidence_ids"))
        evidence_ids = [value for value in dict.fromkeys(proposed) if value in eligible]
        contrary_ids = [value for value in dict.fromkeys(contrary) if value in eligible]
        if status == "answered" and not evidence_ids:
            status = "partially_answered"
        answer = {
            "question_id": call.question_id,
            "status": status,
            "answer": _safe_text(call.arguments.get("answer"), 5_000),
            "confidence": str(call.arguments.get("confidence") or "Unknown"),
            "confidence_reason": _safe_text(call.arguments.get("confidence_reason"), 1_000),
            "mechanism": _safe_text(call.arguments.get("mechanism"), 2_000),
            "evidence_ids": evidence_ids,
            "contrary_evidence_ids": contrary_ids,
            "key_findings": _string_list(call.arguments.get("key_findings"))[:10],
            "strongest_limitation": _safe_text(call.arguments.get("strongest_limitation"), 1_500),
            "remaining_work": _string_list(call.arguments.get("remaining_work"))[:10],
            "changed_conclusions": _string_list(call.arguments.get("changed_conclusions"))[:10],
        }
        self._submitted_answers[call.question_id] = answer
        return self._observation(
            call,
            status="completed",
            summary=f"Submitted a {status.replace('_', ' ')} answer with {len(evidence_ids)} admitted evidence item(s).",
            evidence_ids=evidence_ids,
            content=[answer],
            decision_eligible=bool(evidence_ids),
        )

    def _merge_records(self, records: Iterable[Any]) -> list[dict[str, Any]]:
        records_by_id: dict[str, Any] = {}
        for record in records:
            source_id = str(getattr(record, "source_id", "") or "")
            if source_id:
                records_by_id[source_id] = record
        if not records_by_id:
            return []
        evidence_by_id = {
            str(item.get("evidence_id") or ""): dict(item)
            for item in self.context.get("searchable_evidence_catalog", [])
            if isinstance(item, dict) and item.get("evidence_id")
        }
        normalized_by_id = {
            str(item.get("source_id") or ""): dict(item)
            for item in self.context.get("normalized_sources", [])
            if isinstance(item, dict) and item.get("source_id")
        }
        for source_id, record in records_by_id.items():
            normalized_by_id[source_id] = asdict(record)
            evidence_by_id[source_id] = source_record_to_evidence(record)
        self.context["normalized_sources"] = list(normalized_by_id.values())
        self.context["searchable_evidence_catalog"] = list(evidence_by_id.values())
        eligible_ids = {
            evidence_id for evidence_id, item in evidence_by_id.items()
            if evidence_id and evidence_is_claim_eligible(item)
        }
        self.context["claim_eligible_evidence_ids"] = sorted(eligible_ids)
        self.context["_opened_evidence_ids"] = sorted({
            *self.context.get("_opened_evidence_ids", []),
            *records_by_id.keys(),
        } & eligible_ids)
        self.context["evidence_metadata_catalog"] = evidence_catalog_metadata(
            self.context["searchable_evidence_catalog"]
        )
        evidence_hash, fingerprints = semantic_evidence_hash(
            self.context["searchable_evidence_catalog"],
            prompt_version=str(self.context.get("prompt_version") or "agent-led-research-v1"),
            source_registry_version=str(self.context.get("source_registry_version") or SOURCE_REGISTRY_VERSION),
        )
        self.context["evidence_hash"] = evidence_hash
        self.context["evidence_fingerprints"] = fingerprints
        return [evidence_by_id[source_id] for source_id in records_by_id]

    def _find_source(self, source_id: str) -> dict[str, Any] | None:
        for item in self.context.get("searchable_evidence_catalog", []):
            if isinstance(item, dict) and str(item.get("evidence_id") or "") == source_id:
                return dict(item)
        for item in self.context.get("normalized_sources", []):
            if isinstance(item, dict) and str(item.get("source_id") or "") == source_id:
                return {
                    **dict(item),
                    "evidence_id": source_id,
                    "summary": item.get("exact_excerpt"),
                }
        for item in self.context.get("news_discovery_catalog", []):
            if isinstance(item, dict) and str(item.get("source_id") or "") == source_id:
                return {
                    "evidence_id": "",
                    "source_id": source_id,
                    "source_type": str(item.get("source_type") or "public_news_metadata"),
                    "source_family": "discovery_metadata",
                    "document_id": source_id,
                    "source_url": str(item.get("url") or ""),
                    "publication_date": str(item.get("published_at") or ""),
                    "section": "News discovery lead",
                    "exact_excerpt": str(item.get("excerpt") or ""),
                    "summary": str(item.get("headline") or ""),
                    "origin_group": str(item.get("source_origin_group") or ""),
                    "licensing_policy": str(item.get("licensing_policy") or "metadata_only"),
                    "retrieval_status": "metadata_only",
                    "parser_status": "not_parsed",
                    "validation_status": "quarantined",
                    "metadata_only": True,
                }
        return None

    def _catalog_for_type(self, source_type: str) -> list[dict[str, Any]]:
        return [
            dict(item) for item in self.context.get("searchable_evidence_catalog", [])
            if isinstance(item, dict) and str(item.get("source_type") or "") == source_type
        ]

    def _metric_evidence_id(self, metric: Any) -> str:
        accession = str(getattr(metric, "accession", "") or "")
        period_end = str(getattr(metric, "period_end", "") or "")
        name = str(getattr(metric, "name", "") or "")
        candidates = [
            item for item in self.context.get("searchable_evidence_catalog", [])
            if isinstance(item, dict)
            and _matches_metric(str(item.get("metric") or item.get("summary") or ""), name)
            and (not period_end or period_end in str(item.get("period") or ""))
            and (not accession or accession == str(item.get("accession") or ""))
            and evidence_is_claim_eligible(item)
        ]
        return str(candidates[0].get("evidence_id") or "") if candidates else ""

    def _observation(
        self,
        call: ResearchToolCall,
        *,
        status: str,
        summary: str,
        source_family: str = "",
        source_ids: list[str] | None = None,
        evidence_ids: list[str] | None = None,
        content: list[dict[str, Any]] | None = None,
        failure_code: str = "",
        decision_eligible: bool = False,
    ) -> ResearchObservation:
        return ResearchObservation(
            observation_id=_stable_id("observation", call.call_id, call.tool_name, summary),
            call_id=call.call_id,
            question_id=call.question_id,
            tool_name=call.tool_name,
            status=status,
            summary=redact_sensitive_text(summary),
            source_family=source_family,
            source_ids=source_ids or [],
            evidence_ids=evidence_ids or [],
            content=redact_sensitive_value(content or []),
            failure_code=failure_code,
            decision_eligible=decision_eligible,
            observed_at=_utc_now(),
        )


def _tool(name: str, description: str, arguments: dict[str, str]) -> dict[str, Any]:
    return {"name": name, "description": description, "arguments": arguments}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_id(*parts: Any) -> str:
    payload = json.dumps(parts, ensure_ascii=True, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _safe_text(value: Any, limit: int = 500) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _safe_mapping(value: Any, limit: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    text = json.dumps(value, ensure_ascii=True, default=str)
    if len(text) <= limit:
        return dict(value)
    return {"summary": text[:limit], "truncated": True}


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    try:
        flattened: list[str] = []
        for item in value:
            flattened.extend(_string_list(item))
        return flattened
    except TypeError:
        return [str(value).strip()] if str(value).strip() else []


def _integer(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _matches_metric(left: str, right: str) -> bool:
    left_tokens = {token for token in _normalize_metric(left).split() if len(token) > 2}
    right_tokens = {token for token in _normalize_metric(right).split() if len(token) > 2}
    return bool(left_tokens and right_tokens and (
        left_tokens <= right_tokens
        or right_tokens <= left_tokens
        or len(left_tokens & right_tokens) >= min(2, len(right_tokens))
    ))


def _matches_any_metric(left: str, aliases: Iterable[str]) -> bool:
    normalized_left = _normalize_metric(left)
    for alias in aliases:
        normalized_alias = _normalize_metric(alias)
        # Single-token balance-sheet labels such as Cash must not capture
        # Cash Taxes or Operating Cash Flow.
        if normalized_alias == "cash":
            if normalized_left == normalized_alias:
                return True
            continue
        if _matches_metric(left, alias):
            return True
    return False


def _normalize_metric(value: str) -> str:
    return " ".join(
        "".join(character if character.isalnum() else " " for character in value.casefold()).split()
    )


def _candidate_period_keys(
    grouped: dict[str, list[Any]],
) -> list[tuple[str, int | None]]:
    anchors = (
        grouped.get("operating_cash_flow")
        or grouped.get("net_income")
        or [item for rows in grouped.values() for item in rows]
    )
    keys = {
        (
            str(getattr(item, "period_end", "") or ""),
            _metric_duration(item),
        )
        for item in anchors
        if getattr(item, "period_end", None)
    }
    return sorted(
        keys,
        key=lambda item: (item[0], item[1] or -1),
        reverse=True,
    )


def _select_period_metric(
    rows: list[Any],
    *,
    period: str,
    target_duration: int | None,
) -> Any | None:
    candidates = [
        item for item in rows
        if str(getattr(item, "period_end", "") or "") == period
    ]
    if not candidates:
        return None
    if target_duration is None:
        return candidates[0]
    return min(
        candidates,
        key=lambda item: (
            abs((_metric_duration(item) or target_duration) - target_duration),
            str(getattr(item, "filed", "") or ""),
        ),
    )


def _metric_duration(item: Any) -> int | None:
    value = getattr(item, "duration_days", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _period_type(duration_days: int | None) -> str:
    if duration_days is None:
        return "unknown"
    if duration_days >= 330:
        return "annual"
    if duration_days >= 240:
        return "nine_month"
    if duration_days >= 150:
        return "six_month"
    if duration_days >= 70:
        return "quarterly"
    return "other"


def _common_duration(items: Iterable[Any]) -> int | None:
    durations = {
        int(value) for value in (
            getattr(item, "duration_days", None) for item in items
        ) if value is not None
    }
    return next(iter(durations)) if len(durations) == 1 else None


def _requests_recurrence_bridge(call: ResearchToolCall) -> bool:
    text = " ".join((
        str(call.objective or ""),
        str(call.reason or ""),
        str(call.arguments.get("period_basis") or ""),
    )).casefold().replace("-", " ")
    return (
        "recurr" in text
        or "comparable ttm" in text
        or int(call.arguments.get("comparison_periods") or 1) >= 2
    )


def _select_financial_fact_observations(
    observations: Iterable[Any],
    *,
    requested_metrics: tuple[str, ...],
    comparison_periods: int,
) -> tuple[Any, ...]:
    """Return a compact, period-useful series instead of an unbounded XBRL wall."""

    grouped: dict[str, list[Any]] = {}
    for item in observations:
        grouped.setdefault(str(getattr(item, "normalized_metric", "") or ""), []).append(item)
    preferred = list(dict.fromkeys((
        *requested_metrics,
        "net_income",
        "operating_cash_flow",
        "capital_expenditure",
        "cash_taxes",
        "dividends",
        "repurchases",
    )))
    preferred.extend(key for key in grouped if key not in preferred)
    per_metric_limit = max(4, comparison_periods * 3)
    selected: list[Any] = []
    for metric in preferred:
        rows = sorted(
            grouped.get(metric, ()),
            key=lambda item: (
                str(getattr(item, "period_end", "") or ""),
                int(getattr(item, "duration_days", 0) or 0),
                str(getattr(item, "filed", "") or ""),
            ),
            reverse=True,
        )
        selected.extend(rows[:per_metric_limit])
        if len(selected) >= 19:
            break
    return tuple(selected[:19])


def _readable_source(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidence_id": str(item.get("evidence_id") or item.get("source_id") or ""),
        "source_type": str(item.get("source_type") or ""),
        "document": str(item.get("form") or item.get("document_id") or ""),
        "date": str(item.get("publication_date") or item.get("period") or ""),
        "section": str(item.get("section") or ""),
        "table": str(item.get("page_or_table") or ""),
        "excerpt": _safe_text(item.get("exact_excerpt") or item.get("summary") or "", 2_500),
        "value": item.get("value"),
        "unit": str(item.get("unit") or ""),
        "currency": str(item.get("currency") or ""),
        "source_url": str(item.get("source_url") or ""),
        "claim_eligible": evidence_is_claim_eligible(item),
    }


def _first_mapping(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict) and value:
            return value
    return {}


def _search_discovery_metadata(
    catalog: Any,
    query: str,
    families: list[str],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    if limit <= 0 or not any(
        value in {
            "public_news_metadata", "reputable_publisher", "external_research",
            "licensed_newswire", "research_article",
        }
        for value in families
    ):
        return []
    query_terms = {
        token for token in _normalize_metric(query).split()
        if len(token) >= 4
    }
    ranked: list[tuple[int, dict[str, Any]]] = []
    for raw in catalog or []:
        if not isinstance(raw, dict):
            continue
        text = " ".join(
            str(raw.get(key) or "")
            for key in ("headline", "publisher", "excerpt", "provider")
        )
        overlap = len(query_terms & set(_normalize_metric(text).split()))
        if query_terms and overlap == 0:
            continue
        ranked.append((overlap, raw))
    ranked.sort(key=lambda item: (-item[0], str(item[1].get("published_at") or "")), reverse=False)
    return [
        {
            "source_id": str(item.get("source_id") or ""),
            "headline": _safe_text(item.get("headline"), 500),
            "publisher": _safe_text(item.get("publisher"), 200),
            "published_at": str(item.get("published_at") or ""),
            "url": str(item.get("url") or ""),
            "snippet": _safe_text(item.get("excerpt"), 800),
            "licensing_policy": str(item.get("licensing_policy") or "metadata_only"),
            "metadata_only": True,
            "decision_eligible": False,
            "instruction": (
                "Discovery lead only. Retrieve permitted full content through a "
                "registered connector before citing or using it as fact."
            ),
        }
        for _, item in ranked[:limit]
    ]
