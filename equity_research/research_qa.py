from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Iterable
import uuid

from .domain.research_integrity import CandidateStatusEvent

from .models import (
    AdaptiveMacroResearchPlan,
    AuditFinding,
    AuditResolution,
    CandidateDiscoveryItem,
    CandidateStatus,
    CandidateType,
    ClosureAuditResult,
    CompanyMacroExposure,
    ConclusionRevision,
    DataAccessPolicy,
    EvidenceAdmissionDecision,
    EvidenceGap,
    EvidenceGraph,
    EvidenceNode,
    EvidenceRelationship,
    EvidenceRequest,
    HybridResearchRun,
    IndependentAuditReadiness,
    IndependentResearchQaRun,
    ModelExecutionRecord,
    PrimaryModelReadiness,
    PrimaryAuditResponse,
    ResearchAiExecutionPlan,
    ResearchExecutionPolicy,
    ResearchHypothesis,
    ResearchRequest,
    ResearchQaDimension,
    ResearchQaReport,
    ResearchQaScore,
    ResearchRoleAssignment,
    ResearchSetupPreview,
    SourcePolicyFingerprint,
    UnresolvedResearchRisk,
)


EVIDENCE_GRAPH_VERSION = "evidence-graph-v1"
RESEARCH_QA_VERSION = "independent-research-qa-v1"

BASELINE_MACRO_FACTORS = (
    "Rates and yield curve",
    "Inflation",
    "Economic growth",
    "Labor conditions",
    "USD",
    "Liquidity and financial conditions",
    "Relevant global risks",
)

_SOURCE_FAMILY_BY_TYPE = {
    "sec_filing": "primary_company",
    "historical_filing": "historical_primary",
    "issuer_ir": "primary_company",
    "issuer_announcement": "primary_company",
    "issuer_ir_report": "global_peer_official",
    "earnings_transcript": "management_context",
    "presentation": "issuer_context",
    "consensus_manual": "market_capture",
    "macro_market": "market_context",
    "quantitative_historical_price": "historical_quantitative",
    "licensed_newswire": "news",
    "reputable_publisher": "news",
    "public_news_metadata": "news",
    "gdelt_narrative": "narrative",
    "external_research": "external_research",
    "regulator_court": "primary_official",
    "product_safety": "primary_official",
    "patent_ip": "primary_official",
    "government_contract": "primary_official",
    "trade_import_export": "primary_official",
    "labor_employment": "primary_official",
    "industry_official_dataset": "primary_official",
    "hkex_document": "global_peer_official",
    "cninfo_document": "global_peer_official",
}


def build_research_execution_policy(
    research_depth: str,
    *,
    legacy_budget_mode: str = "",
    max_model_spend_usd: float | None = None,
    cache_bypass: bool = False,
) -> ResearchExecutionPolicy:
    depth = _normalize_depth(research_depth)
    settings = {
        "fast_screening": (1, 0, 0, 1, []),
        "adaptive_ic": (1, 1, 1, 0, []),
        "deep_initiation": (
            2,
            2,
            2,
            0,
            [
                "accounting_specialist",
                "valuation_specialist",
                "industry_specialist",
            ],
        ),
        "investigate_event": (1, 1, 1, 0, ["event_driven_specialist"]),
    }[depth]
    return ResearchExecutionPolicy(
        research_depth=depth,
        retrieval_pass_limit=settings[0],
        evidence_gap_loop_limit=settings[1],
        specialist_roles=list(settings[4]),
        permitted_model_roles=[
            "research_planner",
            "primary_investment_analyst",
            "independent_research_auditor",
            "adversarial_evidence_scout",
            "evidence_gap_resolver",
            "closure_checker",
            "investment_committee_writer",
            *settings[4],
        ],
        max_model_spend_usd=max_model_spend_usd,
        cache_bypass=cache_bypass,
        legacy_budget_mode=legacy_budget_mode,
        max_investigation_cycles=settings[2],
        focused_mapping_escalation_limit=settings[3],
        stop_on_no_new_evidence=True,
    )


def build_data_access_policy(
    evidence_catalog: Iterable[dict[str, Any]] = (),
    *,
    enabled_source_families: Iterable[str] | None = None,
    disabled_source_families: Iterable[str] = (),
    provider_entitlements: dict[str, bool] | None = None,
    allowed_licensing_policies: Iterable[str] = (),
    market: str = "US-listed",
    jurisdiction: str = "US",
    data_cutoff: str = "",
    allow_cached_evidence: bool = True,
) -> DataAccessPolicy:
    discovered = sorted({
        _source_family(item)
        for item in evidence_catalog
        if isinstance(item, dict) and _source_family(item)
    })
    enabled = sorted({str(item) for item in (enabled_source_families or discovered) if item})
    return DataAccessPolicy(
        enabled_source_families=enabled,
        disabled_source_families=sorted({str(item) for item in disabled_source_families if item}),
        provider_entitlements={
            str(key): bool(value)
            for key, value in (provider_entitlements or {}).items()
            if key
        },
        allowed_licensing_policies=sorted({
            str(item) for item in allowed_licensing_policies if item
        }),
        market=market,
        jurisdiction=jurisdiction,
        data_cutoff=data_cutoff,
        allow_cached_evidence=allow_cached_evidence,
    )


