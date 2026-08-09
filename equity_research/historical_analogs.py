from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from typing import Any, Iterable

from .analysis import html_to_text, keyword_snippets
from .models import (
    AnalogAuditDecision,
    AnalogDimensionComparison,
    AnalogInvestigationStatus,
    AnalogQualityScorecard,
    AnalogResearchManifest,
    AnalogSourceLink,
    AnalogSourceObservation,
    Citation,
    CompanyEconomics,
    CompanyIdentity,
    ExternalEvidenceBundle,
    FinancialMetric,
    HistoricalAnalogAssessment,
    HistoricalAnalogCandidate,
    HistoricalAnalogIntelligence,
    HistoricalAnalogSignature,
    HistoricalAnalogWorkOrder,
    HistoricalAnalogBrief,
    HistoricalCompanyBaseline,
    HistoricalEpisodeDossier,
    HistoricalOutcomePath,
    HistoricalResearchPack,
    HybridResearchRun,
    NewsIntelligenceReport,
    ResearchProfile,
    TradeIdea,
)


ANALOG_PROMPT_VERSION = "historical-episode-intelligence-v3"
ALLOWED_ANALOG_CLASSES = {
    "Same-company prior cycle",
    "Current-industry peer cycle",
    "Same economic or financial mechanism",
    "Broad historical case study",
}
ALLOWED_SOURCE_CLASSES = {
    "academic_research",
    "institutional_history",
    "quantitative_market_data",
    "contemporaneous_reporting",
    "documented_case_study",
    "reputable_commentary",
    "licensed_manual_excerpt",
    "issuer_announcement",
    "issuer_management_remarks",
    "management_press_interview",
    "regulatory_history",
    "historical_filing",
    "supplier_customer_announcement",
}

SIMILARITY_WEIGHTS = {
    "economic_mechanism": 25,
    "operating_economics": 20,
    "capital_intensity_financing": 15,
    "valuation": 15,
    "competitive_regulatory_structure": 10,
    "macro_regime": 10,
    "source_quality": 5,
}

_MECHANISM_EPISODES = (
    {
        "mechanisms": {"product cycle", "capital allocation", "buyback", "hardware", "services", "cash generation"},
        "analog_class": "Same economic or financial mechanism",
        "episode": "Apple iPhone normalization and debt-funded capital return, FY2013-FY2016",
        "companies": ["Apple Inc."],
        "tickers": ["AAPL"],
        "period_start": "FY2013",
        "period_end": "FY2016",
        "period_precision": "fiscal_year",
        "rationale": (
            "Tests how a maturing product cycle, offshore cash constraints, debt-funded "
            "repurchases, and subsequent demand normalization affected per-share outcomes."
        ),
        "terms": ["Apple", "iPhone", "FY2013", "FY2016", "repurchases", "debt", "normalization"],
        "outcomes": ["operating", "financing", "valuation", "price"],
    },
    {
        "mechanisms": {"services", "mix", "cash generation", "capital return", "wearables", "margin"},
        "analog_class": "Same economic or financial mechanism",
        "episode": "Apple Services and wearables mix transition, FY2018-FY2020",
        "companies": ["Apple Inc."],
        "tickers": ["AAPL"],
        "period_start": "FY2018",
        "period_end": "FY2020",
        "period_precision": "fiscal_year",
        "rationale": (
            "Tests whether higher recurring-services and wearables mix translated into "
            "durable margins, free-cash-flow conversion, and per-share compounding."
        ),
        "terms": ["Apple", "Services", "wearables", "FY2018", "FY2020", "margin", "cash flow"],
        "outcomes": ["operating", "valuation", "price"],
    },
    {
        "mechanisms": {"supplier commitment", "inventory", "hardware", "purchase obligation", "capacity"},
        "analog_class": "Same economic or financial mechanism",
        "episode": "Cisco supplier commitments and inventory correction, 2000-2001",
        "companies": ["Cisco Systems"],
        "tickers": ["CSCO"],
        "period_start": "2000",
        "period_end": "2001",
        "period_precision": "year",
        "rationale": (
            "Adverse boundary case for non-cancellable supplier commitments, inventory "
            "build, demand reversal, write-down risk, and valuation compression."
        ),
        "terms": ["Cisco", "2000", "2001", "inventory", "supplier commitments", "write-down"],
        "outcomes": ["operating", "financing", "valuation", "price"],
    },
    {
        "mechanisms": {"owned capacity", "capex", "semiconductor", "manufacturing", "financing"},
        "analog_class": "Same economic or financial mechanism",
        "episode": "Intel owned-fab investment cycle, 2021-2024",
        "companies": ["Intel Corporation"],
        "tickers": ["INTC"],
        "period_start": "2021",
        "period_end": "2024",
        "period_precision": "year",
        "rationale": (
            "Counterexample for balance-sheet-owned capacity, depreciation, subsidies, "
            "utilization, and financing exposure; useful mainly to expose asset-light differences."
        ),
        "terms": ["Intel", "2021", "2024", "fabs", "capex", "utilization", "depreciation"],
        "outcomes": ["operating", "financing", "valuation"],
    },
    {
        "mechanisms": {"wearables", "smart glasses", "product adoption", "ecosystem"},
        "analog_class": "Current-industry peer cycle",
        "episode": "Meta and Google smart-glasses adoption experiments, 2013-2025",
        "companies": ["Meta Platforms", "Alphabet"],
        "tickers": ["META", "GOOGL"],
        "period_start": "2013",
        "period_end": "2025",
        "period_precision": "year",
        "rationale": (
            "Tests consumer adoption, developer ecosystems, hardware economics, privacy, "
            "and whether a new form factor becomes material rather than remaining optionality."
        ),
        "terms": ["Meta", "Google", "smart glasses", "2013", "2025", "adoption", "hardware"],
        "outcomes": ["operating", "competitive", "valuation"],
    },
    {
        "mechanisms": {"capacity", "overbuild", "leverage", "infrastructure", "utilization"},
        "analog_class": "Broad historical case study",
        "episode": "Telecom and fiber capacity overbuild, 1998-2002",
        "companies": ["Global Crossing", "WorldCom", "telecom carriers"],
        "tickers": [],
        "period_start": "1998",
        "period_end": "2002",
        "period_precision": "year",
        "rationale": (
            "Adverse boundary case for extrapolated demand, debt-funded capacity, falling "
            "utilization economics, price competition, and refinancing stress."
        ),
        "terms": ["telecom", "fiber", "1998", "2002", "overbuild", "capacity", "leverage"],
        "outcomes": ["operating", "financing", "valuation", "price"],
    },
    {
        "mechanisms": {"cloud", "capacity", "utilization", "infrastructure", "cash conversion"},
        "analog_class": "Same economic or financial mechanism",
        "episode": "AWS capacity conversion and operating leverage, 2017-2019",
        "companies": ["Amazon Web Services"],
        "tickers": ["AMZN"],
        "period_start": "2017",
        "period_end": "2019",
        "period_precision": "year",
        "rationale": (
            "Constructive case for converting infrastructure investment into utilization, "
            "revenue growth, operating leverage, and cash returns."
        ),
        "terms": ["AWS", "2017", "2019", "capacity", "utilization", "operating leverage"],
        "outcomes": ["operating", "valuation", "price"],
    },
)


def build_historical_analog_intelligence(
    identity: CompanyIdentity,
    ideas: list[TradeIdea],
    metrics: list[FinancialMetric],
    economics: CompanyEconomics,
    external_evidence: ExternalEvidenceBundle | None,
    news: NewsIntelligenceReport | None,
    *,
    llm_provider: Any | None = None,
    use_llm: bool = False,
    llm_enabled: bool = True,
    research_profile: ResearchProfile | None = None,
    historical_research: HistoricalResearchPack | None = None,
    investigate_analog_id: str | None = None,
    store: Any | None = None,
    frozen_dossiers: list[HistoricalEpisodeDossier] | None = None,
    frozen_fixture: bool = False,
    sec_client: Any | None = None,
    price_client: Any | None = None,
) -> HistoricalAnalogIntelligence:
    signature = build_analog_signature(identity, ideas, metrics, economics, external_evidence)
    candidates = _deterministic_candidates(identity, signature, economics)
    gaps: list[str] = []
    profile_id = research_profile.profile_id if research_profile else "adaptive_ic"
    investigation_limit = _investigation_limit(profile_id, investigate_analog_id)
    planning_message = ""
    if use_llm and llm_provider is not None and investigation_limit:
        proposed, message = _llm_candidates(identity, signature, candidates, llm_provider)
        if proposed:
            candidates = _merge_candidates(candidates, proposed)[:8]
        planning_message = message
        if message:
            gaps.append(message)
    selected_for_retrieval = (
        candidates[:investigation_limit]
        if investigation_limit > 0 else []
    )
    registered_sources = retrieve_registered_historical_sources(
        identity,
        selected_for_retrieval,
        sec_client=sec_client,
        price_client=price_client,
    )
    sources = _dedupe_analog_sources([
        *_analog_sources(news, external_evidence, historical_research),
        *registered_sources,
    ])
    assessments = [
        _assess_candidate(candidate, signature, sources)
        for candidate in candidates
    ]
    assessments.sort(
        key=lambda item: (
            item.status == "Audited Research Analog",
            item.status == "Investigated Draft",
            item.source_relevance_score,
            item.similarity_score or 0,
            len(item.sources),
        ),
        reverse=True,
    )
    selected = _selected_assessments(
        assessments,
        investigation_limit,
        investigate_analog_id,
    )
    dossiers: list[HistoricalEpisodeDossier] = list(frozen_dossiers or [])
    cache_hits = 0
    validation_failures: list[str] = []
    attempted_count = 0
    llm_status = "frozen_fixture" if frozen_fixture else ""
    if not frozen_fixture:
        cached, misses = _load_cached_dossiers(store, identity.ticker, signature, selected)
        dossiers.extend(cached)
        cache_hits = len(cached)
        if selected and use_llm and llm_provider is not None:
            attempted_count = len(misses)
            if misses:
                investigated, investigation_message, failures = _llm_episode_dossiers(
                    identity,
                    signature,
                    misses,
                    llm_provider,
                )
                validation_failures.extend(failures)
                signature_hash = _signature_hash(signature)
                for dossier in investigated:
                    dossier.cache_key = signature_hash
                dossiers.extend(investigated)
                _save_cached_dossiers(store, identity.ticker, investigated, signature_hash)
                if investigation_message:
                    gaps.append(investigation_message)
                if investigated and len(investigated) == len(misses):
                    llm_status = "completed"
                elif investigated:
                    llm_status = "partial"
                elif _is_timeout_message(investigation_message):
                    llm_status = "timeout"
                else:
                    llm_status = "validation_failed"
            else:
                llm_status = "completed"
        elif investigation_limit == 0:
            llm_status = "not_selected_by_profile"
        elif not llm_enabled:
            llm_status = "disabled"
        elif llm_provider is None:
            llm_status = "no_provider"
        elif not use_llm:
            llm_status = "disabled"
    dossiers = _dedupe_dossiers(dossiers)
    dossiers.sort(
        key=lambda item: (
            item.status in {
                "Audited Research Analog",
                "Frozen Demo Dossier",
                "Researched Episode",
            },
            item.confidence == "High",
            item.confidence == "Medium",
        ),
        reverse=True,
    )
    research_analogs = [
        item for item in assessments
        if item.status in {"Audited Research Analog", "Research Analog"}
    ]
    candidate_analogs = [
        item for item in assessments
        if item.status in {
            "Exploratory Candidate", "Investigated Draft", "Candidate Analog"
        }
    ]
    researched_episodes = [
        item for item in dossiers
        if item.status in {
            "Audited Research Analog", "Frozen Demo Dossier", "Researched Episode"
        }
    ]
    if researched_episodes:
        status = "Available"
        coverage = (
            f"{len(researched_episodes)} source-backed historical episode(s) completed; "
            f"{len(research_analogs)} mechanism-matched Research Analog(s) passed source checks."
        )
    elif research_analogs:
        status = "Available"
        coverage = (
            f"{len(research_analogs)} Research Analog(s) passed source-independence checks, "
            "but no episode dossier has yet completed date, outcome, and citation validation."
        )
    elif candidate_analogs:
        status = "Partial"
        coverage = (
            f"{len(candidate_analogs)} Candidate Analog(s) were identified, but none yet has "
            "two independent sources with a qualifying academic, institutional, quantitative, "
            "or contemporaneous source."
        )
        gaps.append("Historical source retrieval is incomplete; candidate analogs remain exploratory.")
    else:
        status = "Unavailable"
        coverage = "No non-superficial analog candidate survived mechanism matching."
        gaps.append(coverage)
    top_lesson = (
        researched_episodes[0].lessons[0]
        if researched_episodes and researched_episodes[0].lessons
        else assessments[0].lessons[0]
        if assessments and assessments[0].lessons
        else "No historical lesson is strong enough to alter the current thesis."
    )
    investigation_reason = _investigation_reason(
        llm_status,
        profile_id,
        investigation_limit,
        len(selected),
    )
    investigation_status = AnalogInvestigationStatus(
        code=llm_status,
        reason=investigation_reason,
        requested_count=investigation_limit,
        attempted_count=attempted_count,
        completed_count=len(researched_episodes),
        cache_hits=cache_hits,
    )
    manifest = AnalogResearchManifest(
        ticker=identity.ticker.upper(),
        status=llm_status,
        profile_id=profile_id,
        prompt_version=ANALOG_PROMPT_VERSION,
        generated_at=_utc_now(),
        selected_analog_ids=[item.analog_id for item in selected],
        source_ids=_dedupe([
            source.source_id
            for dossier in dossiers
            for source in dossier.sources
        ]),
        provider=getattr(llm_provider, "provider_name", "") if llm_provider else "",
        model=getattr(llm_provider, "model", "") if llm_provider else "",
        cache_hits=cache_hits,
        validation_failures=validation_failures,
        data_gaps=_dedupe([
            item for item in (planning_message, *gaps) if item
        ]),
    )
    work_orders = _build_historical_work_orders(
        candidates,
        assessments,
        selected_for_retrieval,
    )
    brief = _build_historical_brief(
        researched_episodes,
        assessments,
        top_lesson,
    )
    return HistoricalAnalogIntelligence(
        ticker=identity.ticker.upper(),
        status=status,
        coverage_status=coverage,
        signature=signature,
        assessments=assessments,
        dossiers=dossiers,
        top_lesson=top_lesson,
        llm_status=llm_status,
        investigation_status=investigation_status,
        research_manifest=manifest,
        data_gaps=_dedupe(gaps),
        work_orders=work_orders,
        brief=brief,
    )


def build_analog_signature(
    identity: CompanyIdentity,
    ideas: list[TradeIdea],
    metrics: list[FinancialMetric],
    economics: CompanyEconomics,
    external_evidence: ExternalEvidenceBundle | None,
) -> HistoricalAnalogSignature:
    top = ideas[0] if ideas else None
    event = top.source_events[0] if top and top.source_events else None
    driver = (
        str(event.metrics.get("economic_driver") or event.metrics.get("driver_family") or "")
        if event else ""
    ) or (top.signal_family if top else "general")
    values = _metric_map(metrics)
    revenue = values.get("revenue")
    capex = values.get("capital expenditure")
    fcf = values.get("free cash flow")
    operating_cash = values.get("operating cash flow")
    debt = values.get("long-term debt") or values.get("debt")
    backlog_growth = _event_change(ideas, ("remaining performance", "backlog", "orders"))
    capex_intensity = _ratio(capex, revenue)
    fcf_conversion = _ratio(fcf, revenue) if fcf is not None else _ratio(
        (operating_cash - capex) if operating_cash is not None and capex is not None else None,
        revenue,
    )
    macro_regime = _macro_regime(external_evidence)
    industry = economics.industry_playbook.industry_label
    evidence_ids: list[str] = []
    if event:
        evidence_ids.extend(
            citation.accession or citation.url
            for citation in event.citations
            if citation.accession or citation.url
        )
    metric_evidence_ids = [
        item.accession or item.source_url or ""
        for item in metrics
        if item.accession or item.source_url
    ]
    evidence_ids.extend(metric_evidence_ids)
    baseline = _build_current_company_baseline(
        economics,
        metrics,
        macro_regime,
        _valuation_signal(top),
    )
    baseline.evidence_ids = _dedupe([
        *baseline.evidence_ids,
        *evidence_ids,
    ])
    return HistoricalAnalogSignature(
        ticker=identity.ticker.upper(),
        driver=driver or "general",
        industry=industry,
        revenue_growth_pct=_event_change(ideas, ("revenue", "sales")),
        backlog_growth_pct=backlog_growth,
        capex_intensity_pct=capex_intensity,
        utilization_signal=_utilization_signal(ideas),
        fcf_conversion_pct=fcf_conversion,
        leverage_signal=(
            "Elevated financing exposure" if debt and revenue and debt > revenue
            else "Moderate or unclassified"
        ),
        valuation_signal=_valuation_signal(top),
        competition_signal="Requires industry and peer evidence",
        macro_regime=macro_regime,
        evidence_ids=_dedupe(evidence_ids),
        current_baseline=baseline,
    )


def _build_historical_work_orders(
    candidates: list[HistoricalAnalogCandidate],
    assessments: list[HistoricalAnalogAssessment],
    selected: list[HistoricalAnalogCandidate],
) -> list[HistoricalAnalogWorkOrder]:
    assessment_by_id = {item.analog_id: item for item in assessments}
    selected_ids = {item.analog_id for item in selected}
    result = []
    for candidate in candidates:
        assessment = assessment_by_id.get(candidate.analog_id)
        resolved = [
            item.source_id for item in (assessment.source_links if assessment else [])
            if item.status == "accepted"
        ]
        attempted = sorted({
            _source_type_from_link(item)
            for item in (
                (assessment.source_links + assessment.rejected_sources)
                if assessment else []
            )
            if _source_type_from_link(item)
        })
        required = list(candidate.required_source_types) or (
            [
                "historical_filing",
                "quantitative_historical_price",
                "issuer_announcement",
            ]
            if candidate.reference_tickers
            else [
                "institutional_history",
                "academic_research",
                "contemporaneous_reporting",
                "quantitative_historical_price",
            ]
        )
        if resolved:
            status = "evidence_retrieved"
            blocker = ""
            next_action = (
                "Complete chronology, outcome, material-difference, and "
                "independent-source validation."
            )
        elif candidate.analog_id not in selected_ids:
            status = "source_not_attempted"
            blocker = "The current research profile did not select this candidate."
            next_action = "Use Investigate Further or a deeper research profile."
        elif assessment and assessment.rejected_sources:
            status = "search_returned_no_relevant_episode_evidence"
            blocker = (
                "Retrieved material did not match the named entities, period, "
                "mechanism, and outcome path closely enough."
            )
            next_action = (
                "Search registered institutional histories, issuer archives, "
                "and contemporaneous episode reporting."
            )
        else:
            status = "registered_source_unavailable"
            blocker = (
                "No usable normalized evidence was returned by the registered "
                "historical source families."
            )
            next_action = (
                "Retry enabled historical adapters or import a licensed/manual "
                "episode excerpt with citation metadata."
            )
        result.append(HistoricalAnalogWorkOrder(
            work_order_id=_stable_id("analog-work-order", candidate.analog_id),
            analog_id=candidate.analog_id,
            episode=candidate.episode,
            status=status,
            entities=list(candidate.companies),
            aliases=list(candidate.reference_tickers),
            period_start=candidate.period_start,
            period_end=candidate.period_end,
            mechanism=candidate.mechanism,
            expected_outcomes=list(candidate.expected_outcomes),
            required_source_types=required,
            attempted_source_types=attempted,
            resolved_source_ids=resolved,
            blocker=blocker,
            next_action=next_action,
        ))
    return result


def _build_historical_brief(
    dossiers: list[HistoricalEpisodeDossier],
    assessments: list[HistoricalAnalogAssessment],
    top_lesson: str,
) -> HistoricalAnalogBrief | None:
    if dossiers:
        dossier = dossiers[0]
        return HistoricalAnalogBrief(
            status=dossier.status,
            episode=dossier.episode,
            lesson=(
                dossier.lessons[0] if dossier.lessons else top_lesson
            ),
            material_difference=(
                dossier.material_differences[0]
                if dossier.material_differences
                else "Material differences remain under review."
            ),
            linked_monitor=(
                dossier.linked_monitor_rules[0]
                if dossier.linked_monitor_rules
                else dossier.confirm_kpis[0]
                if dossier.confirm_kpis
                else "No monitor has been validated."
            ),
            evidence_status=(
                f"{len({item.source_origin_group for item in dossier.sources})} "
                "independent source origin(s)"
            ),
            analog_id=dossier.analog_id,
        )
    if assessments:
        assessment = assessments[0]
        return HistoricalAnalogBrief(
            status="Exploratory Candidate",
            episode=assessment.episode,
            lesson=top_lesson,
            material_difference=(
                assessment.material_differences[0]
                if assessment.material_differences
                else "Episode-specific differences have not been established."
            ),
            linked_monitor=(
                assessment.confirm_kpis[0]
                if assessment.confirm_kpis
                else "Investigate the episode before relying on it."
            ),
            evidence_status="Episode-specific evidence pending",
            analog_id=assessment.analog_id,
        )
    return None


def empty_historical_analog_intelligence(ticker: str = "") -> HistoricalAnalogIntelligence:
    return HistoricalAnalogIntelligence(
        ticker=ticker.upper(),
        status="Unavailable",
        coverage_status="Historical Analog Lab has not run.",
        signature=HistoricalAnalogSignature(
            ticker=ticker.upper(),
            driver="Unknown",
            industry="Unknown",
        ),
        llm_status="not_selected_by_profile",
        investigation_status=AnalogInvestigationStatus(
            code="not_selected_by_profile",
            reason="Historical Analog Lab has not run for this result.",
        ),
        data_gaps=["Historical Analog Lab has not run."],
    )


def apply_hybrid_analog_results(
    report: HistoricalAnalogIntelligence,
    run: HybridResearchRun | None,
) -> HistoricalAnalogIntelligence:
    """Attach dual-model provenance and publish only audited analog dossiers."""
    if run is None:
        return report
    variants = {item.variant_id: item for item in run.variants}
    report.primary_variant = variants.get("deepseek_led")
    report.challenger_variant = variants.get("sol_led_challenger")
    report.comparison = run.analog_comparison
    if report.research_manifest:
        report.research_manifest.evidence_hash = run.evidence_hash
        report.research_manifest.source_registry_version = (
            run.source_registry_version
        )
        report.research_manifest.primary_variant_id = (
            run.selected_primary_variant_id
        )
        report.research_manifest.challenger_variant_id = (
            "sol_led_challenger" if report.challenger_variant else ""
        )
    primary = report.primary_variant
    if (
        not primary
        or not primary.final_artifact
        or not primary.audit_decision
        or primary.audit_decision.status != "passed"
    ):
        report.llm_status = (
            "audit_unavailable" if primary else report.llm_status
        )
        if primary:
            report.data_gaps.append(
                "GPT-5.6 Sol audit did not pass; model-written historical "
                "dossiers were suppressed and deterministic candidates remain."
            )
        return report
    raw_dossiers = primary.final_artifact.payload.get(
        "historical_analog_dossiers"
    )
    if not isinstance(raw_dossiers, list):
        report.data_gaps.append(
            "The audited run returned no structured historical episode dossier."
        )
        return report
    assessments = {item.analog_id: item for item in report.assessments}
    allowed_sources = {
        source.evidence_id or source.source_id: source
        for assessment in report.assessments
        for source in assessment.sources
    }
    allowed_sources.update({
        source.source_id: source
        for assessment in report.assessments
        for source in assessment.sources
    })
    audited: list[HistoricalEpisodeDossier] = []
    failures: list[str] = []
    for raw in raw_dossiers:
        if not isinstance(raw, dict):
            continue
        dossier, reason = _validated_audited_dossier(
            raw,
            assessments,
            allowed_sources,
            primary.audit_decision,
        )
        if dossier:
            audited.append(dossier)
        elif reason:
            failures.append(reason)
    if audited:
        existing = {
            item.analog_id: item for item in report.dossiers
            if item.status == "Frozen Demo Dossier"
        }
        for item in audited:
            existing[item.analog_id] = item
        report.dossiers = list(existing.values())
        report.status = "Available"
        report.llm_status = "completed"
        report.coverage_status = (
            f"{len(audited)} DeepSeek-led historical analog dossier(s) passed "
            "GPT-5.6 Sol audit and deterministic source validation."
        )
        report.top_lesson = (
            audited[0].lessons[0]
            if audited[0].lessons else report.top_lesson
        )
    if failures:
        report.data_gaps.extend(failures)
        if report.research_manifest:
            report.research_manifest.validation_failures.extend(failures)
    report.data_gaps = _dedupe(report.data_gaps)
    return report