def apply_source_policy_to_context(
    context: dict[str, Any],
    execution_policy: ResearchExecutionPolicy,
    data_access_policy: DataAccessPolicy,
    adaptive_macro_plan: AdaptiveMacroResearchPlan | None = None,
) -> tuple[dict[str, Any], SourcePolicyFingerprint, EvidenceGraph]:
    safe_context = dict(context)
    searchable = [
        dict(item) for item in context.get("searchable_evidence_catalog", [])
        if isinstance(item, dict)
    ]
    decisions = [
        evaluate_evidence_admission(item, data_access_policy)
        for item in searchable
    ]
    admitted_ids = {
        item.evidence_id for item in decisions if item.status == "admitted"
    }
    admitted = [
        item for item in searchable
        if str(item.get("evidence_id") or item.get("source_id") or "") in admitted_ids
    ]
    factor_names = [
        item.factor for item in (adaptive_macro_plan.selected_exposures if adaptive_macro_plan else [])
    ]
    fingerprint = build_source_policy_fingerprint(
        execution_policy,
        data_access_policy,
        adaptive_macro_factors=factor_names,
        prompt_version=str(context.get("prompt_version") or ""),
        source_registry_version=str(context.get("source_registry_version") or ""),
    )
    graph = build_evidence_graph(
        str(context.get("ticker") or ""),
        admitted,
        decisions,
        fingerprint,
    )
    safe_context["evidence_catalog"] = [
        item for item in context.get("evidence_catalog", [])
        if str(item.get("evidence_id") or item.get("source_id") or "") in admitted_ids
    ]
    safe_context["searchable_evidence_catalog"] = admitted
    safe_context["claim_eligible_evidence_ids"] = [
        item for item in context.get("claim_eligible_evidence_ids", [])
        if str(item) in admitted_ids
    ]
    safe_context["evidence_metadata_catalog"] = [
        item for item in context.get("evidence_metadata_catalog", [])
        if str(item.get("evidence_id") or item.get("source_id") or "") in admitted_ids
    ]
    safe_context["source_policy_fingerprint"] = fingerprint.fingerprint
    safe_context["source_policy"] = asdict(fingerprint)
    safe_context["evidence_admission_decisions"] = [asdict(item) for item in decisions]
    safe_context["evidence_graph"] = asdict(graph)
    safe_context["adaptive_macro_plan"] = (
        asdict(adaptive_macro_plan) if adaptive_macro_plan else None
    )
    safe_context["quality_first_execution"] = {
        **dict(safe_context.get("quality_first_execution") or {}),
        "research_depth": execution_policy.research_depth,
        "retrieval_pass_limit": execution_policy.retrieval_pass_limit,
        "evidence_gap_loop_limit": execution_policy.evidence_gap_loop_limit,
        "max_investigation_cycles": execution_policy.max_investigation_cycles,
        "focused_mapping_escalation_limit": execution_policy.focused_mapping_escalation_limit,
        "stop_on_no_new_evidence": execution_policy.stop_on_no_new_evidence,
        "specialist_roles": list(execution_policy.specialist_roles),
        "max_model_spend_usd": execution_policy.max_model_spend_usd,
        "source_policy_fingerprint": fingerprint.fingerprint,
        "shared_evidence_graph": True,
        "two_choreography_production_enabled": False,
    }
    safe_context["evidence_hash"] = _stable_hash({
        "admitted_evidence": admitted,
        "source_policy_fingerprint": fingerprint.fingerprint,
        "prompt_version": context.get("prompt_version") or "",
        "source_registry_version": context.get("source_registry_version") or "",
    })
    safe_context["evidence_fingerprints"] = {
        key: value
        for key, value in dict(context.get("evidence_fingerprints") or {}).items()
        if key in admitted_ids
    }
    return safe_context, fingerprint, graph


def evaluate_evidence_admission(
    item: dict[str, Any],
    policy: DataAccessPolicy,
) -> EvidenceAdmissionDecision:
    evidence_id = str(item.get("evidence_id") or item.get("source_id") or _stable_hash(item)[:20])
    family = _source_family(item)
    provider = str(item.get("provider") or item.get("origin_group") or item.get("source") or "")
    licensing = str(item.get("licensing_policy") or item.get("licensing_status") or "unknown")
    publication = str(
        item.get("publication_date")
        or item.get("event_date")
        or item.get("observed_at")
        or ""
    )
    validation = str(item.get("validation_status") or item.get("status") or "").casefold()
    reasons: list[str] = []
    if family in set(policy.disabled_source_families):
        reasons.append("Available from prior run; excluded by current setup.")
    if policy.enabled_source_families and family not in set(policy.enabled_source_families):
        reasons.append("Source family is not enabled by the current data-access policy.")
    if provider and policy.provider_entitlements.get(provider) is False:
        reasons.append("Provider is not entitled in the current setup.")
    if any(token in licensing.casefold() for token in ("prohibited", "unlicensed", "do_not_use")):
        reasons.append("Licensing policy prohibits evidence admission.")
    if (
        policy.allowed_licensing_policies
        and licensing not in set(policy.allowed_licensing_policies)
    ):
        reasons.append("Licensing classification is outside the current allowlist.")
    if validation in {"rejected", "invalid", "failed", "quarantined"}:
        reasons.append("Deterministic validation did not pass.")
    no_lookahead = "eligible"
    if policy.data_cutoff and publication and publication[:10] > policy.data_cutoff[:10]:
        no_lookahead = "ineligible"
        reasons.append("Evidence occurs after the current point-in-time cutoff.")
    status = "excluded" if reasons else "admitted"
    return EvidenceAdmissionDecision(
        evidence_id=evidence_id,
        status=status,
        reason="; ".join(reasons) if reasons else "Admitted by the current source policy.",
        source_family=family,
        provider=provider,
        licensing_policy=licensing,
        freshness=_freshness(publication),
        no_lookahead_status=no_lookahead,
        admitted_at=_utc_now(),
    )


def build_source_policy_fingerprint(
    execution_policy: ResearchExecutionPolicy,
    data_access_policy: DataAccessPolicy,
    *,
    adaptive_macro_factors: Iterable[str] = (),
    prompt_version: str = "",
    source_registry_version: str = "",
) -> SourcePolicyFingerprint:
    payload = {
        "enabled_source_families": sorted(data_access_policy.enabled_source_families),
        "disabled_source_families": sorted(data_access_policy.disabled_source_families),
        "provider_entitlements": dict(sorted(data_access_policy.provider_entitlements.items())),
        "licensing_rules": sorted(data_access_policy.allowed_licensing_policies),
        "research_depth": execution_policy.research_depth,
        "adaptive_macro_factors": sorted(set(adaptive_macro_factors)),
        "market": data_access_policy.market,
        "jurisdiction": data_access_policy.jurisdiction,
        "data_cutoff": data_access_policy.data_cutoff,
        "permitted_model_roles": sorted(execution_policy.permitted_model_roles),
        "prompt_version": prompt_version,
        "source_registry_version": source_registry_version,
        "execution_policy_version": execution_policy.policy_version,
        "data_access_policy_version": data_access_policy.policy_version,
    }
    return SourcePolicyFingerprint(
        fingerprint=_stable_hash(payload),
        enabled_source_families=payload["enabled_source_families"],
        provider_entitlements=payload["provider_entitlements"],
        licensing_rules=payload["licensing_rules"],
        research_depth=execution_policy.research_depth,
        adaptive_macro_factors=payload["adaptive_macro_factors"],
        market=data_access_policy.market,
        jurisdiction=data_access_policy.jurisdiction,
        data_cutoff=data_access_policy.data_cutoff,
        permitted_model_roles=payload["permitted_model_roles"],
        prompt_version=prompt_version,
        source_registry_version=source_registry_version,
    )