def _validated_audited_dossier(
    raw: dict[str, Any],
    assessments: dict[str, HistoricalAnalogAssessment],
    allowed_sources: dict[str, AnalogSourceObservation],
    audit_decision: AnalogAuditDecision,
) -> tuple[HistoricalEpisodeDossier | None, str]:
    dossier, reason = _validated_llm_dossier(
        raw, assessments, allowed_sources
    )
    if not dossier:
        return None, reason
    origins = {item.source_origin_group for item in dossier.sources}
    if len(origins) < 2:
        return None, (
            f"{dossier.analog_id}: audited analog needs two independent source origins"
        )
    if not any(_qualifying_historical_source(item) for item in dossier.sources):
        return None, (
            f"{dossier.analog_id}: audited analog lacks a primary, institutional, "
            "academic, or quantitative source"
        )
    assessment = assessments[dossier.analog_id]
    if assessment.source_relevance_score < 70:
        return None, (
            f"{dossier.analog_id}: source relevance "
            f"{assessment.source_relevance_score}/100 is below audited threshold 70"
        )
    quality = _quality_scorecard_from_draft(
        raw,
        dossier.sources,
        dossier.dimensions,
        audited=True,
    )
    if quality.critical_failures:
        return None, (
            f"{dossier.analog_id}: critical quality failure(s): "
            + ", ".join(quality.critical_failures)
        )
    scores = {
        _canonical_similarity_dimension(item.dimension): item.score
        for item in dossier.dimensions
        if _canonical_similarity_dimension(item.dimension)
    }
    scores["source_quality"] = assessment.source_relevance_score
    required_dimensions = set(SIMILARITY_WEIGHTS) - {"source_quality"}
    missing_dimensions = sorted(required_dimensions - set(scores))
    if missing_dimensions:
        return None, (
            f"{dossier.analog_id}: audited comparison is missing dimension(s): "
            + ", ".join(missing_dimensions)
        )
    audited_score = _weighted_similarity_score(scores)
    dossier.status = "Audited Research Analog"
    dossier.similarity_band = _similarity_band_from_dimensions(scores)
    dossier.audited_similarity_score = audited_score
    dossier.primary_provider = "deepseek"
    dossier.primary_model = "deepseek-v4-pro"
    dossier.secondary_auditor_provider = audit_decision.auditor_provider
    dossier.secondary_auditor_model = audit_decision.auditor_model
    dossier.audit_status = "passed"
    dossier.audit_decision = audit_decision
    dossier.rejected_sources = list(assessment.rejected_sources)
    dossier.quality_scorecard = quality
    dossier.generation_provenance = {
        "primary": "DeepSeek V4 Pro",
        "secondary_audit": "GPT-5.6 Sol",
        "prompt_version": ANALOG_PROMPT_VERSION,
        "validation": "deterministic_passed",
    }
    return dossier, ""


def _build_current_company_baseline(
    economics: CompanyEconomics,
    metrics: list[FinancialMetric],
    macro_regime: str,
    valuation_signal: str,
) -> HistoricalCompanyBaseline:
    latest = _latest_metrics(metrics)
    evidence_ids = _dedupe([
        item.accession or item.source_url or ""
        for item in latest.values()
        if item.accession or item.source_url
    ])
    capacity_terms = " ".join([
        economics.business_model,
        *(driver.name for driver in economics.drivers),
        *economics.industry_playbook.key_kpis,
    ]).lower()
    asset_light = any(
        token in capacity_terms
        for token in ("platform", "software", "services", "marketplace", "fabless")
    )
    owned_capacity = (
        "Predominantly asset-light or supplier-owned; confirm leases, tooling, and owned facilities."
        if asset_light else
        "Owned-versus-contracted capacity requires filing and asset-footnote evidence."
    )
    return HistoricalCompanyBaseline(
        business_model=economics.business_model or "Unknown",
        segments=list(economics.industry_playbook.key_kpis[:8]),
        owned_capacity=owned_capacity,
        contracted_capacity=_metric_summary(
            latest,
            ("purchase obligations", "commitments", "operating lease liabilities"),
            "Purchase obligations, supplier commitments, guarantees, and prepayments require historical filing extraction.",
        ),
        capex=_metric_summary(
            latest,
            ("capital expenditure", "payments to acquire property plant and equipment"),
        ),
        research_and_development=_metric_summary(
            latest,
            ("r&d expense", "research and development expense"),
        ),
        inventory=_metric_summary(latest, ("inventory",)),
        purchase_obligations=_metric_summary(
            latest,
            ("purchase obligations", "commitments"),
            "Purchase obligations Unknown; inspect commitments and contractual-obligations disclosures.",
        ),
        guarantees_and_leases=_metric_summary(
            latest,
            ("operating lease liabilities", "finance lease liabilities", "guarantees"),
            "Unknown; inspect guarantees, leases, prepayments, and supplier-financing disclosures.",
        ),
        debt_and_liquidity=_metric_summary(
            latest,
            ("long-term debt", "current debt", "cash"),
        ),
        free_cash_flow=_fcf_baseline(latest),
        valuation=valuation_signal or "Unknown",
        competition="; ".join(economics.industry_playbook.peer_tickers[:8]) or "Requires peer and industry evidence.",
        management_changes="Requires management-source chronology and commitment tracking.",
        macro_regime=macro_regime,
        evidence_ids=evidence_ids,
        data_gaps=[
            item for item in (
                "Owned-versus-contracted capacity is not fully classified."
                if "requires" in owned_capacity.lower() else "",
                "Off-balance-sheet commitments require dedicated filing-table extraction."
                if not any(
                    key in latest for key in ("purchase obligations", "commitments")
                ) else "",
            )
            if item
        ],
    )


def _deterministic_candidates(
    identity: CompanyIdentity,
    signature: HistoricalAnalogSignature,
    economics: CompanyEconomics,
) -> list[HistoricalAnalogCandidate]:
    company = identity.name
    industry = signature.industry or identity.sic_description or "industry"
    structured_driver_context = " ".join(driver.name for driver in economics.drivers)
    playbook_kpis = " ".join(economics.industry_playbook.key_kpis)
    context = (
        f"{signature.driver} {industry} {economics.business_model} "
        f"{structured_driver_context} {playbook_kpis} "
        f"owned capacity {signature.current_baseline.owned_capacity} "
        f"contracted capacity {signature.current_baseline.contracted_capacity} "
        f"{signature.current_baseline.purchase_obligations} "
        f"{signature.current_baseline.guarantees_and_leases}"
    ).lower()
    context_tokens = set(_tokens(context))
    candidates: list[HistoricalAnalogCandidate] = []
    for episode in _MECHANISM_EPISODES:
        mechanisms = set(episode["mechanisms"])
        overlap = mechanisms & context_tokens
        phrase_match = any(
            mechanism in context for mechanism in mechanisms
        )
        if len(overlap) < 1 and not phrase_match:
            continue
        candidates.append(_candidate(
            identity.ticker,
            str(episode["analog_class"]),
            str(episode["episode"]),
            list(episode["companies"]),
            str(episode["rationale"]),
            list(episode["terms"]),
            mechanism=", ".join(sorted(mechanisms)),
            period_start=str(episode["period_start"]),
            period_end=str(episode["period_end"]),
            period_precision=str(episode["period_precision"]),
            expected_outcomes=list(episode["outcomes"]),
            required_source_types=[
                "historical_filing",
                "institutional_history",
                "quantitative_market_data",
            ],
            reference_tickers=list(episode.get("tickers") or []),
        ))
    if not candidates:
        candidates.append(_candidate(
            identity.ticker,
            "Same-company prior cycle",
            f"{company} prior {signature.driver} cycle; exact period pending",
            [company],
            (
                "A bounded discovery candidate. It must be decomposed into an exact "
                "episode before it can become investigated history."
            ),
            [company, signature.driver, "historical filing"],
            mechanism=signature.driver,
            period_precision="unknown",
            expected_outcomes=["operating", "financing", "valuation", "price"],
            required_source_types=["historical_filing"],
        ))
    return candidates[:8]


def _candidate(
    ticker: str,
    analog_class: str,
    episode: str,
    companies: list[str],
    rationale: str,
    terms: list[str],
    *,
    proposed_by: str = "deterministic",
    mechanism: str = "",
    period_start: str = "",
    period_end: str = "",
    period_precision: str = "unknown",
    expected_outcomes: list[str] | None = None,
    required_source_types: list[str] | None = None,
    reference_tickers: list[str] | None = None,
) -> HistoricalAnalogCandidate:
    return HistoricalAnalogCandidate(
        analog_id=_stable_id("historical-analog", ticker, analog_class, episode),
        analog_class=analog_class,
        episode=episode,
        companies=companies,
        selection_rationale=rationale,
        search_terms=terms,
        proposed_by=proposed_by,
        mechanism=mechanism,
        period_start=period_start,
        period_end=period_end,
        period_precision=period_precision,
        expected_outcomes=list(expected_outcomes or []),
        required_source_types=list(required_source_types or []),
        reference_tickers=list(reference_tickers or []),
    )


def _llm_candidates(
    identity: CompanyIdentity,
    signature: HistoricalAnalogSignature,
    existing: list[HistoricalAnalogCandidate],
    provider: Any,
) -> tuple[list[HistoricalAnalogCandidate], str]:
    prompt = {
        "prompt_version": ANALOG_PROMPT_VERSION,
        "task": "Propose historical analog candidates for registered deterministic research. Return JSON only.",
        "rules": [
            "Do not browse or invent outcomes, citations, prices, or probabilities.",
            "Prefer same-company and current-industry analogs over distant cross-era analogies.",
            "Reject superficial name or technology similarity; match an economic mechanism.",
            "Do not return URLs or sources. Deterministic adapters retrieve and validate sources.",
        ],
        "allowed_analog_classes": sorted(ALLOWED_ANALOG_CLASSES),
        "allowed_source_classes": sorted(ALLOWED_SOURCE_CLASSES),
        "company": {"ticker": identity.ticker, "name": identity.name},
        "signature": {
            "driver": signature.driver,
            "industry": signature.industry,
            "revenue_growth_pct": signature.revenue_growth_pct,
            "backlog_growth_pct": signature.backlog_growth_pct,
            "capex_intensity_pct": signature.capex_intensity_pct,
            "fcf_conversion_pct": signature.fcf_conversion_pct,
            "leverage_signal": signature.leverage_signal,
            "macro_regime": signature.macro_regime,
            "current_baseline": {
                "business_model": signature.current_baseline.business_model,
                "owned_capacity": signature.current_baseline.owned_capacity,
                "contracted_capacity": signature.current_baseline.contracted_capacity,
                "capex": signature.current_baseline.capex,
                "research_and_development": signature.current_baseline.research_and_development,
                "inventory": signature.current_baseline.inventory,
                "purchase_obligations": signature.current_baseline.purchase_obligations,
                "debt_and_liquidity": signature.current_baseline.debt_and_liquidity,
                "free_cash_flow": signature.current_baseline.free_cash_flow,
                "valuation": signature.current_baseline.valuation,
            },
        },
        "existing_candidates": [
            {"class": item.analog_class, "episode": item.episode}
            for item in existing
        ],
        "output_schema": {
            "candidates": [{
                "analog_class": "one allowed class",
                "episode": "string",
                "companies": ["string"],
                "selection_rationale": "string",
                "search_terms": ["string"],
                "mechanism": "exact economic mechanism",
                "period_start": "year, fiscal year, quarter, or date",
                "period_end": "year, fiscal year, quarter, or date",
                "period_precision": "day|quarter|fiscal_year|year|unknown",
                "expected_outcomes": ["operating|financing|valuation|price"],
            }]
        },
    }
    try:
        payload = provider.complete_json(prompt)
    except Exception as exc:
        return [], f"LLM analog planning did not complete: {_safe_error(exc)}"
    result = []
    for raw in payload.get("candidates", []) if isinstance(payload, dict) else []:
        analog_class = str(raw.get("analog_class") or "")
        episode = str(raw.get("episode") or "").strip()
        rationale = str(raw.get("selection_rationale") or "").strip()
        if analog_class not in ALLOWED_ANALOG_CLASSES or not episode or not rationale:
            continue
        if "http://" in episode.lower() or "https://" in episode.lower():
            continue
        mechanism = str(raw.get("mechanism") or "").strip()
        period_start = str(raw.get("period_start") or "").strip()
        period_end = str(raw.get("period_end") or "").strip()
        if not mechanism:
            continue
        result.append(_candidate(
            identity.ticker,
            analog_class,
            episode,
            _string_list(raw.get("companies")) or [identity.name],
            rationale,
            _string_list(raw.get("search_terms")),
            proposed_by=getattr(provider, "provider_name", "llm"),
            mechanism=mechanism,
            period_start=period_start,
            period_end=period_end,
            period_precision=str(raw.get("period_precision") or "unknown"),
            expected_outcomes=_string_list(raw.get("expected_outcomes")),
            required_source_types=[
                "historical_filing",
                "institutional_history",
                "quantitative_market_data",
            ],
        ))
    return result, "" if result else "LLM analog planner returned no valid registered candidates."