def build_evidence_graph(
    ticker: str,
    admitted_evidence: Iterable[dict[str, Any]],
    admission_decisions: Iterable[EvidenceAdmissionDecision],
    fingerprint: SourcePolicyFingerprint,
) -> EvidenceGraph:
    ticker = ticker.upper()
    decisions = list(admission_decisions)
    nodes = [EvidenceNode(
        node_id=f"company:{ticker}",
        node_type="company",
        entity=ticker,
        statement=f"Company and security identity for {ticker}",
        source_policy_admission_state="admitted",
        no_lookahead_status="eligible",
        confidence="High",
        validation_state="validated",
    )]
    relationships: list[EvidenceRelationship] = []
    admitted_ids: list[str] = []
    for item in admitted_evidence:
        evidence_id = str(item.get("evidence_id") or item.get("source_id") or _stable_hash(item)[:20])
        admitted_ids.append(evidence_id)
        statement = str(
            item.get("statement")
            or item.get("claim")
            or item.get("metric_or_claim")
            or item.get("summary")
            or item.get("exact_excerpt")
            or item.get("title")
            or "Validated evidence"
        )
        source_url = str(item.get("url") or item.get("source_url") or "")
        stance = str(item.get("stance") or item.get("direction") or "supports").casefold()
        relationship_type = "contradicts" if "contrad" in stance or stance == "negative" else "supports"
        nodes.append(EvidenceNode(
            node_id=evidence_id,
            node_type=str(item.get("kind") or item.get("source_type") or "evidence"),
            entity=str(item.get("entity") or item.get("entity_name") or ticker),
            statement=statement[:1600],
            source=str(item.get("source") or item.get("provider") or item.get("source_type") or ""),
            citation=source_url,
            publication_timestamp=str(item.get("publication_date") or ""),
            event_timestamp=str(item.get("event_date") or item.get("event_timestamp") or ""),
            retrieval_timestamp=str(item.get("retrieved_at") or item.get("observed_at") or ""),
            reporting_period=str(item.get("period") or item.get("reporting_period") or ""),
            metric=str(item.get("metric") or item.get("metric_or_claim") or ""),
            value=item.get("value"),
            unit=str(item.get("unit") or ""),
            currency=str(item.get("currency") or ""),
            licensing_classification=str(item.get("licensing_policy") or item.get("licensing_status") or ""),
            provider=str(item.get("provider") or item.get("origin_group") or ""),
            freshness=_freshness(str(item.get("publication_date") or item.get("observed_at") or "")),
            source_policy_admission_state="admitted",
            no_lookahead_status="eligible",
            confidence=str(item.get("confidence") or "Unknown"),
            validation_state=str(item.get("validation_status") or item.get("status") or "validated"),
        ))
        relationships.append(EvidenceRelationship(
            relationship_id=_stable_hash((evidence_id, ticker, relationship_type))[:20],
            source_node_id=evidence_id,
            target_node_id=f"company:{ticker}",
            relationship_type=relationship_type,
            confidence=str(item.get("confidence") or "Unknown"),
        ))
    excluded_ids = [
        item.evidence_id for item in decisions if item.status != "admitted"
    ]
    evidence_hash = _stable_hash({
        "ticker": ticker,
        "admitted_ids": sorted(admitted_ids),
        "fingerprint": fingerprint.fingerprint,
    })
    return EvidenceGraph(
        ticker=ticker,
        graph_version=EVIDENCE_GRAPH_VERSION,
        evidence_hash=evidence_hash,
        source_policy_fingerprint=fingerprint.fingerprint,
        nodes=nodes,
        relationships=relationships,
        admission_decisions=decisions,
        admitted_evidence_ids=sorted(admitted_ids),
        excluded_evidence_ids=sorted(excluded_ids),
        generated_at=_utc_now(),
    )


def build_adaptive_macro_plan(
    ticker: str,
    *,
    company_name: str = "",
    sic_description: str = "",
    exchange: str = "",
    adr_or_fpi: bool = False,
    evidence: Any = None,
) -> AdaptiveMacroResearchPlan:
    text = f"{ticker} {company_name} {sic_description} {exchange}".casefold()
    candidates: list[tuple[str, int, int, str, str, str]] = []
    if any(token in text for token in ("bank", "financial", "insurance", "credit")):
        candidates.extend([
            ("Rates and yield curve", 5, 5, "net interest income, funding cost, and valuation", "0-18 months", "direct"),
            ("Liquidity and financial conditions", 5, 5, "credit growth, losses, and capital availability", "0-18 months", "direct"),
            ("Economic growth", 4, 5, "loan demand and credit quality", "1-8 quarters", "directional"),
        ])
    elif any(token in text for token in ("oil", "gas", "energy", "petroleum", "mining", "metal")):
        candidates.extend([
            ("Commodity prices", 5, 5, "realized pricing, revenue, margins, and free cash flow", "0-8 quarters", "direct"),
            ("USD", 4, 4, "reported revenue and globally priced input/output economics", "0-8 quarters", "directional"),
            ("Economic growth", 4, 4, "industrial and end-market demand", "1-8 quarters", "directional"),
        ])
    elif any(token in text for token in ("reit", "real estate", "property")):
        candidates.extend([
            ("Rates and yield curve", 5, 5, "capitalization rates, financing cost, and valuation", "0-18 months", "direct"),
            ("Liquidity and financial conditions", 5, 5, "refinancing capacity and transaction activity", "0-18 months", "direct"),
            ("Labor conditions", 4, 4, "occupancy, rent growth, and tenant demand", "1-8 quarters", "directional"),
        ])
    elif any(token in text for token in ("semiconductor", "technology", "software", "computer", "electronics")):
        candidates.extend([
            ("Economic growth", 4, 4, "enterprise and consumer demand", "1-8 quarters", "directional"),
            ("USD", 4, 4, "reported international revenue and pricing", "0-8 quarters", "directional"),
            ("Relevant global risks", 5, 5, "supply chain, trade restrictions, and addressable market", "0-24 months", "direct"),
        ])
    else:
        candidates.extend([
            ("Economic growth", 4, 4, "revenue demand and operating leverage", "1-8 quarters", "directional"),
            ("Inflation", 4, 4, "pricing, input costs, and margins", "0-8 quarters", "directional"),
            ("Rates and yield curve", 3, 4, "financing cost and valuation", "0-18 months", "directional"),
        ])
    if adr_or_fpi:
        candidates.extend([
            ("USD", 5, 5, "reporting-currency translation and ADR valuation", "0-8 quarters", "direct"),
            ("Relevant global risks", 5, 5, "home-market regulation, trade, and capital access", "0-24 months", "direct"),
        ])
    deduped: dict[str, tuple[str, int, int, str, str, str]] = {}
    for item in candidates:
        prior = deduped.get(item[0])
        if prior is None or (item[1] + item[2]) > (prior[1] + prior[2]):
            deduped[item[0]] = item
    ranked = sorted(deduped.values(), key=lambda item: (item[1] + item[2], item[1]), reverse=True)[:3]
    evidence_ids = _collect_evidence_ids(evidence)[:12]
    exposures = [CompanyMacroExposure(
        exposure_id=_stable_hash((ticker.upper(), item[0]))[:20],
        factor=item[0],
        materiality=item[1],
        causal_relevance=item[2],
        affected_business_driver=item[3],
        time_horizon=item[4],
        current_signal="Requires current admitted macro evidence",
        direction=item[5],
        rationale=f"Selected for {ticker.upper()} because it can affect {item[3]}.",
        evidence_ids=evidence_ids,
    ) for item in ranked]
    selected_names = {item.factor for item in exposures}
    return AdaptiveMacroResearchPlan(
        ticker=ticker.upper(),
        status="Available" if exposures else "Unavailable",
        baseline_factors=list(BASELINE_MACRO_FACTORS),
        selected_exposures=exposures,
        excluded_factors=[item for item in BASELINE_MACRO_FACTORS if item not in selected_names],
        selection_policy=(
            "Rank company exposures by materiality and causal relevance; show at most three. "
            "Macro context cannot independently create or promote a thesis."
        ),
        data_gaps=([] if evidence_ids else [
            "Current macro evidence was not citation-bound; selected exposures remain directional until retrieval completes."
        ]),
    )


def build_research_ai_execution_plan(
    execution_policy: ResearchExecutionPolicy,
    *,
    primary_provider: Any = None,
    auditor_provider: Any = None,
    primary_credential_source: str = "",
    auditor_credential_source: str = "",
) -> ResearchAiExecutionPlan:
    primary_ready = primary_provider is not None
    auditor_ready = auditor_provider is not None
    primary_label, primary_model = _provider_identity(primary_provider)
    auditor_label, auditor_model = _provider_identity(auditor_provider)
    primary = PrimaryModelReadiness(
        status="ready" if primary_ready else "deterministic_fallback",
        provider=primary_label,
        model=primary_model,
        credential_source=primary_credential_source,
        message=(
            "Primary AI is ready."
            if primary_ready else
            "No primary model is available; deterministic research remains available."
        ),
    )
    auditor = IndependentAuditReadiness(
        status="ready" if auditor_ready else "optional_not_configured",
        provider=auditor_label,
        model=auditor_model,
        credential_source=auditor_credential_source,
        optional=True,
        message=(
            "Independent Research QA is ready."
            if auditor_ready else
            "Independent audit is optional and is not configured."
        ),
    )
    assignments = [
        _assignment("deterministic_bootstrap", None, deterministic=True),
        _assignment("research_planner", primary_provider),
        _assignment("evidence_collector", None, deterministic=True),
        _assignment("primary_investment_analyst", primary_provider),
        _assignment("independent_research_auditor", auditor_provider, required=False),
        _assignment("adversarial_evidence_scout", auditor_provider, required=False),
        _assignment("evidence_gap_resolver", None, deterministic=True),
        _assignment("closure_checker", auditor_provider, required=False),
        _assignment("investment_committee_writer", primary_provider),
        _assignment("deterministic_scoring_and_promotion", None, deterministic=True),
    ]
    for role in execution_policy.specialist_roles:
        assignments.insert(-1, _assignment(role, primary_provider, required=False))
    status = (
        "ready" if primary_ready and auditor_ready else
        "primary_ready_audit_optional" if primary_ready else
        "deterministic_fallback"
    )
    expected_calls = 0 if not primary_ready else 4 + (3 if auditor_ready else 0)
    return ResearchAiExecutionPlan(
        status=status,
        assignments=assignments,
        primary_readiness=primary,
        independent_audit_readiness=auditor,
        expected_model_calls=expected_calls,
        expected_retrieval_passes=execution_policy.retrieval_pass_limit,
        expected_gap_loops=execution_policy.evidence_gap_loop_limit if auditor_ready else 0,
        deterministic_fallback=not primary_ready,
    )


def build_research_setup_preview(
    execution_policy: ResearchExecutionPolicy,
    ai_plan: ResearchAiExecutionPlan,
    macro_plan: AdaptiveMacroResearchPlan,
    evidence_graph: EvidenceGraph,
    *,
    cache_hit: bool = False,
) -> ResearchSetupPreview:
    primary_status = (
        ai_plan.primary_readiness.message if ai_plan.primary_readiness else "Unknown"
    )
    audit_status = (
        ai_plan.independent_audit_readiness.message
        if ai_plan.independent_audit_readiness else "Unknown"
    )
    return ResearchSetupPreview(
        depth=execution_policy.research_depth,
        data_status="Best available",
        source_count=len(evidence_graph.admitted_evidence_ids),
        macro_status="Adaptive" if macro_plan.status == "Available" else macro_plan.status,
        primary_ai_status=primary_status,
        independent_qa_status=audit_status,
        expected_execution="Cached result" if cache_hit else "New run",
        expected_stages=[
            "Deterministic bootstrap",
            "Primary planning and analysis",
            "Independent research audit",
            "Targeted evidence-gap retrieval",
            "Final IC memo and thesis monitor",
        ],
        excluded_evidence_count=len(evidence_graph.excluded_evidence_ids),
    )