def _llm_episode_dossiers(
    identity: CompanyIdentity,
    signature: HistoricalAnalogSignature,
    assessments: list[HistoricalAnalogAssessment],
    provider: Any,
) -> tuple[list[HistoricalEpisodeDossier], str, list[str]]:
    eligible = [
        assessment for assessment in assessments
        if assessment.status == "Investigated Draft"
        and assessment.source_relevance_score >= 60
        and assessment.sources
    ]
    ineligible_ids = {
        assessment.analog_id for assessment in assessments
        if assessment not in eligible
    }
    if not eligible:
        return (
            [],
            "No selected analog has a candidate-relevant registered source scoring at least 60/100; LLM synthesis was not allowed to fill the evidence gap.",
            [f"{analog_id}: candidate-relevant historical sources are incomplete" for analog_id in sorted(ineligible_ids)],
        )
    allowed_sources = {
        source.source_id: source
        for assessment in eligible
        for source in assessment.sources
    }
    prompt = {
        "prompt_version": ANALOG_PROMPT_VERSION,
        "task": (
            "Build concise historical episode dossiers from the supplied registered evidence only. "
            "Return JSON only."
        ),
        "rules": [
            "Use only supplied source_ids and excerpts.",
            "Do not browse, invent dates, outcomes, prices, metrics, citations, or probabilities.",
            "Use exact dates when supported; fiscal year, calendar year, or quarter precision is acceptable when explicitly labelled.",
            "Distinguish documented outcomes from qualitative interpretation.",
            "A draft must cite at least one relevant registered source. Two independent origins and a passing Sol audit are required before Audited Research Analog status.",
            "Historical analogs cannot determine the current security's direction, fair value, probability, or promotion stage.",
            "Compare economic_mechanism, operating_economics, capital_intensity_financing, valuation, competitive_regulatory_structure, and macro_regime. Source quality is computed deterministically.",
        ],
        "company": {"ticker": identity.ticker, "name": identity.name},
        "current_signature": {
            "driver": signature.driver,
            "industry": signature.industry,
            "revenue_growth_pct": signature.revenue_growth_pct,
            "backlog_growth_pct": signature.backlog_growth_pct,
            "capex_intensity_pct": signature.capex_intensity_pct,
            "fcf_conversion_pct": signature.fcf_conversion_pct,
            "leverage_signal": signature.leverage_signal,
            "macro_regime": signature.macro_regime,
            "current_baseline": {
                "business_model": signature.current_baseline.business_model,
                "owned_capacity": signature.current_baseline.owned_capacity,
                "contracted_capacity": signature.current_baseline.contracted_capacity,
                "capex": signature.current_baseline.capex,
                "research_and_development": signature.current_baseline.research_and_development,
                "inventory": signature.current_baseline.inventory,
                "purchase_obligations": signature.current_baseline.purchase_obligations,
                "guarantees_and_leases": signature.current_baseline.guarantees_and_leases,
                "debt_and_liquidity": signature.current_baseline.debt_and_liquidity,
                "free_cash_flow": signature.current_baseline.free_cash_flow,
                "valuation": signature.current_baseline.valuation,
                "competition": signature.current_baseline.competition,
                "macro_regime": signature.current_baseline.macro_regime,
            },
        },
        "candidates": [
            {
                "analog_id": assessment.analog_id,
                "episode": assessment.episode,
                "class": assessment.analog_class,
                "selection_rationale": assessment.selection_rationale,
                "confirm_kpis": assessment.confirm_kpis,
                "reject_kpis": assessment.reject_kpis,
                "allowed_source_ids": [source.source_id for source in assessment.sources],
            }
            for assessment in eligible
        ],
        "sources": {
            source_id: {
                "provider": source.provider,
                "title": source.title,
                "source_class": source.source_class,
                "origin_group": source.source_origin_group,
                "published_at": source.published_at,
                "excerpt": source.excerpt[:1800],
                "quantitative": source.quantitative,
                "contemporaneous": source.contemporaneous,
            }
            for source_id, source in allowed_sources.items()
        },
        "output_schema": {
            "dossiers": [{
                "analog_id": "existing analog id",
                "episode": "specific historical episode",
                "start_date": "YYYY-MM-DD",
                "end_date": "YYYY-MM-DD",
                "period_precision": "day|quarter|fiscal_year|year",
                "companies": ["string"],
                "securities": ["string"],
                "triggering_decisions": ["string"],
                "operating_outcome": "string",
                "financing_outcome": "string",
                "valuation_outcome": "string",
                "price_outcome": "string",
                "macro_context": "string",
                "competitive_context": "string",
                "material_differences": ["string"],
                "lessons": ["string"],
                "limitations": ["string"],
                "confirm_kpis": ["string"],
                "reject_kpis": ["string"],
                "linked_monitor_rules": ["string"],
                "hidden_risks": ["string"],
                "source_ids": ["allowed source id"],
                "dimensions": [{
                    "dimension": "string",
                    "current_value": "string",
                    "historical_value": "string",
                    "score": 0,
                    "assessment": "string",
                    "evidence_ids": ["allowed source id"],
                }],
            }]
        },
    }
    try:
        payload = provider.complete_json(prompt)
    except Exception as exc:
        return [], f"LLM historical episode investigation did not complete: {_safe_error(exc)}", []
    raw_dossiers = payload.get("dossiers", []) if isinstance(payload, dict) else []
    by_id = {item.analog_id: item for item in eligible}
    dossiers: list[HistoricalEpisodeDossier] = []
    failures: list[str] = []
    for raw in raw_dossiers:
        dossier, reason = _validated_llm_dossier(raw, by_id, allowed_sources)
        if dossier:
            dossiers.append(dossier)
        elif reason:
            failures.append(reason)
    message = "" if dossiers else "LLM returned no historical episode dossier that passed source, date, and citation validation."
    return dossiers, message, failures


def _validated_llm_dossier(
    raw: dict,
    assessments: dict[str, HistoricalAnalogAssessment],
    allowed_sources: dict[str, AnalogSourceObservation],
) -> tuple[HistoricalEpisodeDossier | None, str]:
    analog_id = str(raw.get("analog_id") or "")
    assessment = assessments.get(analog_id)
    if not assessment:
        return None, f"{analog_id or 'unknown'}: dossier references an unselected analog"
    start_date = str(raw.get("start_date") or "")
    end_date = str(raw.get("end_date") or "")
    period_precision = str(raw.get("period_precision") or "day")
    if not _valid_period_range(start_date, end_date, period_precision):
        return None, (
            f"{analog_id}: supported episode dates/period are missing or invalid "
            f"for precision {period_precision}"
        )
    source_ids = _string_list(raw.get("source_ids"))
    if not source_ids or any(source_id not in allowed_sources for source_id in source_ids):
        return None, f"{analog_id}: dossier contains missing or unregistered source IDs"
    selected_sources = [allowed_sources[source_id] for source_id in source_ids]
    origins = {source.source_origin_group for source in selected_sources}
    qualifying = any(
        source.quantitative
        or source.contemporaneous
        or source.source_class in {
            "academic_research",
            "institutional_history",
            "quantitative_market_data",
        }
        for source in selected_sources
    )
    if not qualifying:
        return None, f"{analog_id}: no qualifying historical or quantitative source"
    required_text = (
        "episode",
        "operating_outcome",
        "financing_outcome",
        "valuation_outcome",
        "price_outcome",
    )
    if any(not str(raw.get(key) or "").strip() for key in required_text):
        return None, f"{analog_id}: one or more required episode outcomes are empty"
    dimensions = []
    for item in raw.get("dimensions", []) if isinstance(raw.get("dimensions"), list) else []:
        evidence_ids = _string_list(item.get("evidence_ids"))
        if any(source_id not in source_ids for source_id in evidence_ids):
            continue
        try:
            score = max(0, min(100, int(item.get("score", 0))))
        except (TypeError, ValueError):
            score = 0
        dimensions.append(AnalogDimensionComparison(
            dimension=str(item.get("dimension") or "Unspecified"),
            current_value=str(item.get("current_value") or "Unknown"),
            historical_value=str(item.get("historical_value") or "Unknown"),
            score=score,
            assessment=str(item.get("assessment") or ""),
            evidence_ids=evidence_ids,
        ))
    quality = _quality_scorecard_from_draft(
        raw,
        selected_sources,
        dimensions,
        audited=False,
    )
    return HistoricalEpisodeDossier(
        analog_id=analog_id,
        status="Investigated Draft",
        episode=str(raw.get("episode") or assessment.episode),
        start_date=start_date,
        end_date=end_date,
        companies=_string_list(raw.get("companies")) or list(assessment.companies),
        securities=_string_list(raw.get("securities")),
        triggering_decisions=_string_list(raw.get("triggering_decisions")),
        operating_outcome=str(raw.get("operating_outcome") or ""),
        financing_outcome=str(raw.get("financing_outcome") or ""),
        valuation_outcome=str(raw.get("valuation_outcome") or ""),
        price_outcome=str(raw.get("price_outcome") or ""),
        macro_context=str(raw.get("macro_context") or "Unknown"),
        competitive_context=str(raw.get("competitive_context") or "Unknown"),
        dimensions=dimensions,
        material_differences=_string_list(raw.get("material_differences")),
        lessons=_string_list(raw.get("lessons")),
        limitations=_string_list(raw.get("limitations")) or [
            "The analogy cannot establish direction, fair value, probability, or timing for the current security."
        ],
        confirm_kpis=_string_list(raw.get("confirm_kpis")) or list(assessment.confirm_kpis),
        reject_kpis=_string_list(raw.get("reject_kpis")) or list(assessment.reject_kpis),
        linked_monitor_rules=_string_list(raw.get("linked_monitor_rules")),
        source_ids=source_ids,
        sources=selected_sources,
        source_quality="Independent registered sources with citation-bound LLM synthesis",
        confidence="Medium",
        quantitative_verified=any(source.quantitative for source in selected_sources),
        researched_at=_utc_now(),
        cache_key=_dossier_cache_key(analog_id, source_ids),
        period_precision=period_precision,
        similarity_band=_similarity_band_from_dimensions({
            item.dimension: item.score for item in dimensions
        }),
        primary_provider="deepseek",
        primary_model="deepseek-v4-pro",
        audit_status="not_run",
        hidden_risks=_string_list(raw.get("hidden_risks")),
        quality_scorecard=quality,
        generation_provenance={
            "primary": "DeepSeek V4 Pro",
            "secondary_audit": "not_run",
            "prompt_version": ANALOG_PROMPT_VERSION,
        },
    ), ""


def retrieve_registered_historical_sources(
    identity: CompanyIdentity,
    candidates: list[HistoricalAnalogCandidate],
    *,
    sec_client: Any | None = None,
    price_client: Any | None = None,
) -> list[AnalogSourceObservation]:
    """Retrieve bounded primary filings and price paths for named episodes."""
    sources: list[AnalogSourceObservation] = []
    for candidate in candidates:
        start_year = _year_from_period(candidate.period_start)
        end_year = _year_from_period(candidate.period_end)
        if start_year is None or end_year is None:
            continue
        references = _candidate_reference_entities(identity, candidate)
        for reference_identity, security in references[:2]:
            if (
                reference_identity is None
                and sec_client is not None
                and security
            ):
                try:
                    reference_identity = sec_client.map_ticker(security)
                except Exception:
                    reference_identity = None
            if sec_client is not None and reference_identity is not None:
                sources.extend(_historical_filing_sources(
                    sec_client,
                    reference_identity,
                    security,
                    candidate,
                    start_year,
                    end_year,
                ))
            if price_client is not None and security:
                price_source = _historical_price_source(
                    price_client,
                    reference_identity.name if reference_identity else security,
                    security,
                    candidate,
                    start_year,
                    end_year,
                )
                if price_source is not None:
                    sources.append(price_source)
    return _dedupe_analog_sources(sources)


def _candidate_reference_entities(
    identity: CompanyIdentity,
    candidate: HistoricalAnalogCandidate,
) -> list[tuple[CompanyIdentity | None, str]]:
    normalized_name = identity.name.lower()
    same_company = any(
        normalized_name in company.lower() or company.lower() in normalized_name
        for company in candidate.companies
    )
    tickers = list(candidate.reference_tickers)
    if same_company and identity.ticker.upper() not in {
        item.upper() for item in tickers
    }:
        tickers.insert(0, identity.ticker.upper())
    if not tickers and candidate.analog_class == "Same-company prior cycle":
        tickers = [identity.ticker.upper()]
    rows: list[tuple[CompanyIdentity | None, str]] = []
    for ticker in tickers:
        if ticker.upper() == identity.ticker.upper():
            rows.append((identity, ticker.upper()))
        else:
            rows.append((None, ticker.upper()))
    return rows


def _historical_filing_sources(
    sec_client: Any,
    reference_identity: CompanyIdentity,
    security: str,
    candidate: HistoricalAnalogCandidate,
    start_year: int,
    end_year: int,
) -> list[AnalogSourceObservation]:
    filings = []
    for form in ("10-K", "20-F", "40-F"):
        try:
            filings.extend(
                sec_client.get_comparable_filings(
                    reference_identity.cik,
                    form,
                    limit=35,
                )
            )
        except Exception:
            continue
    bounded = [
        filing for filing in filings
        if _filing_matches_episode(filing, start_year, end_year)
    ]
    bounded.sort(key=lambda filing: (
        filing.report_date or filing.filing_date,
        filing.filing_date,
    ))
    selected = _episode_boundary_filings(bounded)
    sources: list[AnalogSourceObservation] = []
    keywords = _historical_source_keywords(candidate)
    for filing in selected:
        try:
            text = html_to_text(sec_client.get_filing_text(filing))
        except Exception:
            continue
        snippets = keyword_snippets(text, keywords, max_items=5)
        if not snippets:
            continue
        excerpt = " ".join(snippets)[:2_400]
        evidence_id = _stable_id(
            "analog-filing-evidence",
            candidate.analog_id,
            filing.accession,
        )
        citation = Citation(
            source=f"SEC EDGAR {filing.form}",
            url=filing.url,
            filed=filing.filing_date,
            form=filing.form,
            section="Mechanism-matched historical filing excerpts",
            snippet=excerpt[:500],
            accession=filing.accession,
            period_end=filing.report_date,
            source_tier=1,
        )
        sources.append(AnalogSourceObservation(
            source_id=evidence_id,
            provider="SEC EDGAR historical filing",
            title=(
                f"{reference_identity.name} {filing.form} for "
                f"{filing.report_date or filing.filing_date}"
            ),
            source_class="historical_filing",
            source_origin_group="sec_edgar",
            published_at=filing.filing_date,
            url=filing.url,
            evidence_id=evidence_id,
            source_tier=1,
            licensing_policy="public_primary_source_excerpt",
            excerpt=excerpt,
            quantitative=any(character.isdigit() for character in excerpt),
            contemporaneous=True,
            citation=citation,
        ))
    return sources