def build_independent_research_qa_run(
    hybrid_run: HybridResearchRun | None,
    *,
    evidence_graph: EvidenceGraph,
    execution_plan: ResearchAiExecutionPlan,
    setup_preview: ResearchSetupPreview,
) -> IndependentResearchQaRun:
    generated_at = hybrid_run.generated_at if hybrid_run else _utc_now()
    result = IndependentResearchQaRun(
        run_id=hybrid_run.run_id if hybrid_run else str(uuid.uuid4()),
        ticker=evidence_graph.ticker,
        status=hybrid_run.status if hybrid_run else "deterministic_fallback",
        generated_at=generated_at,
        completed_at=hybrid_run.completed_at if hybrid_run else _utc_now(),
        evidence_hash=evidence_graph.evidence_hash,
        source_policy_fingerprint=evidence_graph.source_policy_fingerprint,
        execution_plan=execution_plan,
        setup_preview=setup_preview,
        evidence_graph=evidence_graph,
        final_artifact=hybrid_run.final_artifact if hybrid_run else None,
    )
    if hybrid_run is None:
        result.qa_report = _qa_report(result, audit_completed=False)
        result.data_gaps.append("Independent Research QA not completed: no primary model run was requested.")
        return result
    result.model_executions = [_model_record(item, hybrid_run.evidence_hash) for item in hybrid_run.rounds]
    result.hypotheses = _hypotheses_from_run(hybrid_run)
    result.audit_findings = _audit_findings_from_run(hybrid_run)
    result.research_requests = _research_requests_from_run(hybrid_run)
    result.evidence_requests = [
        item.to_evidence_request() for item in result.research_requests
    ]
    result.candidate_discovery_items = _candidate_discovery_from_run(hybrid_run)
    result.evidence_gaps = _evidence_gaps(result.audit_findings, result.evidence_requests, hybrid_run)
    result.audit_resolutions = _audit_resolutions(result.audit_findings, result.evidence_gaps)
    result.primary_audit_responses = _primary_audit_responses(
        result.audit_findings,
        result.audit_resolutions,
        hybrid_run,
    )
    result.conclusion_revisions = _conclusion_revisions(hybrid_run, result.audit_resolutions)
    result.unresolved_risks = [
        UnresolvedResearchRisk(
            risk_id=_stable_hash((item.gap_id, item.summary))[:20],
            summary=item.summary,
            severity=item.severity,
            affected_conclusions=list(item.affected_claim_ids),
            required_evidence=list(item.evidence_request_ids),
            confidence_impact="Reduce confidence until the evidence gap is closed.",
        )
        for item in result.evidence_gaps if item.status == "unresolved"
    ]
    audit_completed = any(
        item.role == "independent_research_auditor" and item.status == "completed"
        for item in result.model_executions
    )
    qa_relationship = str(
        hybrid_run.generation_provenance.get("qa_relationship")
        or "independent"
    )
    independent_audit_completed = (
        audit_completed and qa_relationship == "independent"
    )
    recorded_depth = str(
        hybrid_run.generation_provenance.get(
            "investigation_cycles_completed", ""
        )
    )
    result.recursion_depth = (
        int(recorded_depth)
        if recorded_depth.isdigit()
        else min(
            setup_preview.expected_stages.count(
                "Targeted evidence-gap retrieval"
            ) + int(bool(hybrid_run.retrieval_lifecycles)),
            max(0, execution_plan.expected_gap_loops),
        )
    )
    result.estimated_cost_usd = (
        hybrid_run.usage_summary.provider_cost_usd
        if hybrid_run.usage_summary else None
    )
    result.stopping_reason = str(
        hybrid_run.generation_provenance.get("stopping_reason")
        or _stopping_reason(result, audit_completed=audit_completed)
    )
    result.closure_result = _closure_result(
        result,
        audit_completed=audit_completed,
        hybrid_run=hybrid_run,
    )
    result.qa_report = _qa_report(
        result,
        audit_completed=independent_audit_completed,
    )
    if not audit_completed:
        result.data_gaps.append("Independent Research QA not completed.")
    elif not independent_audit_completed:
        result.data_gaps.append(
            "A same-model second pass completed; it is not labeled as independent Research QA."
        )
    result.data_gaps.extend(hybrid_run.data_gaps)
    return result


def _candidate_discovery_from_run(
    run: HybridResearchRun,
) -> list[CandidateDiscoveryItem]:
    decisions = {
        item.candidate_id: item for item in run.source_admission_decisions
    }
    result: list[CandidateDiscoveryItem] = []
    for item in run.source_candidates:
        decision = decisions.get(item.candidate_id)
        decision_status = str(getattr(decision, "status", "") or "")
        if decision_status == "admitted" and getattr(decision, "evidence_id", ""):
            status = CandidateStatus.ADMITTED.value
            decision_id = str(getattr(decision, "decision_id", "") or item.candidate_id)
            evidence_ids = (str(decision.evidence_id),)
        elif decision_status == "rejected":
            status = CandidateStatus.REJECTED.value
            decision_id = str(getattr(decision, "decision_id", "") or item.candidate_id)
            evidence_ids = ()
        else:
            status = CandidateStatus.QUARANTINED.value
            decision_id = ""
            evidence_ids = ()
        result.append(CandidateDiscoveryItem(
            candidate_id=item.candidate_id,
            candidate_type=(
                item.candidate_type
                if item.candidate_type in {value.value for value in CandidateType}
                else CandidateType.SOURCE.value
            ),
            status=status,
            ticker=item.ticker,
            objective=item.claim_to_test or item.claimed_relevance or item.title,
            origin_role="adversarial_evidence_scout",
            origin_provider=item.discovery_provider,
            research_request_id=item.research_request_id,
            url=item.url,
            title=item.title,
            snippet=item.excerpt,
            proposed_source_family=item.proposed_source_family,
            proposed_mapping=item.proposed_mapping,
            company_or_expert=item.company_or_expert,
            materiality=item.materiality,
            discovered_at=run.generated_at,
            status_history=(CandidateStatusEvent(
                status=status,
                changed_at=(
                    str(getattr(decision, "decided_at", "") or run.completed_at)
                ),
                reason=(
                    "; ".join(getattr(decision, "reasons", []) or [])
                    or "Candidate remains quarantined pending registered retrieval and admission."
                ),
            ),),
            connector_id=item.discovery_method,
            admission_decision_id=decision_id,
            admitted_evidence_ids=evidence_ids,
        ))
    return result


def _primary_audit_responses(
    findings: list[AuditFinding],
    resolutions: list[AuditResolution],
    run: HybridResearchRun,
) -> list[PrimaryAuditResponse]:
    resolutions_by_id = {item.finding_id: item for item in resolutions}
    revision = next(
        (
            item.artifact for item in run.rounds
            if item.role == "primary_revision" and item.artifact
        ),
        None,
    )
    raw_responses = list(
        (revision.payload if revision else {}).get("primary_responses") or []
    )
    model_responses = {
        str(item.get("finding_id") or ""): item
        for item in raw_responses
        if isinstance(item, dict) and item.get("finding_id")
    }
    responses: list[PrimaryAuditResponse] = []
    for finding in findings:
        resolution = resolutions_by_id.get(finding.finding_id)
        raw = model_responses.get(finding.finding_id, {})
        evidence_ids = [
            str(value) for value in raw.get("evidence_ids", []) if value
        ] or list(getattr(resolution, "evidence_ids", []) or [])
        requested_state = str(raw.get("state") or raw.get("status") or "")
        if requested_state == "rejected_with_evidence" and not evidence_ids:
            state = "unresolved"
        elif requested_state == "accepted" and not (
            bool(raw.get("conclusion_changed"))
            or bool(resolution and resolution.conclusion_changed)
        ):
            state = "unresolved"
        elif requested_state in {
            "accepted", "rejected_with_evidence", "unresolved"
        }:
            state = requested_state
        elif resolution and resolution.conclusion_changed:
            state = "accepted"
        elif resolution and resolution.status == "resolved" and evidence_ids:
            state = "rejected_with_evidence"
        else:
            state = "unresolved"
        response = PrimaryAuditResponse(
            finding_id=finding.finding_id,
            state=state,
            explanation=(
                str(raw.get("explanation") or "")
                or (
                    resolution.resolution
                    if resolution else
                    "The primary conclusion has not yet addressed this finding."
                )
            ),
            evidence_ids=evidence_ids,
            conclusion_changed=(
                bool(raw.get("conclusion_changed"))
                or bool(resolution and resolution.conclusion_changed)
            ),
        )
        finding.primary_response_state = response.state
        responses.append(response)
    return responses


def _closure_result(
    run: IndependentResearchQaRun,
    *,
    audit_completed: bool,
    hybrid_run: HybridResearchRun,
) -> ClosureAuditResult:
    if not audit_completed:
        return ClosureAuditResult(
            state="indeterminate_provider_or_evidence_failure",
            summary="Independent QA did not complete; no closure pass is asserted.",
        )
    material = {
        item.finding_id for item in run.audit_findings
        if item.severity.casefold() in {"high", "critical", "material"}
    }
    response_by_id = {
        item.finding_id: item for item in run.primary_audit_responses
    }
    unaddressed = sorted(
        finding_id for finding_id in material
        if finding_id not in response_by_id
        or response_by_id[finding_id].state == "unresolved"
    )
    contrary = sorted({
        evidence_id
        for hypothesis in run.hypotheses
        for evidence_id in hypothesis.contradicting_evidence_ids
        if evidence_id
    })
    addressed_ids = {
        evidence_id
        for response in run.primary_audit_responses
        for evidence_id in response.evidence_ids
    }
    ignored_contrary = sorted(set(contrary) - addressed_ids)
    unresolved = any(item.state == "unresolved" for item in run.primary_audit_responses)
    closure_round = next(
        (
            item for item in hybrid_run.rounds
            if item.role == "closure_checker" and item.artifact
        ),
        None,
    )
    closure_payload = dict(closure_round.artifact.payload) if closure_round and closure_round.artifact else {}
    model_state = str(closure_payload.get("closure_state") or "")
    audit_decision = closure_payload.get("audit_decision")
    audit_status = str(
        audit_decision.get("status")
        if isinstance(audit_decision, dict) else ""
    )
    model_unaddressed = [
        str(value) for value in closure_payload.get("unaddressed_material_finding_ids", []) if value
    ]
    unaddressed = sorted(set(unaddressed + model_unaddressed))
    if audit_status == "failed" and not unaddressed:
        unaddressed = sorted(material) or ["closure_checker_reported_failure"]
    if unaddressed or ignored_contrary:
        state = "failed_material_findings_unaddressed"
        summary = "Closure failed because material findings or contrary evidence were not addressed."
    elif model_state == "indeterminate_provider_or_evidence_failure":
        state = model_state
        summary = str(closure_payload.get("summary") or "Closure was indeterminate because a required provider or evidence step failed.")
    elif unresolved:
        state = "closed_with_residual_risk"
        summary = "Material findings were addressed, with lower-severity residual risks retained."
    else:
        state = "closed"
        summary = "All recorded audit findings and contrary evidence were addressed."
    return ClosureAuditResult(
        state=state,
        summary=summary,
        checked_finding_ids=[item.finding_id for item in run.audit_findings],
        unaddressed_material_finding_ids=unaddressed,
        contrary_evidence_ids=contrary,
        ignored_contrary_evidence_ids=ignored_contrary,
        provider=str(getattr(closure_round, "provider", "") or ""),
        model=str(getattr(closure_round, "model", "") or ""),
        completed_at=run.completed_at,
    )


def _stopping_reason(
    run: IndependentResearchQaRun,
    *,
    audit_completed: bool,
) -> str:
    if not audit_completed:
        return "auditor_unavailable_or_failed"
    if run.audit_findings and not run.evidence_requests:
        return "evidence_unavailable_or_not_requested"
    if run.evidence_requests and not any(
        item.status in {"closed", "partially_resolved"}
        for item in run.evidence_gaps
    ):
        return "no_new_admitted_evidence"
    if any(item.status == "unresolved" for item in run.evidence_gaps):
        return "configured_depth_or_evidence_limit"
    return "all_material_findings_resolved"


def _qa_report(
    run: IndependentResearchQaRun,
    *,
    audit_completed: bool,
) -> ResearchQaReport:
    graph = run.evidence_graph
    admitted = len(graph.admitted_evidence_ids) if graph else 0
    evidence_nodes = [item for item in (graph.nodes if graph else []) if item.node_type != "company"]
    traceable = sum(1 for item in evidence_nodes if item.citation or item.source)
    contradictory = sum(
        1 for item in (graph.relationships if graph else [])
        if item.relationship_type == "contradicts"
    )
    unresolved = sum(1 for item in run.evidence_gaps if item.status == "unresolved")
    closed = sum(1 for item in run.evidence_gaps if item.status == "closed")
    partial = sum(1 for item in run.evidence_gaps if item.status == "partially_resolved")
    writer_completed = any(
        item.role == "investment_committee_writer" and item.status == "completed"
        for item in run.model_executions
    )
    names_and_scores = [
        ("Source Coverage", min(100, 30 + admitted * 7), "Validated admitted evidence breadth."),
        ("Evidence Traceability", int(100 * traceable / max(1, len(evidence_nodes))), "Evidence with a source or citation."),
        ("Contradictory Evidence Coverage", min(100, 45 + contradictory * 20) if audit_completed else 30, "Coverage of evidence that challenges the leading thesis."),
        ("Hypothesis Diversity", min(100, 40 + len(run.hypotheses) * 20), "Leading and alternative hypotheses explicitly represented."),
        ("Causal Reasoning Quality", 90 if writer_completed else 60 if run.final_artifact else 35, "Evidence-bound synthesis completion."),
        ("Accounting and Numerical Consistency", 90 if run.status == "completed" else 65, "Deterministic validation status of the production workflow."),
        ("Valuation Support", 85 if _payload_contains(run.final_artifact, "valuation") else 55, "Valuation assumptions in the final artifact."),
        ("Market-Capture Support", 85 if _payload_contains(run.final_artifact, "market") else 55, "Market-capture evidence in the final artifact."),
        ("Catalyst Specificity", 85 if _payload_contains(run.final_artifact, "catalyst") else 55, "Specific catalysts represented in synthesis."),
        ("Monitoring Readiness", 85 if _payload_contains(run.final_artifact, "monitor") else 55, "Confirmation and thesis-break monitoring criteria."),
        ("Audit Completion", 100 if audit_completed else 0, "Independent audit execution status."),
        ("Unresolved Evidence Risk", max(0, 100 - unresolved * 20 - partial * 8), "Penalty for unresolved and partially resolved gaps."),
    ]
    dimensions: list[ResearchQaDimension] = []
    deductions: list[str] = []
    weight = 1.0 / len(names_and_scores)
    for name, raw_score, explanation in names_and_scores:
        score = max(0, min(100, int(raw_score)))
        dimension_deductions: list[str] = []
        if score < 80:
            message = f"{name} scored {score}/100: {explanation}"
            dimension_deductions.append(message)
            deductions.append(message)
        dimensions.append(ResearchQaDimension(
            name=name,
            score=score,
            weight=weight,
            status="Complete" if score >= 80 else "Needs attention",
            explanation=explanation,
            deductions=dimension_deductions,
        ))
    total = round(sum(item.score * item.weight for item in dimensions))
    scorecard = ResearchQaScore(
        total_score=total,
        status="Complete" if audit_completed and unresolved == 0 else "Partial",
        dimensions=dimensions,
        deductions=deductions,
    )
    findings = run.audit_findings
    unsupported = sum(1 for item in findings if "unsupported" in item.category.casefold())
    alternatives = max(
        0,
        len([item for item in run.hypotheses if item.status != "leading"]),
    )
    market_challenged = any("market" in item.summary.casefold() for item in findings)
    return ResearchQaReport(
        ticker=run.ticker,
        status=scorecard.status,
        score=scorecard,
        primary_thesis_challenged=audit_completed and bool(findings),
        alternative_hypotheses_explored=alternatives,
        unsupported_claims_removed=unsupported,
        evidence_gaps_identified=len(run.evidence_gaps),
        evidence_gaps_closed=closed,
        evidence_gaps_partially_resolved=partial,
        evidence_gaps_unresolved=unresolved,
        additional_validated_sources=closed + partial,
        market_capture_challenged=market_challenged,
        confidence_before="Medium" if run.final_artifact else "Low",
        confidence_after="Medium-High" if total >= 85 and unresolved == 0 else "Medium" if total >= 65 else "Low",
        summary=(
            "Research-process quality score; it does not assert that the investment conclusion is objectively correct."
        ),
    )