def _historical_price_source(
    price_client: Any,
    company_name: str,
    security: str,
    candidate: HistoricalAnalogCandidate,
    start_year: int,
    end_year: int,
) -> AnalogSourceObservation | None:
    historical_result = None
    try:
        if hasattr(price_client, "historical_rows_result"):
            historical_result = price_client.historical_rows_result(
                security,
                date(start_year, 1, 1),
                date(end_year, 12, 31),
            )
            rows = historical_result.rows
        else:
            rows = price_client._cached_daily_rows(security)
    except Exception:
        return None
    bounded = [
        row for row in rows
        if date(start_year, 1, 1) <= row["date"] <= date(end_year, 12, 31)
        and row.get("close") not in {None, 0}
    ]
    if len(bounded) < 2:
        return None
    start_row = bounded[0]
    end_row = bounded[-1]
    start_close = float(start_row["close"])
    end_close = float(end_row["close"])
    change_pct = (end_close / start_close - 1.0) * 100
    provider = "Daily price provider"
    source_url = ""
    adjusted = False
    if historical_result is not None:
        provider = historical_result.provider
        source_url = historical_result.source_url or ""
        adjusted = bool(historical_result.adjusted)
    else:
        try:
            status = price_client._price_source(security)
            provider = status.provider
            source_url = status.source_url or ""
            adjusted = bool(status.adjusted)
        except Exception:
            pass
    excerpt = (
        f"{company_name} ({security}) price path for {candidate.episode}: "
        f"{start_row['date'].isoformat()} close {start_close:.2f}; "
        f"{end_row['date'].isoformat()} close {end_close:.2f}; "
        f"change {change_pct:+.1f}%. "
        f"Corporate-action adjusted: {'yes' if adjusted else 'not confirmed'}."
    )
    evidence_id = _stable_id(
        "analog-price-evidence",
        candidate.analog_id,
        security,
        start_row["date"],
        end_row["date"],
        provider,
    )
    citation = Citation(
        source=provider,
        url=source_url,
        filed=end_row["date"].isoformat(),
        form="Historical price series",
        section="Episode price path",
        snippet=excerpt,
        period_end=end_row["date"].isoformat(),
        source_tier=2,
    )
    return AnalogSourceObservation(
        source_id=evidence_id,
        provider=provider,
        title=f"{company_name} validated price path: {candidate.episode}",
        source_class="quantitative_market_data",
        source_origin_group=_source_origin(provider),
        published_at=end_row["date"].isoformat(),
        url=source_url,
        evidence_id=evidence_id,
        source_tier=2,
        licensing_policy="derived_price_observation",
        excerpt=excerpt,
        quantitative=True,
        contemporaneous=True,
        citation=citation,
    )


def _filing_matches_episode(
    filing: Any,
    start_year: int,
    end_year: int,
) -> bool:
    report_year = _year_from_period(
        filing.report_date or filing.filing_date
    )
    return bool(
        report_year is not None
        and start_year <= report_year <= end_year
    )