def _hypotheses_from_run(run: HybridResearchRun) -> list[ResearchHypothesis]:
    planner = next((item.artifact for item in run.rounds if item.role == "research_planner" and item.artifact), None)
    payload = dict(planner.payload if planner else {})
    values = payload.get("hypotheses") or payload.get("competing_hypotheses") or [
        *list(payload.get("constructive_hypotheses") or []),
        *list(payload.get("adverse_hypotheses") or []),
    ]
    if isinstance(values, dict):
        values = list(values.values())
    result: list[ResearchHypothesis] = []
    for index, item in enumerate(values if isinstance(values, list) else []):
        if isinstance(item, dict):
            statement = str(item.get("statement") or item.get("hypothesis") or item.get("summary") or "")
            status = str(item.get("status") or ("leading" if index == 0 else "alternative"))
            confirm = list(item.get("confirming_evidence_ids") or item.get("evidence_ids") or [])
            contradict = list(item.get("contradicting_evidence_ids") or [])
            falsification = str(item.get("falsification_test") or item.get("what_would_falsify") or "")
        else:
            statement = str(item)
            status = "leading" if index == 0 else "alternative"
            confirm, contradict, falsification = [], [], ""
        if statement:
            result.append(ResearchHypothesis(
                hypothesis_id=_stable_hash((run.run_id, index, statement))[:20],
                statement=statement,
                status=status,
                confirming_evidence_ids=confirm,
                contradicting_evidence_ids=contradict,
                falsification_test=falsification,
            ))
    return result


def _audit_findings_from_run(run: HybridResearchRun) -> list[AuditFinding]:
    result = []
    for item in run.audit_findings:
        request_ids = list(getattr(item, "evidence_request_ids", []) or [])
        result.append(AuditFinding(
            finding_id=item.finding_id,
            category=item.category,
            severity=item.severity,
            summary=item.summary,
            status="resolved" if item.resolved else "open",
            evidence_ids=list(item.evidence_ids),
            evidence_request_ids=request_ids,
            affected_claim_ids=list(getattr(item, "affected_claim_ids", []) or []),
            proposed_change="Retrieve and validate targeted evidence before revising the affected conclusion.",
            frozen_evidence_hash=run.evidence_hash,
            materiality=item.severity,
            what_would_change_assessment=(
                getattr(item, "what_would_change_assessment", "")
                or "Admitted confirming or contradicting evidence from the requested source families."
            ),
        ))
    return result


def _evidence_requests_from_run(run: HybridResearchRun) -> list[EvidenceRequest]:
    return [
        item.to_evidence_request()
        for item in _research_requests_from_run(run)
    ]


def _research_requests_from_run(run: HybridResearchRun) -> list[ResearchRequest]:
    audit = next((item.artifact for item in run.rounds if item.role == "independent_research_auditor" and item.artifact), None)
    raw = list((audit.payload if audit else {}).get("source_requests") or [])
    result: list[ResearchRequest] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        request_id = str(item.get("request_id") or _stable_hash((run.run_id, "request", index, item))[:20])
        source_types = item.get("required_source_families") or item.get("source_types") or [item.get("source_type")]
        attempts = [
            value for value in run.retrieval_lifecycles
            if value.request_id == request_id
        ]
        candidate_ids = [
            value.candidate_id for value in run.source_candidates
            if value.research_request_id == request_id
        ]
        admitted_ids = [
            value.evidence_id for value in run.source_admission_decisions
            if value.candidate_id in set(candidate_ids)
            and value.status == "admitted"
            and value.evidence_id
        ]
        request_status = (
            "resolved" if admitted_ids
            else "attempted" if attempts
            else "planned"
        )
        result.append(ResearchRequest(
            request_id=request_id,
            objective=str(item.get("objective") or item.get("question") or item.get("title") or item.get("reason") or "Targeted evidence request"),
            requested_by_role="independent_research_auditor",
            required_source_families=[str(value) for value in source_types if value],
            desired_confirming_evidence=[
                str(value) for value in item.get("desired_confirming_evidence", []) if value
            ],
            desired_contradicting_evidence=[
                str(value) for value in item.get("desired_contradicting_evidence", []) if value
            ],
            scope={
                **dict(item.get("scope") or {}),
                "required_fields": [
                    str(value) for value in item.get("required_fields", []) if value
                ],
            },
            priority=str(item.get("priority") or "High"),
            materiality=str(item.get("materiality") or item.get("priority") or "High"),
            affected_claim_ids=[str(value) for value in item.get("affected_claim_ids", []) if value],
            affected_finding_ids=[str(value) for value in item.get("affected_finding_ids", []) if value],
            preferred_connectors=[str(value) for value in item.get("preferred_connectors", []) if value],
            stopping_rule=str(item.get("stopping_rule") or "Stop when sufficient confirming and contradicting evidence is admitted or unavailable."),
            attempted_connector_ids=list(dict.fromkeys(
                str(value.source_type or value.source_id)
                for value in attempts
                if value.source_type or value.source_id
            )),
            candidate_ids=candidate_ids,
            admitted_evidence_ids=admitted_ids,
            status=request_status,
            resolution_summary=(
                f"{len(admitted_ids)} candidate evidence item(s) admitted."
                if admitted_ids else
                "Registered connector attempts completed without candidate evidence admission."
                if attempts else "Awaiting registered retrieval."
            ),
        ))
    return result