def _episode_boundary_filings(filings: list[Any]) -> list[Any]:
    if len(filings) <= 3:
        return filings
    middle = filings[len(filings) // 2]
    return [filings[0], middle, filings[-1]]


def _historical_source_keywords(
    candidate: HistoricalAnalogCandidate,
) -> list[str]:
    values = [
        *candidate.search_terms,
        *candidate.mechanism.replace("/", " ").replace(",", " ").split(),
        "purchase obligations",
        "commitments",
        "inventory",
        "cash flow",
        "debt",
        "capital expenditures",
        "repurchases",
        "revenue",
        "margin",
    ]
    return _dedupe([
        value for value in values
        if len(value.strip()) >= 4 and not value.strip().isdigit()
    ])[:24]


def _source_origin(provider: str) -> str:
    normalized = "".join(
        character if character.isalnum() else "_"
        for character in provider.lower()
    )
    return "_".join(part for part in normalized.split("_") if part) or "price_provider"


def _dedupe_analog_sources(
    sources: list[AnalogSourceObservation],
) -> list[AnalogSourceObservation]:
    rows: list[AnalogSourceObservation] = []
    seen: set[str] = set()
    for source in sources:
        key = source.evidence_id or source.source_id
        if key and key not in seen:
            rows.append(source)
            seen.add(key)
    return rows


def _dedupe_source_links(
    links: list[AnalogSourceLink],
) -> list[AnalogSourceLink]:
    rows: list[AnalogSourceLink] = []
    seen: set[tuple[str, str]] = set()
    for link in links:
        key = (link.analog_id, link.source_id)
        if key in seen:
            continue
        seen.add(key)
        rows.append(link)
    return rows


def _source_type_from_link(link: AnalogSourceLink) -> str:
    provider = str(link.source_provider or "").strip()
    title = str(link.source_title or "").casefold()
    provider_text = provider.casefold()
    if any(token in provider_text for token in ("sec", "edgar")):
        return "historical_filing"
    if any(token in provider_text for token in ("issuer", "investor relations")):
        return "issuer_archive"
    if any(token in provider_text for token in ("nber", "bis", "imf", "federal reserve")):
        return "institutional_history"
    if any(token in title for token in ("annual report", "10-k", "20-f", "filing")):
        return "historical_filing"
    if any(token in title for token in ("announcement", "newsroom", "press release")):
        return "issuer_announcement"
    return provider or "registered_historical_source"


def _rejection_summary(reasons: list[str]) -> str:
    labels = {
        "entity_or_mechanism_mismatch": (
            "The source does not match the named entities or economic mechanism."
        ),
        "episode_period_mismatch": (
            "The source does not cover the selected historical period."
        ),
        "current_feed_does_not_analyze_historical_episode": (
            "The current article does not substantively analyze the selected "
            "historical episode."
        ),
        "source_relevance_below_60": (
            "Episode, period, mechanism, and outcome coverage did not reach "
            "the research threshold."
        ),
        "outcome_not_documented": (
            "The source does not document an operating, financing, valuation, "
            "or price outcome."
        ),
    }
    readable = [labels.get(item, item.replace("_", " ").capitalize()) for item in reasons]
    return " ".join(_dedupe(readable))


def _analog_sources(
    news: NewsIntelligenceReport | None,
    external_evidence: ExternalEvidenceBundle | None,
    historical_research: HistoricalResearchPack | None = None,
) -> list[AnalogSourceObservation]:
    sources = []
    for item in news.articles if news else []:
        article_type = str(item.article_type or "").lower()
        publisher = f"{item.publisher} {item.provider}".lower()
        if item.source_tier == 1 and (
            article_type in {"issuer_release", "official_release"}
            or any(token in publisher for token in ("investor relations", "newsroom", "issuer"))
        ):
            source_class = "issuer_announcement"
        elif article_type in {"management_interview", "press_interview"}:
            source_class = "management_press_interview"
        elif article_type in {"issuer_remarks", "prepared_remarks"}:
            source_class = "issuer_management_remarks"
        elif article_type == "reporting":
            source_class = "contemporaneous_reporting"
        elif article_type == "opinion":
            source_class = "reputable_commentary"
        else:
            source_class = "documented_case_study"
        sources.append(AnalogSourceObservation(
            source_id=_stable_id("analog-source", item.observation_id),
            provider=item.provider,
            title=item.headline,
            source_class=source_class,
            source_origin_group=item.source_origin_group,
            published_at=item.published_at,
            url=item.url,
            evidence_id=item.evidence_id,
            source_tier=item.source_tier,
            licensing_policy=item.licensing_policy,
            excerpt=item.excerpt,
            contemporaneous=source_class == "contemporaneous_reporting",
        ))
    for item in external_evidence.evidence if external_evidence else []:
        if item.source_type not in {"macro_series", "factor_return", "external_analyst_context"}:
            continue
        citation = item.citation
        sources.append(AnalogSourceObservation(
            source_id=_stable_id("analog-external", item.provider, item.title, item.source_as_of),
            provider=item.provider,
            title=item.title,
            source_class=(
                "quantitative_market_data"
                if item.source_type in {"macro_series", "factor_return"}
                else "reputable_commentary"
            ),
            source_origin_group=item.provider.lower().replace(" ", "_"),
            published_at=item.source_as_of,
            url=citation.url if citation else "",
            evidence_id=_stable_id("analog-evidence", item.provider, item.title, item.source_as_of),
            source_tier=item.source_tier,
            licensing_policy=item.licensing_policy,
            excerpt=item.summary,
            quantitative=item.metric_value is not None,
            contemporaneous=item.lookahead_safe,
            citation=citation,
        ))
    for index, summary in enumerate(
        historical_research.trend_summaries if historical_research else []
    ):
        accession = (
            historical_research.filing_accessions[index]
            if index < len(historical_research.filing_accessions)
            else ""
        )
        sources.append(AnalogSourceObservation(
            source_id=_stable_id("analog-sec-history", historical_research.ticker, accession, summary),
            provider="SEC EDGAR historical filing comparison",
            title=f"{historical_research.ticker} historical filing comparison",
            source_class="institutional_history",
            source_origin_group="sec_edgar",
            published_at=None,
            url="",
            evidence_id=accession or _stable_id("historical-summary", summary),
            source_tier=1,
            licensing_policy="public_primary_source_excerpt",
            excerpt=summary,
            quantitative=any(character.isdigit() for character in summary),
            contemporaneous=True,
            citation=(
                Citation(
                    source="SEC EDGAR historical filing comparison",
                    url="",
                    accession=accession or None,
                    source_tier=1,
                    snippet=summary,
                )
                if summary else None
            ),
        ))
    return sources


def _assess_candidate(
    candidate: HistoricalAnalogCandidate,
    signature: HistoricalAnalogSignature,
    sources: list[AnalogSourceObservation],
) -> HistoricalAnalogAssessment:
    links = [
        _link_source_to_candidate(candidate, source)
        for source in sources
    ]
    matched_links = [
        item for item in links
        if item.status == "accepted" and item.relevance_score >= 60
    ]
    rejected_links = [
        item for item in links
        if item.status != "accepted" or item.relevance_score < 60
    ]
    by_source_id = {source.source_id: source for source in sources}
    matched = [
        by_source_id[item.source_id]
        for item in sorted(
            matched_links, key=lambda link: link.relevance_score, reverse=True
        )
        if item.source_id in by_source_id
    ]
    origins = {item.source_origin_group for item in matched}
    qualifying = any(_qualifying_historical_source(item) for item in matched)
    source_relevance = (
        round(sum(item.relevance_score for item in matched_links) / len(matched_links))
        if matched_links else 0
    )
    exact_episode = bool(
        candidate.mechanism
        and candidate.period_start
        and candidate.period_end
        and candidate.period_precision != "unknown"
    )
    status = (
        "Investigated Draft"
        if exact_episode and matched and source_relevance >= 60
        else "Exploratory Candidate"
    )
    dimension_scores = _candidate_dimension_scores(
        candidate, signature, source_relevance
    )
    # Numeric composite similarity is withheld until a Sol audit has passed.
    similarity = None
    differences = _material_differences(candidate, signature, matched)
    outcome = HistoricalOutcomePath(
        operating_outcome="Source retrieval must establish the operating outcome.",
        financing_outcome="Source retrieval must establish financing and dilution outcomes.",
        valuation_outcome="Source retrieval must establish valuation compression or expansion.",
        price_outcome="Qualitative until traceable historical price data is attached.",
        quantitative_verified=any(item.quantitative for item in matched),
    )
    classification = (
        "Quantitative analog" if outcome.quantitative_verified and status == "Investigated Draft"
        else "Contemporary market narrative" if any(item.contemporaneous for item in matched)
        else "Documented case study" if status == "Investigated Draft"
        else "Exploratory analogy"
    )
    return HistoricalAnalogAssessment(
        analog_id=candidate.analog_id,
        analog_class=candidate.analog_class,
        status=status,
        classification=classification,
        episode=candidate.episode,
        companies=list(candidate.companies),
        selection_rationale=candidate.selection_rationale,
        similarity_score=similarity,
        dimension_scores=dimension_scores,
        similarity_band=_similarity_band_from_dimensions(dimension_scores),
        source_relevance_score=source_relevance,
        source_links=matched_links[:12],
        rejected_sources=sorted(
            _dedupe_source_links(rejected_links),
            key=lambda item: item.relevance_score,
            reverse=True,
        )[:20],
        material_differences=differences,
        outcome=outcome,
        what_it_suggests=(
            "Use the analog to test whether demand conversion and utilization can outrun capital intensity and financing cost."
            if "capital" in " ".join(candidate.search_terms).lower()
            else "Use the analog to challenge the durability and valuation implications of the current driver."
        ),
        what_it_cannot_establish=(
            "The analogy cannot establish direction, fair value, probability, or timing for the current security."
        ),
        lessons=[
            "Track the operating KPI that converts the narrative into cash returns before relying on the historical resemblance.",
            "Treat differences in market structure, financing, regulation, and technology as possible analogy breakers.",
        ],
        confirm_kpis=_confirm_kpis(signature),
        reject_kpis=_reject_kpis(signature),
        sources=matched[:8],
        source_quality=(
            "Candidate-relevant source attached; Sol audit still required"
            if status == "Investigated Draft"
            else "Exploratory; episode-specific evidence incomplete"
        ),
        confidence="Medium" if status == "Investigated Draft" and len(origins) >= 2 and qualifying else "Low",
    )


def _link_source_to_candidate(
    candidate: HistoricalAnalogCandidate,
    source: AnalogSourceObservation,
) -> AnalogSourceLink:
    text = f"{source.title} {source.excerpt}".lower()
    text_tokens = set(_tokens(text))
    entity_tokens = {
        token
        for company in candidate.companies
        for token in _tokens(company)
        if token not in {"inc", "corp", "corporation", "company", "limited", "systems"}
    }
    mechanism_tokens = {
        token for token in _tokens(
            f"{candidate.mechanism} {' '.join(candidate.search_terms)}"
        )
        if token not in {
            "historical", "cycle", "year", "fiscal", "company", "current",
            "outcome", "market", "technology",
        }
    }
    matched_entities = sorted(entity_tokens & text_tokens)
    matched_mechanisms = sorted(mechanism_tokens & text_tokens)
    entity_score = min(30, len(matched_entities) * 15)
    if candidate.analog_class == "Broad historical case study":
        entity_score = max(entity_score, min(30, len(matched_mechanisms) * 5))
    mechanism_score = min(30, len(matched_mechanisms) * 6)
    period_status, period_score = _source_period_match(candidate, source, text)
    outcome_terms = {
        "revenue", "margin", "cash", "debt", "inventory", "valuation",
        "price", "utilization", "capex", "write", "repurchase", "leverage",
    }
    outcome_coverage = sorted(outcome_terms & text_tokens)
    outcome_score = min(10, len(outcome_coverage) * 2)
    quality_score = _source_quality_score(source)
    relevance = min(
        100,
        entity_score + mechanism_score + period_score + outcome_score + quality_score,
    )
    reasons: list[str] = []
    current_feed_without_history = (
        source.source_class == "contemporaneous_reporting"
        and period_status == "outside_or_unstated"
        and not _contains_episode_year(text, candidate)
    )
    if not matched_entities and len(matched_mechanisms) < 2:
        reasons.append("entity_or_mechanism_mismatch")
    if current_feed_without_history:
        reasons.append("current_feed_does_not_analyze_historical_episode")
    if relevance < 60:
        reasons.append("source_relevance_below_60")
    status = "accepted" if not reasons else "rejected"
    return AnalogSourceLink(
        source_id=source.source_id,
        analog_id=candidate.analog_id,
        relevance_score=relevance,
        status=status,
        matched_entities=matched_entities,
        matched_mechanisms=matched_mechanisms,
        episode_period_match=period_status,
        outcome_coverage=outcome_coverage,
        source_quality_score=quality_score,
        rejection_reasons=_dedupe(reasons),
        source_title=source.title,
        source_provider=source.provider,
        source_published_at=source.published_at,
        rejection_summary=_rejection_summary(reasons),
    )


def _candidate_dimension_scores(
    candidate: HistoricalAnalogCandidate,
    signature: HistoricalAnalogSignature,
    source_relevance: int,
) -> dict[str, int]:
    context = f"{candidate.mechanism} {' '.join(candidate.search_terms)}".lower()
    driver_tokens = set(_tokens(signature.driver))
    mechanism_tokens = set(_tokens(context))
    mechanism_overlap = len(driver_tokens & mechanism_tokens)
    economic = min(95, 50 + mechanism_overlap * 15) if candidate.mechanism else 25
    operating = 75 if any(
        token in context for token in ("revenue", "margin", "utilization", "inventory", "adoption", "cash")
    ) else 50
    capital = 80 if any(
        token in context for token in ("capex", "capacity", "financing", "debt", "commitment", "repurchase")
    ) else 45
    valuation = 70 if "valuation" in " ".join(candidate.expected_outcomes).lower() else 45
    structure = 70 if candidate.analog_class != "Broad historical case study" else 40
    macro = 45 if signature.macro_regime == "Unknown" else 65
    return {
        "economic_mechanism": economic,
        "operating_economics": operating,
        "capital_intensity_financing": capital,
        "valuation": valuation,
        "competitive_regulatory_structure": structure,
        "macro_regime": macro,
        "source_quality": min(100, source_relevance),
    }


def _material_differences(
    candidate: HistoricalAnalogCandidate,
    signature: HistoricalAnalogSignature,
    sources: list[AnalogSourceObservation],
) -> list[str]:
    differences = []
    if candidate.analog_class == "Broad historical case study":
        differences.append("Industry structure, technology, regulation, capital markets, and accounting may not transfer across eras.")
    if signature.macro_regime == "Unknown":
        differences.append("The current and historical macro regimes have not been aligned.")
    if not sources:
        differences.append("No retrieved source yet documents the proposed episode against the current signature.")
    if signature.utilization_signal == "Unknown":
        differences.append("Current utilization and capacity-conversion evidence is missing.")
    return differences or ["Material differences were reviewed but require more source-specific evidence."]


def _confirm_kpis(signature: HistoricalAnalogSignature) -> list[str]:
    values = ["Revenue or backlog conversion", "Utilization or volume", "Free-cash-flow conversion"]
    if signature.capex_intensity_pct is not None:
        values.append("Capex intensity")
    if signature.leverage_signal != "Unknown":
        values.append("Interest coverage and refinancing")
    return values


def _reject_kpis(signature: HistoricalAnalogSignature) -> list[str]:
    values = ["Demand conversion falls while committed capacity continues rising", "Cash returns fail to cover reinvestment"]
    if signature.leverage_signal == "Elevated financing exposure":
        values.append("Financing cost or dilution absorbs operating improvement")
    return values


def _metric_map(metrics: list[FinancialMetric]) -> dict[str, float]:
    result = {}
    for item in sorted(metrics, key=lambda metric: metric.period_end, reverse=True):
        result.setdefault(item.name.lower(), item.value)
    return result


def _latest_metrics(
    metrics: list[FinancialMetric],
) -> dict[str, FinancialMetric]:
    result: dict[str, FinancialMetric] = {}
    for item in sorted(metrics, key=lambda metric: metric.period_end, reverse=True):
        result.setdefault(item.name.lower(), item)
    return result


def _metric_summary(
    latest: dict[str, FinancialMetric],
    names: tuple[str, ...],
    fallback: str = "Unknown",
) -> str:
    values = []
    for name in names:
        item = latest.get(name)
        if not item:
            continue
        change = (
            f", {item.yoy_change_pct:+.1f}% YoY"
            if item.yoy_change_pct is not None else ""
        )
        values.append(
            f"{item.name} {_format_metric_value(item.value, item.unit)} "
            f"for {item.period_end}{change}"
        )
    return "; ".join(values) if values else fallback


def _fcf_baseline(latest: dict[str, FinancialMetric]) -> str:
    direct = latest.get("free cash flow")
    if direct:
        return _metric_summary(latest, ("free cash flow",))
    operating = latest.get("operating cash flow")
    capex = latest.get("capital expenditure")
    if operating and capex and operating.period_end == capex.period_end:
        value = operating.value - abs(capex.value)
        return (
            f"Derived FCF {_format_metric_value(value, operating.unit)} for "
            f"{operating.period_end} = operating cash flow minus capex"
        )
    return "Unknown; period-aligned operating cash flow and capex are required."


def _format_metric_value(value: float, unit: str) -> str:
    magnitude = abs(value)
    if magnitude >= 1_000_000_000:
        rendered = f"{value / 1_000_000_000:.1f}B"
    elif magnitude >= 1_000_000:
        rendered = f"{value / 1_000_000:.1f}M"
    else:
        rendered = f"{value:,.2f}"
    return f"{rendered} {unit}".strip()


def _event_change(ideas: list[TradeIdea], names: tuple[str, ...]) -> float | None:
    for idea in ideas:
        for event in idea.source_events:
            text = f"{event.title} {event.summary} {event.metrics.get('metric_name', '')}".lower()
            if not any(name in text for name in names):
                continue
            for key in ("yoy_change_pct", "change_pct", "percent_change"):
                try:
                    return float(event.metrics[key])
                except (KeyError, TypeError, ValueError):
                    continue
    return None


def _utilization_signal(ideas: list[TradeIdea]) -> str:
    text = " ".join(
        f"{event.title} {event.summary}"
        for idea in ideas for event in idea.source_events
    ).lower()
    if "utilization" in text:
        return "Evidence detected"
    if "backlog" in text or "remaining performance" in text:
        return "Backlog evidence exists; utilization conversion remains unverified"
    return "Unknown"


def _valuation_signal(top: TradeIdea | None) -> str:
    if (
        top
        and top.payoff_model
        and top.payoff_model.payoff_completeness
        and top.payoff_model.payoff_completeness.status == "Complete"
    ):
        return "Scenario payoff available"
    return "Valuation comparison incomplete"


def _macro_regime(bundle: ExternalEvidenceBundle | None) -> str:
    text = " ".join(
        f"{item.title} {item.summary} {' '.join(item.tags)}"
        for item in bundle.evidence if bundle
    ).lower()
    if "credit spread" in text or "financial stress" in text:
        return "Rates and credit regime evidence available"
    if "treasury" in text or "interest rate" in text:
        return "Rates regime evidence available"
    return "Unknown"


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in {None, 0}:
        return None
    return numerator / denominator * 100


def _dimension_score(value: float | None, candidate: HistoricalAnalogCandidate, keyword: str) -> int:
    if value is None:
        return 35
    return 75 if keyword in " ".join(candidate.search_terms).lower() else 55


def frozen_demo_episode_dossiers(identity: CompanyIdentity) -> list[HistoricalEpisodeDossier]:
    if identity.ticker.upper() != "AAPL":
        return []
    sources = [
        AnalogSourceObservation(
            source_id="aapl-fy2013-10k",
            provider="SEC EDGAR",
            title="Apple FY2013 Form 10-K",
            source_class="historical_filing",
            source_origin_group="sec_edgar",
            published_at="2013-10-30",
            url="https://www.sec.gov/Archives/edgar/data/320193/000119312513416534/d590790d10k.htm",
            evidence_id="000119312513416534",
            source_tier=1,
            licensing_policy="public_primary_source_excerpt",
            excerpt=(
                "Apple reported FY2013 net sales of 170.9B USD and operating "
                "cash flow of 53.7B USD while expanding its capital-return program."
            ),
            quantitative=True,
            contemporaneous=True,
            citation=Citation(
                source="Apple FY2013 Form 10-K",
                url="https://www.sec.gov/Archives/edgar/data/320193/000119312513416534/d590790d10k.htm",
                filed="2013-10-30",
                form="10-K",
                accession="000119312513416534",
                period_end="2013-09-28",
                source_tier=1,
            ),
        ),
        AnalogSourceObservation(
            source_id="aapl-fy2016-10k",
            provider="SEC EDGAR",
            title="Apple FY2016 Form 10-K",
            source_class="institutional_history",
            source_origin_group="sec_edgar",
            published_at="2016-10-26",
            url="https://www.sec.gov/Archives/edgar/data/320193/000162828016020309/aapl-20160924x10k.htm",
            evidence_id="000162828016020309",
            source_tier=1,
            licensing_policy="public_primary_source_excerpt",
            excerpt=(
                "Apple reported FY2016 net sales of 215.6B USD, Services net sales of 24.3B USD, "
                "and operating cash flow of 65.8B USD."
            ),
            quantitative=True,
            contemporaneous=True,
            citation=Citation(
                source="Apple FY2016 Form 10-K",
                url="https://www.sec.gov/Archives/edgar/data/320193/000162828016020309/aapl-20160924x10k.htm",
                filed="2016-10-26",
                form="10-K",
                accession="000162828016020309",
                period_end="2016-09-24",
                source_tier=1,
            ),
        ),
        AnalogSourceObservation(
            source_id="aapl-fy2018-10k",
            provider="SEC EDGAR",
            title="Apple FY2018 Form 10-K",
            source_class="historical_filing",
            source_origin_group="sec_edgar",
            published_at="2018-11-05",
            url="https://www.sec.gov/Archives/edgar/data/320193/000032019318000145/aapl-20180929x10k.htm",
            evidence_id="000032019318000145",
            source_tier=1,
            licensing_policy="public_primary_source_excerpt",
            excerpt=(
                "Apple reported FY2018 net sales of 265.6B USD, Services net "
                "sales of 39.7B USD, and operating cash flow of 77.4B USD."
            ),
            quantitative=True,
            contemporaneous=True,
            citation=Citation(
                source="Apple FY2018 Form 10-K",
                url="https://www.sec.gov/Archives/edgar/data/320193/000032019318000145/aapl-20180929x10k.htm",
                filed="2018-11-05",
                form="10-K",
                accession="000032019318000145",
                period_end="2018-09-29",
                source_tier=1,
            ),
        ),
        AnalogSourceObservation(
            source_id="aapl-fy2020-10k",
            provider="SEC EDGAR",
            title="Apple FY2020 Form 10-K",
            source_class="institutional_history",
            source_origin_group="sec_edgar",
            published_at="2020-10-30",
            url="https://www.sec.gov/Archives/edgar/data/320193/000032019320000096/aapl-20200926.htm",
            evidence_id="000032019320000096",
            source_tier=1,
            licensing_policy="public_primary_source_excerpt",
            excerpt=(
                "Apple reported FY2020 net sales of 274.5B USD, Services net sales of 53.8B USD, "
                "and operating cash flow of 80.7B USD while continuing repurchases."
            ),
            quantitative=True,
            contemporaneous=True,
            citation=Citation(
                source="Apple FY2020 Form 10-K",
                url="https://www.sec.gov/Archives/edgar/data/320193/000032019320000096/aapl-20200926.htm",
                filed="2020-10-30",
                form="10-K",
                accession="000032019320000096",
                period_end="2020-09-26",
                source_tier=1,
            ),
        ),
        AnalogSourceObservation(
            source_id="aapl-nasdaq-price-history",
            provider="Nasdaq historical market data",
            title="AAPL historical price series",
            source_class="quantitative_market_data",
            source_origin_group="nasdaq_market_data",
            published_at="2020-09-26",
            url="https://www.nasdaq.com/market-activity/stocks/aapl/historical",
            evidence_id="aapl-historical-price-series",
            source_tier=3,
            licensing_policy="metadata_and_link_only",
            excerpt=(
                "The linked split-adjusted historical series supplies price-path context; "
                "the frozen fixture does not use the analogy to assign a current return forecast."
            ),
            quantitative=True,
            contemporaneous=True,
            citation=Citation(
                source="Nasdaq AAPL historical price series",
                url="https://www.nasdaq.com/market-activity/stocks/aapl/historical",
                source_tier=3,
            ),
        ),
    ]
    dossiers = [HistoricalEpisodeDossier(
        analog_id=_stable_id(
            "historical-analog",
            "AAPL",
            "Same-company prior cycle",
            "Apple Inc. prior strategic and capital-allocation cycle",
        ),
        status="Frozen Demo Dossier",
        episode="Apple services-mix and capital-return cycle, FY2016-FY2020",
        start_date="2016-09-24",
        end_date="2020-09-26",
        companies=["Apple Inc."],
        securities=["AAPL common stock"],
        triggering_decisions=[
            "Expanded the Services ecosystem while the installed base became a larger monetization driver.",
            "Continued large repurchases and dividends alongside operating reinvestment.",
        ],
        operating_outcome=(
            "SEC filings show Services net sales rising from 24.3B USD in FY2016 to 53.8B USD "
            "in FY2020, total net sales rising from 215.6B USD to 274.5B USD, and operating "
            "cash flow increasing from 65.8B USD to 80.7B USD."
        ),
        financing_outcome=(
            "Apple combined internally generated cash with sustained repurchases and dividends. "
            "The 2020 stock split means raw share counts must be split-adjusted before comparing per-share outcomes."
        ),
        valuation_outcome=(
            "The episode paired a higher recurring-services mix and stronger cash generation with "
            "capital return; the evidence does not isolate how much valuation change came from each mechanism."
        ),
        price_outcome=(
            "The linked Nasdaq split-adjusted series provides the traceable price path. The demo "
            "treats appreciation during the episode as context, not as a forecast or calibrated analog return."
        ),
        macro_context=(
            "The episode spans a 2016 hardware-demand slowdown and the 2020 pandemic shock, so its "
            "rates, demand, and supply-chain regimes do not cleanly match the current period."
        ),
        competitive_context=(
            "Apple's integrated hardware, software, and services ecosystem limited direct comparability "
            "with stand-alone hardware or internet-services peers."
        ),
        dimensions=[
            AnalogDimensionComparison(
                "Cash conversion",
                "Current demo: operating cash flow growth and lower capex support near-term conversion.",
                "FY2016-FY2020: operating cash flow rose from 65.8B USD to 80.7B USD.",
                86,
                "Strong same-company mechanism match, subject to working-capital and product-cycle differences.",
                ["aapl-fy2016-10k", "aapl-fy2020-10k"],
            ),
            AnalogDimensionComparison(
                "Mix and margin",
                "Current demo: gross margin expanded while revenue and gross profit grew.",
                "FY2016-FY2020: Services more than doubled and became a larger sales-mix contributor.",
                82,
                "Services mix is relevant, but the current product, geographic, and regulatory mix differs.",
                ["aapl-fy2016-10k", "aapl-fy2020-10k"],
            ),
            AnalogDimensionComparison(
                "Capital return",
                "Current demo: diluted shares fell while repurchase cash spending declined.",
                "Prior episode: sustained repurchases supported per-share compounding.",
                75,
                "The mechanism matches, but current reinvestment requirements and valuation are different.",
                ["aapl-fy2020-10k"],
            ),
        ],
        material_differences=[
            "The current product, regulatory, supply-chain, and interest-rate regimes differ from FY2016-FY2020.",
            "Current repurchase spending is not following the same path as the prior cycle.",
        ],
        lessons=[
            "A durable Apple thesis requires margin and cash conversion to persist together; a larger cash balance alone is not enough.",
            "Per-share compounding depends on the balance between high-return reinvestment and sustained share reduction.",
        ],
        limitations=[
            "The analogy cannot establish current direction, fair value, probability, or timing.",
            "The market-data link supplies price context, but no analog return is used in ranking or calibration.",
        ],
        confirm_kpis=[
            "Gross margin remains above the prior-year comparison.",
            "Operating cash flow growth remains positive after working-capital normalization.",
            "Diluted shares continue to decline or reinvestment returns compensate for slower repurchases.",
        ],
        reject_kpis=[
            "Gross margin and operating cash flow reverse together.",
            "R&D and capex rise without corresponding revenue, margin, or retention evidence.",
        ],
        linked_monitor_rules=[
            "gross_margin_pct >= 47.0",
            "diluted_shares_yoy_pct <= 0.0",
            "operating_cash_flow_yoy_pct > 0.0",
        ],
        source_ids=[source.source_id for source in sources],
        sources=sources,
        source_quality="Primary SEC filings plus independent historical market-data context",
        confidence="Medium",
        quantitative_verified=True,
        researched_at="2026-07-27T00:00:00+00:00",
        cache_key="frozen-aapl-services-capital-return-v1",
        period_precision="fiscal_year",
        similarity_band="Strong",
        audited_similarity_score=None,
        audit_status="deterministic_fixture",
        hidden_risks=[
            "A services-mix analogy can obscure product-cycle, regulatory, supplier-commitment, and valuation differences.",
        ],
        quality_scorecard=AnalogQualityScorecard(
            total_score=88,
            status="fixture_validated",
            current_baseline=10,
            exact_episode=10,
            chronology=8,
            mechanism=10,
            similarities=8,
            differences=10,
            outcome_path=16,
            hidden_risks=8,
            lessons_and_monitors=8,
            citation_relevance=8,
            notes=[
                "Curated deterministic benchmark fixture; no live model provenance is claimed."
            ],
        ),
        generation_provenance={
            "status": "curated_deterministic_benchmark",
            "live_model": "no",
            "prompt_version": ANALOG_PROMPT_VERSION,
        },
    )]
    dossiers.append(HistoricalEpisodeDossier(
        analog_id=_stable_id(
            "historical-analog",
            "AAPL",
            "Same economic or financial mechanism",
            "Apple iPhone normalization and debt-funded capital return, FY2013-FY2016",
        ),
        status="Frozen Demo Dossier",
        episode="Apple iPhone normalization and debt-funded capital return, FY2013-FY2016",
        start_date="FY2013",
        end_date="FY2016",
        period_precision="fiscal_year",
        companies=["Apple Inc."],
        securities=["AAPL common stock"],
        triggering_decisions=[
            "Apple expanded its capital-return authorization while overseas cash constraints encouraged debt issuance.",
            "The iPhone franchise moved from rapid growth into a FY2016 revenue normalization.",
        ],
        operating_outcome=(
            "The episode tests whether product-cycle normalization can coexist "
            "with high cash generation and per-share support. FY2013 and FY2016 "
            "filings provide the operating and cash-flow anchors."
        ),
        financing_outcome=(
            "Debt-funded repurchases changed the balance-sheet mix while reducing "
            "share count; the financing mechanism differs from a purely excess-cash-funded return."
        ),
        valuation_outcome=(
            "Product-cycle uncertainty and slower growth can compress the multiple "
            "even when buybacks support EPS; current valuation must therefore be tested separately."
        ),
        price_outcome=(
            "Use the linked split-adjusted market series to validate the path; "
            "the fixture does not convert this episode into a current return forecast."
        ),
        macro_context=(
            "The episode predates the current rates, regulation, AI-device, and "
            "supply-chain regimes."
        ),
        competitive_context=(
            "Apple was more dependent on iPhone unit economics and had a smaller "
            "Services contribution than in the current business mix."
        ),
        dimensions=[
            AnalogDimensionComparison(
                "economic_mechanism",
                "Current thesis must separate product demand from cash-return support.",
                "FY2013-FY2016 combined iPhone-cycle normalization with an enlarged capital-return program.",
                88,
                "Strong same-company mechanism match.",
                ["aapl-fy2013-10k", "aapl-fy2016-10k"],
            ),
            AnalogDimensionComparison(
                "capital_intensity_financing",
                "Current supplier commitments and leverage must be compared with owned physical capex.",
                "Repurchases were partly financed with debt while much cash remained offshore.",
                78,
                "Relevant financing analogy, but tax and cash-location constraints differ.",
                ["aapl-fy2013-10k", "aapl-fy2016-10k"],
            ),
            AnalogDimensionComparison(
                "valuation",
                "Current valuation embeds a different services mix and growth expectation.",
                "Cycle risk mattered even when cash generation and buybacks were substantial.",
                76,
                "Moderate valuation-mechanism match.",
                ["aapl-nasdaq-price-history"],
            ),
        ],
        material_differences=[
            "Services is now a much larger share of revenue and gross profit.",
            "Current off-balance-sheet purchase obligations and supplier-owned capacity require separate analysis.",
            "Tax, rates, regulation, installed base, and product breadth differ materially.",
        ],
        hidden_risks=[
            "Repurchases can mask operating normalization in per-share data.",
            "Supplier commitments and prepayments can create economic capacity exposure without appearing as owned capex.",
        ],
        lessons=[
            "Separate product-cycle earnings power from the mechanical EPS support of buybacks.",
            "Treat purchase obligations and supplier commitments as capital-at-risk even when physical assets are supplier-owned.",
        ],
        limitations=[
            "The analogy cannot establish current direction, fair value, probability, or timing.",
        ],
        confirm_kpis=[
            "Product and Services revenue growth remain positive together.",
            "FCF covers repurchases, dividends, debt service, and committed reinvestment.",
        ],
        reject_kpis=[
            "Product demand normalizes while commitments and capital return consume more cash than FCF.",
            "Net leverage rises without offsetting growth or margin evidence.",
        ],
        linked_monitor_rules=[
            "product_revenue_yoy_pct > 0.0",
            "free_cash_flow_coverage_of_capital_returns >= 1.0",
        ],
        source_ids=[
            "aapl-fy2013-10k",
            "aapl-fy2016-10k",
            "aapl-nasdaq-price-history",
        ],
        sources=[
            source for source in sources
            if source.source_id in {
                "aapl-fy2013-10k",
                "aapl-fy2016-10k",
                "aapl-nasdaq-price-history",
            }
        ],
        source_quality="Two primary SEC filings plus independent market-data context",
        confidence="Medium",
        quantitative_verified=True,
        researched_at="2026-07-27T00:00:00+00:00",
        cache_key="frozen-aapl-iphone-capital-return-v1",
        similarity_band="Strong",
        audited_similarity_score=None,
        audit_status="deterministic_fixture",
        quality_scorecard=AnalogQualityScorecard(
            total_score=89,
            status="fixture_validated",
            current_baseline=10,
            exact_episode=10,
            chronology=8,
            mechanism=10,
            similarities=8,
            differences=10,
            outcome_path=16,
            hidden_risks=8,
            lessons_and_monitors=9,
            citation_relevance=8,
            notes=[
                "Curated deterministic benchmark fixture; no live model provenance is claimed."
            ],
        ),
        generation_provenance={
            "status": "curated_deterministic_benchmark",
            "live_model": "no",
            "prompt_version": ANALOG_PROMPT_VERSION,
        },
    ))
    return dossiers


def _investigation_limit(profile_id: str, investigate_analog_id: str | None) -> int:
    if investigate_analog_id:
        return 1
    return {
        "fast_screening": 0,
        "adaptive_ic": 1,
        "deep_initiation": 3,
        "investigate_event": 1,
    }.get(profile_id, 1)


def _selected_assessments(
    assessments: list[HistoricalAnalogAssessment],
    limit: int,
    investigate_analog_id: str | None,
) -> list[HistoricalAnalogAssessment]:
    if not limit:
        return []
    if investigate_analog_id:
        return [
            item for item in assessments
            if item.analog_id == investigate_analog_id
        ][:1]
    return assessments[:limit]


def _load_cached_dossiers(
    store: Any | None,
    ticker: str,
    signature: HistoricalAnalogSignature,
    selected: list[HistoricalAnalogAssessment],
) -> tuple[list[HistoricalEpisodeDossier], list[HistoricalAnalogAssessment]]:
    loader = getattr(store, "load_historical_analog_dossier", None)
    if not callable(loader):
        return [], list(selected)
    signature_hash = _signature_hash(signature)
    cached: list[HistoricalEpisodeDossier] = []
    misses: list[HistoricalAnalogAssessment] = []
    for assessment in selected:
        payload = loader(ticker, assessment.analog_id, signature_hash)
        dossier = _dossier_from_payload(payload) if payload else None
        if dossier:
            cached.append(dossier)
        else:
            misses.append(assessment)
    return cached, misses


def _save_cached_dossiers(
    store: Any | None,
    ticker: str,
    dossiers: list[HistoricalEpisodeDossier],
    signature_hash: str,
) -> None:
    saver = getattr(store, "save_historical_analog_dossier", None)
    if not callable(saver):
        return
    for dossier in dossiers:
        saver(ticker, dossier, signature_hash)


def _dossier_from_payload(payload: dict | None) -> HistoricalEpisodeDossier | None:
    if not isinstance(payload, dict):
        return None
    try:
        sources = []
        for raw in payload.get("sources", []):
            citation_raw = raw.get("citation")
            citation = Citation(**citation_raw) if isinstance(citation_raw, dict) else None
            sources.append(AnalogSourceObservation(
                **{key: value for key, value in raw.items() if key != "citation"},
                citation=citation,
            ))
        dimensions = [
            AnalogDimensionComparison(**item)
            for item in payload.get("dimensions", [])
            if isinstance(item, dict)
        ]
        audit_raw = payload.get("audit_decision")
        audit = (
            AnalogAuditDecision(**audit_raw)
            if isinstance(audit_raw, dict) else None
        )
        quality_raw = payload.get("quality_scorecard")
        quality = (
            AnalogQualityScorecard(**quality_raw)
            if isinstance(quality_raw, dict) else None
        )
        rejected_sources = [
            AnalogSourceLink(**item)
            for item in payload.get("rejected_sources", [])
            if isinstance(item, dict)
        ]
        return HistoricalEpisodeDossier(
            **{
                key: value for key, value in payload.items()
                if key not in {
                    "sources", "dimensions", "audit_decision",
                    "quality_scorecard", "rejected_sources",
                }
            },
            sources=sources,
            dimensions=dimensions,
            audit_decision=audit,
            quality_scorecard=quality,
            rejected_sources=rejected_sources,
        )
    except (TypeError, ValueError):
        return None


def _dedupe_dossiers(
    dossiers: list[HistoricalEpisodeDossier],
) -> list[HistoricalEpisodeDossier]:
    result = []
    seen = set()
    for dossier in dossiers:
        if dossier.analog_id in seen:
            continue
        seen.add(dossier.analog_id)
        result.append(dossier)
    return result


def _signature_hash(signature: HistoricalAnalogSignature) -> str:
    values = (
        signature.ticker,
        signature.driver,
        signature.industry,
        signature.revenue_growth_pct,
        signature.backlog_growth_pct,
        signature.capex_intensity_pct,
        signature.fcf_conversion_pct,
        signature.leverage_signal,
        signature.valuation_signal,
        signature.macro_regime,
        signature.current_baseline.business_model,
        signature.current_baseline.owned_capacity,
        signature.current_baseline.contracted_capacity,
        signature.current_baseline.purchase_obligations,
        signature.current_baseline.free_cash_flow,
        ANALOG_PROMPT_VERSION,
    )
    return _stable_id(*values)


def _dossier_cache_key(
    analog_id: str,
    source_ids: list[str],
) -> str:
    return _stable_id(ANALOG_PROMPT_VERSION, analog_id, *sorted(source_ids))


def _valid_period_range(
    start: str,
    end: str,
    precision: str,
) -> bool:
    normalized = precision.strip().lower()
    if normalized == "day":
        return _valid_date_range(start, end)
    start_year = _year_from_period(start)
    end_year = _year_from_period(end)
    return (
        normalized in {"quarter", "fiscal_year", "year"}
        and start_year is not None
        and end_year is not None
        and start_year <= end_year
    )


def _valid_date_range(start_date: str, end_date: str) -> bool:
    try:
        start = datetime.fromisoformat(start_date).date()
        end = datetime.fromisoformat(end_date).date()
    except (TypeError, ValueError):
        return False
    return start <= end


def _year_from_period(value: str | None) -> int | None:
    text = str(value or "")
    for index in range(max(0, len(text) - 3)):
        candidate = text[index:index + 4]
        if candidate.isdigit() and 1900 <= int(candidate) <= 2100:
            return int(candidate)
    return None


def _source_period_match(
    candidate: HistoricalAnalogCandidate,
    source: AnalogSourceObservation,
    text: str,
) -> tuple[str, int]:
    start = _year_from_period(candidate.period_start)
    end = _year_from_period(candidate.period_end)
    if start is None or end is None:
        return "candidate_period_pending", 0
    mentioned = {
        year for year in range(start, end + 1)
        if str(year) in text or f"fy{year}" in text
    }
    published_year = _year_from_period(source.published_at)
    if mentioned:
        return "episode_explicitly_discussed", 20
    if published_year is not None and start <= published_year <= end:
        return "published_during_episode", 20
    return "outside_or_unstated", 0


def _contains_episode_year(
    text: str,
    candidate: HistoricalAnalogCandidate,
) -> bool:
    start = _year_from_period(candidate.period_start)
    end = _year_from_period(candidate.period_end)
    return bool(
        start is not None
        and end is not None
        and any(str(year) in text for year in range(start, end + 1))
    )


def _source_quality_score(source: AnalogSourceObservation) -> int:
    if source.source_tier == 1:
        return 10
    if source.source_class in {
        "academic_research",
        "institutional_history",
        "quantitative_market_data",
        "historical_filing",
        "issuer_announcement",
        "regulatory_history",
    }:
        return 9
    if source.source_tier == 2:
        return 8
    if source.source_tier == 3:
        return 6
    return 2


def _qualifying_historical_source(source: AnalogSourceObservation) -> bool:
    return bool(
        source.quantitative
        or source.source_tier in {1, 2}
        or source.source_class in {
            "academic_research",
            "institutional_history",
            "quantitative_market_data",
            "historical_filing",
            "issuer_announcement",
            "regulatory_history",
        }
    )


def _weighted_similarity_score(scores: dict[str, int]) -> int:
    normalized: dict[str, int] = {}
    for dimension, score in scores.items():
        canonical = _canonical_similarity_dimension(dimension)
        if canonical:
            normalized[canonical] = max(
                normalized.get(canonical, 0),
                max(0, min(100, int(score))),
            )
    weighted = sum(
        normalized.get(dimension, 0) * weight
        for dimension, weight in SIMILARITY_WEIGHTS.items()
    )
    return round(weighted / 100)


def _similarity_band_from_dimensions(scores: dict[str, int]) -> str:
    if not scores:
        return "Unscored"
    score = _weighted_similarity_score(scores)
    if score >= 75:
        return "Strong"
    if score >= 55:
        return "Moderate"
    return "Weak"


def _canonical_similarity_dimension(value: str) -> str:
    text = str(value or "").lower().replace("-", " ").replace("_", " ")
    aliases = {
        "economic_mechanism": ("economic", "mechanism", "driver"),
        "operating_economics": ("operating", "cash conversion", "margin", "mix"),
        "capital_intensity_financing": ("capital", "financing", "leverage", "capacity"),
        "valuation": ("valuation", "multiple"),
        "competitive_regulatory_structure": ("competitive", "regulatory", "structure"),
        "macro_regime": ("macro", "rates", "cycle regime"),
        "source_quality": ("source quality", "evidence quality"),
    }
    for canonical, terms in aliases.items():
        if any(term in text for term in terms):
            return canonical
    return ""


def _quality_scorecard_from_draft(
    raw: dict[str, Any],
    sources: list[AnalogSourceObservation],
    dimensions: list[AnalogDimensionComparison],
    *,
    audited: bool,
) -> AnalogQualityScorecard:
    source_origins = {item.source_origin_group for item in sources}
    fields = {
        "current_baseline": 10 if raw.get("current_baseline") else 5,
        "exact_episode": 10 if raw.get("episode") and raw.get("start_date") and raw.get("end_date") else 0,
        "chronology": 8 if raw.get("triggering_decisions") else 0,
        "mechanism": 10 if dimensions else 4,
        "similarities": 8 if raw.get("similarities") or dimensions else 3,
        "differences": 10 if raw.get("material_differences") else 0,
        "outcome_path": 16 if all(raw.get(key) for key in (
            "operating_outcome", "financing_outcome", "valuation_outcome", "price_outcome"
        )) else 6,
        "hidden_risks": 8 if raw.get("hidden_risks") else 0,
        "lessons_and_monitors": 10 if raw.get("lessons") and (
            raw.get("confirm_kpis") or raw.get("linked_monitor_rules")
        ) else 4,
        "citation_relevance": 10 if len(source_origins) >= 2 else 5 if sources else 0,
    }
    total = sum(fields.values())
    critical: list[str] = []
    if len(source_origins) < 2:
        critical.append("fewer_than_two_independent_source_origins")
    if not dimensions:
        critical.append("mechanism_comparison_missing")
    if audited and critical:
        status = "failed"
    elif audited and total >= 85:
        status = "passed"
    elif audited:
        status = "partial"
    else:
        status = "draft"
    return AnalogQualityScorecard(
        total_score=total,
        status=status,
        critical_failures=critical,
        **fields,
    )


def _investigation_reason(
    status: str,
    profile_id: str,
    requested: int,
    selected: int,
) -> str:
    return {
        "frozen_fixture": "The demo uses a precomputed, citation-bound dossier and makes no live LLM or paid-provider call.",
        "disabled": "LLM historical investigation is disabled; deterministic candidates remain visible.",
        "no_provider": "The selected profile permits historical investigation, but no configured LLM provider is available.",
        "not_selected_by_profile": f"{profile_id} performs analog discovery only; use Investigate Further or a deeper profile.",
        "completed": f"Historical investigation completed for {selected or requested} selected analog(s).",
        "partial": "Some selected historical episodes passed validation; others remain unresolved.",
        "timeout": "The LLM investigation timed out; deterministic candidates and retrieved evidence were preserved.",
        "validation_failed": "LLM output returned, but no dossier passed source, date, and citation validation.",
    }.get(status, "Historical investigation status is unavailable.")


def _merge_candidates(
    first: list[HistoricalAnalogCandidate],
    second: list[HistoricalAnalogCandidate],
) -> list[HistoricalAnalogCandidate]:
    seen = set()
    result = []
    for item in first + second:
        key = (item.analog_class.casefold(), item.episode.casefold())
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _tokens(value: str) -> list[str]:
    stop = {"and", "the", "for", "with", "into", "from", "that", "this", "cycle", "historical"}
    return [
        token for token in "".join(
            character.lower() if character.isalnum() else " "
            for character in value
        ).split()
        if len(token) > 3 and token not in stop
    ]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _dedupe(values: Iterable[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        text = str(value).strip()
        if not text or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        result.append(text)
    return result


def _stable_id(*parts: object) -> str:
    return hashlib.sha1("|".join(str(part or "") for part in parts).encode("utf-8")).hexdigest()[:16]


def _safe_error(exc: BaseException) -> str:
    text = str(exc)
    return text[:240] if text else type(exc).__name__


def _is_timeout_message(message: str) -> bool:
    normalized = (message or "").lower().replace("-", " ")
    return any(token in normalized for token in ("timeout", "timed out"))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