def _evidence_gaps(
    findings: list[AuditFinding],
    requests: list[EvidenceRequest],
    run: HybridResearchRun,
) -> list[EvidenceGap]:
    outcomes = {
        item.request_id: item for item in run.retrieval_lifecycles
    }
    result: list[EvidenceGap] = []
    for index, request in enumerate(requests):
        outcome = outcomes.get(request.request_id)
        lifecycle_status = str(getattr(outcome, "status", "") or "").casefold()
        if lifecycle_status in {"completed", "validated", "available", "success"}:
            status = "closed"
        elif lifecycle_status in {"partial", "metadata_only"}:
            status = "partially_resolved"
        else:
            status = "unresolved"
        related = [
            item for item in findings
            if request.request_id in item.evidence_request_ids
            or set(request.source_types) & set(item.evidence_request_ids)
        ]
        result.append(EvidenceGap(
            gap_id=_stable_hash((run.run_id, "gap", index, request.request_id))[:20],
            summary=request.question,
            severity=related[0].severity if related else request.priority,
            status=status,
            evidence_request_ids=[request.request_id],
            affected_claim_ids=list(request.affected_claim_ids),
            resolution_evidence_ids=[],
        ))
    for item in findings:
        if any(item.finding_id in gap.affected_claim_ids for gap in result):
            continue
        if not item.evidence_request_ids and item.status != "resolved":
            result.append(EvidenceGap(
                gap_id=_stable_hash((run.run_id, item.finding_id))[:20],
                summary=item.summary,
                severity=item.severity,
                status="unresolved",
                affected_claim_ids=list(item.affected_claim_ids),
            ))
    return result


def _audit_resolutions(
    findings: list[AuditFinding],
    gaps: list[EvidenceGap],
) -> list[AuditResolution]:
    unresolved = any(item.status == "unresolved" for item in gaps)
    return [AuditResolution(
        finding_id=item.finding_id,
        status="unresolved" if unresolved and item.status != "resolved" else "resolved",
        resolution=(
            "Targeted retrieval remains incomplete; the finding is retained as an open risk."
            if unresolved and item.status != "resolved" else
            "The finding was addressed using admitted evidence."
        ),
        evidence_ids=list(item.evidence_ids),
        conclusion_changed=item.status == "resolved",
    ) for item in findings]


def _conclusion_revisions(
    run: HybridResearchRun,
    resolutions: list[AuditResolution],
) -> list[ConclusionRevision]:
    return [ConclusionRevision(
        revision_id=_stable_hash((run.run_id, item.finding_id, "revision"))[:20],
        conclusion_area="Independent audit finding",
        before="Primary analyst conclusion",
        after="Revised using admitted evidence" if item.conclusion_changed else "Retained with an explicit unresolved caveat",
        reason=item.resolution,
        evidence_ids=list(item.evidence_ids),
        materiality="High" if item.status == "unresolved" else "Medium",
    ) for item in resolutions]


def _model_record(item: Any, evidence_hash: str) -> ModelExecutionRecord:
    return ModelExecutionRecord(
        role=str(item.role),
        provider=str(item.provider),
        model=str(item.model),
        status=str(item.status),
        started_at=str(item.started_at),
        completed_at=str(item.completed_at),
        latency_ms=item.latency_ms,
        prompt_hash=str(item.prompt_hash),
        evidence_hash=evidence_hash,
        token_usage=dict(item.token_usage),
        provider_cost_usd=item.provider_cost_usd,
        failure_class="" if item.status == "completed" else str(item.status),
        message=str(item.message),
    )


def _assignment(
    role: str,
    provider: Any,
    *,
    required: bool = True,
    deterministic: bool = False,
) -> ResearchRoleAssignment:
    provider_label, model = _provider_identity(provider)
    return ResearchRoleAssignment(
        role=role,
        provider="deterministic" if deterministic else provider_label,
        model="" if deterministic else model,
        status="ready" if deterministic or provider is not None else "optional_not_configured" if not required else "unavailable",
        required=required,
        deterministic=deterministic,
        message=(
            "Deterministic stage."
            if deterministic else
            "Model role is ready."
            if provider is not None else
            "Optional model role is not configured."
            if not required else
            "Required model role is unavailable."
        ),
    )


def _provider_identity(provider: Any) -> tuple[str, str]:
    if provider is None:
        return "", ""
    return (
        str(getattr(provider, "provider_name", "") or type(provider).__name__),
        str(getattr(provider, "model", "") or ""),
    )


def _source_family(item: dict[str, Any]) -> str:
    explicit = str(item.get("source_family") or "")
    if explicit:
        return explicit
    source_type = str(item.get("source_type") or item.get("kind") or "")
    return _SOURCE_FAMILY_BY_TYPE.get(source_type, source_type or "unknown")


def _freshness(value: str) -> str:
    if not value:
        return "Unknown"
    try:
        observed = datetime.fromisoformat(value[:10]).replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - observed).days
    except (TypeError, ValueError):
        return "Unknown"
    if age <= 7:
        return "Current"
    if age <= 90:
        return "Recent"
    if age <= 365:
        return "Aging"
    return "Stale"


def _collect_evidence_ids(value: Any) -> list[str]:
    found: list[str] = []
    seen: set[int] = set()

    def visit(item: Any) -> None:
        if item is None or len(found) >= 50:
            return
        if is_dataclass(item):
            visit(asdict(item))
            return
        if isinstance(item, dict):
            marker = id(item)
            if marker in seen:
                return
            seen.add(marker)
            for key, child in item.items():
                if key in {"evidence_id", "source_id", "citation_id"} and child:
                    found.append(str(child))
                else:
                    visit(child)
            return
        if isinstance(item, (list, tuple, set)):
            for child in item:
                visit(child)

    visit(value)
    return list(dict.fromkeys(found))


def _payload_contains(artifact: Any, token: str) -> bool:
    if artifact is None:
        return False
    payload = getattr(artifact, "payload", {}) or {}
    return token.casefold() in json.dumps(payload, sort_keys=True, default=str).casefold()


def _normalize_depth(value: str) -> str:
    normalized = str(value or "adaptive_ic").strip().casefold().replace("-", "_").replace(" ", "_")
    aliases = {
        "fast": "fast_screening",
        "screen": "fast_screening",
        "adaptive": "adaptive_ic",
        "default": "adaptive_ic",
        "deep": "deep_initiation",
        "event": "investigate_event",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in {
        "fast_screening", "adaptive_ic", "deep_initiation", "investigate_event"
    } else "adaptive_ic"


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
