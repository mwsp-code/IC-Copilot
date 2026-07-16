from __future__ import annotations

import hashlib
import re
from dataclasses import asdict
from datetime import datetime, timezone

from .models import (
    AnalystDebateMap,
    ChangeEvent,
    Citation,
    CompanyIdentity,
    CompanyEconomics,
    ExternalEvidence,
    ExternalEvidenceBundle,
    ExternalNarrativeScore,
    ExternalResearchExcerpt,
    MarketCapture,
    MonitorItem,
    ResearchSourcePlan,
    ResearchSourceRequest,
    ScoreBreakdown,
    TradeIdea,
    WisburgResearchLens,
    WisburgCoverageAudit,
    WisburgCorroborationDecision,
    WisburgReportRecord,
    WisburgResearchTask,
    WisburgRevisionObservation,
    WisburgSourceSuggestion,
    WisburgStructuredClaim,
    WisburgTheme,
    WisburgToolEntitlement,
)
from .wisburg_intelligence import enrich_source_plan_with_wisburg_tasks


WISBURG_CONTEXT_TYPES = {
    "external_analyst_context",
    "management_transcript_context",
    "external_market_context",
}

THEME_DEFINITIONS: tuple[tuple[str, str, str, tuple[str, ...], tuple[str, ...], tuple[str, ...]], ...] = (
    ("ai_cloud", "AI / cloud monetization", "Cloud", ("ai", "cloud", "maas", "云", "人工智能", "通义"), ("growth", "improve", "upside", "拐点", "增长", "上调"), ("weak", "cut", "pressure", "疲软", "下调")),
    ("china_commerce", "China commerce demand", "China commerce", ("commerce", "ecommerce", "retail", "gmv", "淘宝", "天猫", "电商", "零售"), ("growth", "recover", "monetization", "复苏", "增长"), ("weak", "soft", "competition", "疲软", "放缓", "竞争")),
    ("international", "International commerce", "International commerce", ("international", "global", "aliexpress", "lazada", "海外", "国际"), ("growth", "scale", "expansion", "增长", "扩张"), ("loss", "competition", "亏损", "竞争")),
    ("local_services", "Local services / instant retail", "Local services", ("local services", "instant retail", "ele.me", "到店", "本地生活", "即时零售"), ("narrow", "unit economics", "收窄", "转正"), ("loss", "subsidy", "亏损", "补贴")),
    ("logistics", "Logistics and fulfillment", "Logistics", ("logistics", "cainiao", "fulfillment", "菜鸟", "物流"), ("efficiency", "margin", "效率"), ("cost", "loss", "成本", "亏损")),
    ("buybacks", "Buybacks / capital return", "Buybacks", ("buyback", "repurchase", "capital return", "回购", "股东回报"), ("accretive", "support", "提升"), ("dilution", "offset", "稀释")),
    ("fx_policy", "RMB/USD and policy risk", "Policy risk", ("policy", "regulation", "rmb", "cny", "fx", "政策", "监管", "人民币", "汇率"), ("support", "easing", "支持", "宽松"), ("risk", "tighten", "风险", "收紧")),
    ("target_rating", "External target / rating debate", "Market expectations", ("target", "rating", "buy", "sell", "目标价", "评级", "买入", "卖出"), ("upside", "upgrade", "上调", "买入"), ("downgrade", "cut", "下调", "卖出")),
    ("margin_profit", "Margin / EBITA debate", "Margin / mix", ("margin", "ebita", "profit", "利润率", "利润", "盈利"), ("improve", "leverage", "提升", "改善"), ("pressure", "deleverage", "下调", "承压")),
)


# Keep the effective dictionary ASCII-safe so Chinese Wisburg excerpts are
# matched consistently across Windows terminals and repository clones.
THEME_DEFINITIONS = (
    (
        "ai_cloud",
        "AI / cloud monetization",
        "Cloud",
        ("ai", "cloud", "maas", "\u4eba\u5de5\u667a\u80fd", "\u901a\u4e49"),
        ("growth", "improve", "upside", "\u62d0\u70b9", "\u589e\u957f", "\u4e0a\u8c03"),
        ("weak", "cut", "pressure", "\u75b2\u8f6f", "\u4e0b\u8c03"),
    ),
    (
        "china_commerce",
        "China commerce demand",
        "China commerce",
        ("commerce", "ecommerce", "retail", "gmv", "\u6dd8\u5b9d", "\u5929\u732b", "\u7535\u5546", "\u96f6\u552e"),
        ("growth", "recover", "monetization", "\u590d\u82cf", "\u589e\u957f"),
        ("weak", "soft", "competition", "\u75b2\u8f6f", "\u653e\u7f13", "\u7ade\u4e89"),
    ),
    (
        "international",
        "International commerce",
        "International commerce",
        ("international", "global", "aliexpress", "lazada", "\u6d77\u5916", "\u56fd\u9645"),
        ("growth", "scale", "expansion", "\u589e\u957f", "\u6269\u5f20"),
        ("loss", "competition", "\u4e8f\u635f", "\u7ade\u4e89"),
    ),
    (
        "local_services",
        "Local services / instant retail",
        "Local services",
        ("local services", "instant retail", "ele.me", "\u5230\u5e97", "\u672c\u5730\u751f\u6d3b", "\u5373\u65f6\u96f6\u552e"),
        ("narrow", "unit economics", "\u6536\u7a84", "\u8f6c\u6b63"),
        ("loss", "subsidy", "\u4e8f\u635f", "\u8865\u8d34"),
    ),
    (
        "logistics",
        "Logistics and fulfillment",
        "Logistics",
        ("logistics", "cainiao", "fulfillment", "\u83dc\u9e1f", "\u7269\u6d41"),
        ("efficiency", "margin", "\u6548\u7387"),
        ("cost", "loss", "\u6210\u672c", "\u4e8f\u635f"),
    ),
    (
        "buybacks",
        "Buybacks / capital return",
        "Buybacks",
        ("buyback", "repurchase", "capital return", "\u56de\u8d2d", "\u80a1\u4e1c\u56de\u62a5"),
        ("accretive", "support", "\u63d0\u5347"),
        ("dilution", "offset", "\u7a00\u91ca"),
    ),
    (
        "fx_policy",
        "RMB/USD and policy risk",
        "Policy risk",
        ("policy", "regulation", "rmb", "cny", "fx", "\u653f\u7b56", "\u76d1\u7ba1", "\u4eba\u6c11\u5e01", "\u6c47\u7387"),
        ("support", "easing", "\u652f\u6301", "\u5bbd\u677e"),
        ("risk", "tighten", "\u98ce\u9669", "\u6536\u7d27"),
    ),
    (
        "target_rating",
        "External target / rating debate",
        "Market expectations",
        ("target", "rating", "buy", "sell", "\u76ee\u6807\u4ef7", "\u8bc4\u7ea7", "\u4e70\u5165", "\u5356\u51fa"),
        ("upside", "upgrade", "\u4e0a\u8c03", "\u4e70\u5165"),
        ("downgrade", "cut", "\u4e0b\u8c03", "\u5356\u51fa"),
    ),
    (
        "margin_profit",
        "Margin / EBITA debate",
        "Margin / mix",
        ("margin", "ebita", "profit", "\u5229\u6da6\u7387", "\u5229\u6da6", "\u76c8\u5229"),
        ("improve", "leverage", "\u63d0\u5347", "\u6539\u5584"),
        ("pressure", "deleverage", "\u4e0b\u8c03", "\u627f\u538b"),
    ),
)


def build_wisburg_lens(
    identity: CompanyIdentity,
    external_evidence: ExternalEvidenceBundle,
    economics: CompanyEconomics | None = None,
) -> WisburgResearchLens:
    observed_at = _utc_now()
    wisburg_items = [
        item for item in external_evidence.evidence
        if item.provider == "Wisburg research" and item.source_type in WISBURG_CONTEXT_TYPES
    ]
    excerpts = [_excerpt_from_evidence(identity, item, index) for index, item in enumerate(wisburg_items)]
    coverage_audit = _coverage_audit_from_evidence(identity, external_evidence)
    reports, structured_claims, revisions = _structured_intelligence_from_evidence(external_evidence)
    themes = _themes_from_excerpts(excerpts, economics)
    debate = _debate_from_themes(themes)
    narrative = _narrative_score(excerpts, themes)
    suggestions = _source_suggestions(identity, themes, debate)
    caveats = [
        "Wisburg is external analyst/narrative context, not primary issuer evidence.",
        "The current adapter analyzes capped listing metadata and excerpts; it does not imply full-report review.",
        "External targets and ratings are not official consensus snapshots.",
        "Wisburg is treated as one aggregator origin unless underlying publisher ownership is independently identified.",
        "Themes require SEC/issuer, valuation, price, or consensus corroboration before promotion.",
    ]
    stale_count = sum(1 for item in excerpts if _source_age_days(item.source_as_of, observed_at) is None or _source_age_days(item.source_as_of, observed_at) > 180)
    if stale_count:
        caveats.append(f"{stale_count} excerpt(s) are undated or older than 180 days and receive low-confidence treatment.")
    status = "Available" if excerpts else "Unavailable"
    if external_evidence.status == "Partial" and excerpts:
        status = "Partial"
    return WisburgResearchLens(
        ticker=identity.ticker.upper(),
        status=status,
        observed_at=observed_at,
        excerpts=excerpts,
        themes=themes,
        debate_map=debate,
        narrative_score=narrative,
        source_suggestions=suggestions,
        caveats=caveats if excerpts else ["No Wisburg research excerpts were available for this run."],
        provider_status=external_evidence.status,
        coverage_audit=coverage_audit,
        reports=reports,
        structured_claims=structured_claims,
        revisions=revisions,
    )


def enrich_source_plan_with_wisburg(plan: ResearchSourcePlan, lens: WisburgResearchLens) -> ResearchSourcePlan:
    if not lens.source_suggestions:
        return enrich_source_plan_with_wisburg_tasks(plan, lens)
    existing = {(request.source_type, request.title.lower()) for request in plan.requests}
    for suggestion in lens.source_suggestions:
        key = (suggestion.source_type, suggestion.title.lower())
        if key in existing:
            continue
        existing.add(key)
        plan.requests.append(ResearchSourceRequest(
            request_id=suggestion.suggestion_id,
            source_type=suggestion.source_type,
            title=suggestion.title,
            reason_to_inspect=suggestion.reason_to_inspect,
            expected_evidence_type=suggestion.expected_evidence_type,
            priority=suggestion.priority,
            cost_latency="Context source; deterministic follow-up required",
            confirms_or_disproves=suggestion.confirms_or_disproves,
            status="planned",
            provider=suggestion.provider,
        ))
    order = {"High": 0, "Medium": 1, "Low": 2}
    plan.requests = sorted(plan.requests, key=lambda item: (order.get(item.priority, 1), item.source_type, item.title))[:16]
    if lens.status in {"Available", "Partial"}:
        plan.provider = f"{plan.provider}+wisburg"
    return enrich_source_plan_with_wisburg_tasks(plan, lens)


def generate_wisburg_candidate_ideas(
    identity: CompanyIdentity,
    lens: WisburgResearchLens,
) -> list[TradeIdea]:
    if lens.status == "Unavailable":
        return []
    ideas: list[TradeIdea] = []
    for theme in lens.themes[:4]:
        if theme.driver == "Unmapped":
            continue
        citation = _first_citation_for_theme(lens, theme)
        event = ChangeEvent(
            category="wisburg_external_theme",
            title=f"Wisburg watch theme: {theme.label}",
            summary=theme.summary,
            severity=2,
            direction="neutral",
            event_date=citation.filed if citation else lens.observed_at[:10],
            source="Wisburg research",
            citations=[citation] if citation else [],
            metrics={
                "economic_driver": theme.driver,
                "driver_materiality": "Low",
                "thesis_grade_status": "Watch Item",
                "not_thesis_grade_reason": "Wisburg external research must be corroborated by primary issuer evidence, valuation, price, or consensus before promotion.",
                "direction_rationale": "No trade direction is assigned because this is an external narrative theme.",
                "wisburg_theme_id": theme.theme_id,
                "source_tier": citation.source_tier if citation and citation.source_tier else 4,
            },
            why_this_matters=(
                f"Outside analysts are discussing {theme.label}. It may guide follow-up research, "
                "but it is not thesis-grade evidence by itself."
            ),
        )
        idea = TradeIdea(
            idea_id=_stable_id(identity.ticker, theme.theme_id, theme.summary),
            title=f"Watch {identity.ticker}: external debate - {theme.label}",
            direction="Watch",
            structure="Watch item / evidence-gathering candidate",
            thesis=(
                f"{identity.name.title()} has an external analyst debate around {theme.label}. "
                f"{theme.summary} This remains a Watch item until primary evidence validates the driver."
            ),
            horizon="1-3 quarters",
            catalyst="Primary-source corroboration, next filing/call, estimate revision, or price-attribution refresh",
            variant_perception="External research may identify a debate, but no variant perception is stated until deterministic evidence confirms it.",
            source_events=[event],
            citations=[citation] if citation else [],
            market_capture=MarketCapture(
                "Unknown",
                None,
                None,
                lens.narrative_score.label if lens.narrative_score else "Unknown",
                "Wisburg can describe narrative activity, but price reaction and point-in-time consensus still determine market capture.",
                ["Wisburg is not official consensus and cannot establish market capture by itself."],
                consensus_official=False,
            ),
            score=_watch_score(theme),
            monitor_items=[MonitorItem(
                criterion=f"Corroborate Wisburg theme: {theme.label}",
                data_source="SEC/issuer filings, IR deck, transcript, consensus/manual import",
                cadence="After filings, earnings calls, or material external research updates",
                confirm_trigger=f"Primary evidence confirms {theme.driver} moved in the direction implied by outside research.",
                break_trigger="Issuer facts, segment KPIs, or management commentary contradict the external theme.",
                metric="wisburg_theme_corroboration",
                operator=">=",
                confirm_threshold=1.0,
                break_threshold=0.0,
                deadline="Next earnings cycle",
                source_field="wisburg_lens.themes",
            )],
            stage="Candidate",
            signal_family="wisburg_external_theme",
            strongest_counter_thesis=_opposing_case(lens, theme),
            thesis_grade_status="Watch Item",
            direction_rationale="No trade direction is assigned; Wisburg themes require primary-source corroboration.",
            next_source_to_check=_next_source_for_theme(lens, theme),
            driver_template_summary="External research context: confirm with issuer facts, filings, transcripts, valuation, price reaction, or consensus revisions.",
        )
        ideas.append(idea)
    return ideas


def lens_to_prompt_payload(lens: WisburgResearchLens | None) -> dict:
    if lens is None:
        return {
            "status": "Unavailable",
            "excerpts": [],
            "themes": [],
            "debate_map": None,
            "narrative_score": None,
            "source_suggestions": [],
            "coverage_audit": None,
            "reports": [],
            "structured_claims": [],
            "revisions": [],
            "corroboration": [],
            "research_tasks": [],
            "caveats": ["Wisburg lens was not attached."],
        }
    return {
        "status": lens.status,
        "excerpts": [
            {
                "excerpt_id": item.excerpt_id,
                "provider": item.provider,
                "category": item.category,
                "title": item.title[:220],
                "source_as_of": item.source_as_of,
                "source_language": item.source_language,
                "original_excerpt": item.original_excerpt[:900],
                "translated_summary": item.translated_summary[:700],
                "generated_summary": item.generated_summary[:700],
                "theme_tags": item.theme_tags,
                "source_tier": item.source_tier,
                "licensing_policy": item.licensing_policy,
                "mentions_target_or_rating": item.mentions_target_or_rating,
                "non_consensus_label": item.non_consensus_label,
            }
            for item in lens.excerpts[:12]
        ],
        "themes": [asdict(theme) for theme in lens.themes[:10]],
        "debate_map": asdict(lens.debate_map) if lens.debate_map else None,
        "narrative_score": asdict(lens.narrative_score) if lens.narrative_score else None,
        "source_suggestions": [asdict(item) for item in lens.source_suggestions[:10]],
        "coverage_audit": asdict(lens.coverage_audit) if lens.coverage_audit else None,
        "reports": [
            {
                "report_key": item.report_key,
                "category": item.category,
                "title": item.title[:240],
                "publisher": item.publisher,
                "published_at": item.published_at,
                "source_language": item.source_language,
                "source_tier": item.source_tier,
                "detail_status": item.detail_status,
                "content_scope": item.content_scope,
                "sections_found": item.sections_found[:12],
                "citation_id": _citation_id(item.citation),
            }
            for item in lens.reports[:12]
        ],
        "structured_claims": [
            {
                "claim_id": item.claim_id,
                "report_key": item.report_key,
                "claim_type": item.claim_type,
                "statement": item.statement[:700],
                "driver": item.driver,
                "direction": item.direction,
                "metric": item.metric,
                "fiscal_period": item.fiscal_period,
                "value": item.value,
                "previous_value": item.previous_value,
                "unit": item.unit,
                "currency": item.currency,
                "source_tier": item.source_tier,
                "corroboration_status": item.corroboration_status,
                "corroboration_explanation": item.corroboration_explanation,
                "primary_evidence_ids": item.primary_evidence_ids,
                "citation_id": _citation_id(item.citation),
                "evidence_label": item.evidence_label,
                "allowed_stage": item.allowed_stage,
            }
            for item in lens.structured_claims[:20]
        ],
        "revisions": [asdict(item) for item in lens.revisions[:12]],
        "corroboration": [asdict(item) for item in lens.corroboration[:20]],
        "research_tasks": [asdict(item) for item in lens.research_tasks[:16]],
        "caveats": lens.caveats,
    }


def _coverage_audit_from_evidence(
    identity: CompanyIdentity,
    external_evidence: ExternalEvidenceBundle,
) -> WisburgCoverageAudit | None:
    for item in external_evidence.evidence:
        payload = item.metadata.get("wisburg_coverage_audit") if item.metadata else None
        if not isinstance(payload, dict):
            continue
        tools = [WisburgToolEntitlement(**row) for row in payload.get("tools", [])]
        values = dict(payload)
        values["tools"] = tools
        return WisburgCoverageAudit(**values)
    return None


def _structured_intelligence_from_evidence(
    external_evidence: ExternalEvidenceBundle,
) -> tuple[list[WisburgReportRecord], list[WisburgStructuredClaim], list[WisburgRevisionObservation]]:
    reports: dict[str, WisburgReportRecord] = {}
    claims: dict[str, WisburgStructuredClaim] = {}
    revisions: dict[str, WisburgRevisionObservation] = {}
    for item in external_evidence.evidence:
        metadata = item.metadata or {}
        report_payload = metadata.get("wisburg_report")
        if isinstance(report_payload, dict):
            report = _report_from_dict(report_payload)
            reports[report.report_key] = report
        for payload in metadata.get("wisburg_claims", []) if isinstance(metadata.get("wisburg_claims", []), list) else []:
            if isinstance(payload, dict):
                claim = _claim_from_dict(payload)
                claims[claim.claim_id] = claim
        for payload in metadata.get("wisburg_revisions", []) if isinstance(metadata.get("wisburg_revisions", []), list) else []:
            if isinstance(payload, dict):
                revision = _revision_from_dict(payload)
                revisions[revision.revision_id] = revision
    return list(reports.values()), list(claims.values()), list(revisions.values())


def _report_from_dict(payload: dict) -> WisburgReportRecord:
    values = dict(payload)
    values["citation"] = _citation_from_dict(values.get("citation"))
    return WisburgReportRecord(**values)


def _claim_from_dict(payload: dict) -> WisburgStructuredClaim:
    values = dict(payload)
    values["citation"] = _citation_from_dict(values.get("citation"))
    return WisburgStructuredClaim(**values)


def _revision_from_dict(payload: dict) -> WisburgRevisionObservation:
    values = dict(payload)
    values["citation"] = _citation_from_dict(values.get("citation"))
    return WisburgRevisionObservation(**values)


def _citation_from_dict(payload: dict | Citation | None) -> Citation | None:
    if isinstance(payload, Citation):
        return payload
    return Citation(**payload) if isinstance(payload, dict) else None


def _citation_id(citation: Citation | None) -> str | None:
    if not citation:
        return None
    raw = "|".join(filter(None, [citation.source, citation.url, citation.accession, citation.section]))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _excerpt_from_evidence(identity: CompanyIdentity, item: ExternalEvidence, index: int) -> ExternalResearchExcerpt:
    category, report_id = _category_report_id(item)
    language = next((tag for tag in item.tags if tag in {"en", "zh"}), _detect_language(f"{item.title} {item.summary}"))
    original = _cap(item.summary, 1400)
    tags = _theme_tags(f"{item.title} {item.summary}")
    mentions_target = "target_rating" in tags
    return ExternalResearchExcerpt(
        excerpt_id=_stable_id(identity.ticker, category, report_id or str(index), item.title),
        ticker=identity.ticker.upper(),
        provider=item.provider,
        category=category,
        report_id=report_id or str(index),
        title=item.title,
        source_as_of=item.source_as_of,
        observed_at=item.observed_at,
        source_language=language,
        original_excerpt=original,
        translated_summary=_translated_summary(original, tags, language),
        generated_summary=_generated_summary(item, tags),
        theme_tags=tags,
        citation=item.citation,
        source_tier=item.source_tier,
        confidence=_freshness_confidence(item.confidence, item.source_as_of, item.observed_at),
        mentions_target_or_rating=mentions_target,
    )


def _category_report_id(item: ExternalEvidence) -> tuple[str, str]:
    section = item.citation.section if item.citation else ""
    if ":" in section:
        category, report_id = section.split(":", 1)
        return category, report_id
    return item.source_type, ""


def _theme_tags(text: str) -> list[str]:
    lower = text.lower()
    tags = []
    for key, _label, _driver, tokens, _bullish, _bearish in THEME_DEFINITIONS:
        if any(token.lower() in lower for token in tokens):
            tags.append(key)
    return tags or ["general_external_research"]


def _themes_from_excerpts(
    excerpts: list[ExternalResearchExcerpt],
    economics: CompanyEconomics | None,
) -> list[WisburgTheme]:
    by_key: dict[str, list[ExternalResearchExcerpt]] = {}
    for excerpt in excerpts:
        for tag in excerpt.theme_tags:
            by_key.setdefault(tag, []).append(excerpt)
    themes: list[WisburgTheme] = []
    known_drivers = {driver.name for driver in economics.drivers} if economics else set()
    for key, rows in by_key.items():
        definition = next((item for item in THEME_DEFINITIONS if item[0] == key), None)
        if definition:
            _key, label, driver, _tokens, bullish_tokens, bearish_tokens = definition
        else:
            label, driver, bullish_tokens, bearish_tokens = "General external research", "Unmapped", (), ()
        text = " ".join(f"{row.title} {row.original_excerpt}" for row in rows)
        stance = _stance(text, bullish_tokens, bearish_tokens)
        if known_drivers and driver not in known_drivers and driver != "Market expectations":
            driver = _closest_driver(driver, known_drivers)
        excerpts_ids = [row.excerpt_id for row in rows]
        languages = sorted({row.source_language for row in rows})
        themes.append(WisburgTheme(
            theme_id=_stable_id("theme", key, ",".join(excerpts_ids)),
            label=label,
            stance=stance,
            driver=driver,
            summary=_theme_summary(label, stance, driver, rows),
            evidence_count=len(rows),
            source_excerpt_ids=excerpts_ids,
            source_language_mix=languages,
            confidence="Medium" if len(rows) >= 2 else "Low",
        ))
    return sorted(themes, key=lambda item: (item.evidence_count, item.confidence == "Medium"), reverse=True)


def _debate_from_themes(themes: list[WisburgTheme]) -> AnalystDebateMap:
    bullish = [theme for theme in themes if theme.stance == "bullish"]
    bearish = [theme for theme in themes if theme.stance == "bearish"]
    mixed = [theme for theme in themes if theme.stance == "mixed"]
    if not themes:
        return AnalystDebateMap("Unavailable", caveats=["No Wisburg themes available."])
    status = "Contested" if bullish and bearish else "One-sided" if bullish or bearish else "Mixed"
    return AnalystDebateMap(
        status=status,
        bullish_themes=bullish,
        bearish_themes=bearish,
        mixed_themes=mixed,
        strongest_bull_case=bullish[0].summary if bullish else "",
        strongest_bear_case=bearish[0].summary if bearish else "",
        caveats=["External debate is not primary evidence and may be stale or already priced in."],
    )


def _narrative_score(excerpts: list[ExternalResearchExcerpt], themes: list[WisburgTheme]) -> ExternalNarrativeScore:
    count = len(excerpts)
    score = float(count) if count else None
    label = "Unavailable"
    if count >= 8:
        label = "Crowded"
    elif count >= 3:
        label = "Active"
    elif count > 0:
        label = "Emerging"
    topics = [theme.label for theme in themes[:6]]
    return ExternalNarrativeScore(
        "Available" if count else "Unavailable",
        score,
        label,
        count,
        topics,
        ["Narrative score is based on Wisburg item/theme count, not official consensus."],
    )


def _source_suggestions(
    identity: CompanyIdentity,
    themes: list[WisburgTheme],
    debate: AnalystDebateMap,
) -> list[WisburgSourceSuggestion]:
    suggestions: list[WisburgSourceSuggestion] = []
    for theme in themes[:8]:
        source_type, expected = _suggested_source(theme)
        suggestions.append(WisburgSourceSuggestion(
            suggestion_id=_stable_id(identity.ticker, theme.theme_id, source_type, expected),
            source_type=source_type,
            title=f"Corroborate Wisburg theme: {theme.label}",
            reason_to_inspect=(
                f"Wisburg external research is {theme.stance} on {theme.label}; "
                "deterministic evidence must confirm or disprove the driver."
            ),
            expected_evidence_type=expected,
            priority="High" if theme.evidence_count >= 2 or debate.status == "Contested" else "Medium",
            confirms_or_disproves=(
                f"Confirms or disproves whether {theme.driver} supports the external {theme.stance} narrative."
            ),
            linked_theme_id=theme.theme_id,
        ))
    if debate.status == "Contested":
        suggestions.append(WisburgSourceSuggestion(
            suggestion_id=_stable_id(identity.ticker, "debate", "counter-thesis"),
            source_type="sec_filing",
            title="Resolve external bull/bear debate with issuer facts",
            reason_to_inspect="Wisburg contains both bullish and bearish external themes; the top thesis needs primary-source arbitration.",
            expected_evidence_type="SEC/issuer excerpt, segment KPI, or management quote that resolves the debate.",
            priority="High",
            confirms_or_disproves="Confirms whether the bull or bear external framing matches issuer evidence.",
            linked_theme_id=None,
        ))
    return suggestions[:10]


def _suggested_source(theme: WisburgTheme) -> tuple[str, str]:
    lower = f"{theme.label} {theme.driver}".lower()
    if "target" in lower or "rating" in lower or "market expectations" in lower:
        return "consensus_manual", "Point-in-time target, estimate, and recommendation revisions from licensed/manual sources."
    if "policy" in lower or "rmb" in lower or "fx" in lower:
        return "macro_market", "Official macro, FX, policy, and event-window attribution context."
    if "buyback" in lower or "share" in lower:
        return "sec_filing", "Issuer-filed buyback table, ADS/ordinary-share reconciliation, and share-count basis."
    if "cloud" in lower or "commerce" in lower or "margin" in lower or "services" in lower:
        return "presentation", "Issuer results deck, segment KPI table, transcript Q&A, and margin bridge."
    return "external_research", "Additional external research metadata used only as context."


def _stance(text: str, bullish_tokens: tuple[str, ...], bearish_tokens: tuple[str, ...]) -> str:
    lower = text.lower()
    bullish = sum(lower.count(token.lower()) for token in bullish_tokens)
    bearish = sum(lower.count(token.lower()) for token in bearish_tokens)
    if bullish and bearish:
        return "mixed"
    if bullish:
        return "bullish"
    if bearish:
        return "bearish"
    return "mixed"


def _theme_summary(label: str, stance: str, driver: str, rows: list[ExternalResearchExcerpt]) -> str:
    sample = rows[0].generated_summary or rows[0].original_excerpt
    return (
        f"{len(rows)} Wisburg item(s) discuss {label} with a {stance} or debated framing. "
        f"Mapped driver: {driver}. Example: {_cap(sample, 260)}"
    )


def _closest_driver(driver: str, known_drivers: set[str]) -> str:
    driver_tokens = set(re.findall(r"[a-zA-Z]+", driver.lower()))
    best = "Unmapped"
    best_score = 0
    for candidate in known_drivers:
        score = len(driver_tokens & set(re.findall(r"[a-zA-Z]+", candidate.lower())))
        if score > best_score:
            best, best_score = candidate, score
    return best if best_score else driver


def _translated_summary(text: str, tags: list[str], language: str) -> str:
    topics = ", ".join(_label_for_tag(tag) for tag in tags[:4])
    if language == "zh":
        return f"Rule-based English topic summary from Chinese source: {topics or 'general external research'}."
    return _cap(text, 700)


def _generated_summary(item: ExternalEvidence, tags: list[str]) -> str:
    topics = ", ".join(_label_for_tag(tag) for tag in tags[:4])
    prefix = f"Topics: {topics}. " if topics else ""
    return _cap(prefix + item.summary, 900)


def _label_for_tag(tag: str) -> str:
    definition = next((item for item in THEME_DEFINITIONS if item[0] == tag), None)
    return definition[1] if definition else tag.replace("_", " ")


def _first_citation_for_theme(lens: WisburgResearchLens, theme: WisburgTheme) -> Citation | None:
    excerpt_ids = set(theme.source_excerpt_ids)
    return next((item.citation for item in lens.excerpts if item.excerpt_id in excerpt_ids and item.citation), None)


def _opposing_case(lens: WisburgResearchLens, theme: WisburgTheme) -> str:
    debate = lens.debate_map
    if not debate:
        return "External research is context only; primary-source contradiction has not been tested."
    if theme.stance == "bullish" and debate.strongest_bear_case:
        return debate.strongest_bear_case
    if theme.stance == "bearish" and debate.strongest_bull_case:
        return debate.strongest_bull_case
    return "External research has not resolved the strongest primary-source counter-thesis."


def _next_source_for_theme(lens: WisburgResearchLens, theme: WisburgTheme) -> str:
    suggestion = next((item for item in lens.source_suggestions if item.linked_theme_id == theme.theme_id), None)
    if suggestion:
        return f"{suggestion.title} [{suggestion.source_type}]: {suggestion.reason_to_inspect}"
    return "Check primary issuer filings, IR deck, transcript, valuation, and consensus/manual imports."


def _watch_score(theme: WisburgTheme) -> ScoreBreakdown:
    evidence = min(8, 3 + theme.evidence_count)
    specificity = 8 if theme.driver != "Unmapped" else 3
    total = min(40, evidence + specificity + 8)
    return ScoreBreakdown(
        total=total,
        evidence_strength=evidence,
        novelty=8,
        magnitude=0,
        timing=4,
        market_capture=0,
        data_confidence=3,
        rationale=[
            "Wisburg external research is context only.",
            "Primary-source validation, valuation, price reaction, and consensus are required before promotion.",
        ],
        score_cap=55,
        score_cap_reason="No Tier 1 primary evidence; external research cannot independently support conviction.",
        thesis_specificity=specificity,
        research_quality=total,
        evidence_strength_score=evidence * 4,
        actionability=20,
    )


def _detect_language(text: str) -> str:
    return "zh" if re.search(r"[\u4e00-\u9fff]", text) else "en"


def _freshness_confidence(confidence: str, source_as_of: str | None, observed_at: str) -> str:
    age = _source_age_days(source_as_of, observed_at)
    if age is None or age < 0 or age > 180:
        return "Low"
    return confidence


def _source_age_days(source_as_of: str | None, observed_at: str) -> int | None:
    if not source_as_of:
        return None
    try:
        source_day = datetime.fromisoformat(source_as_of.replace("Z", "+00:00")).date()
        observed_day = datetime.fromisoformat(observed_at.replace("Z", "+00:00")).date()
    except ValueError:
        return None
    return (observed_day - source_day).days


def _stable_id(*parts: str) -> str:
    return hashlib.sha1(":".join(parts).encode("utf-8")).hexdigest()[:12]


def _cap(text: str, limit: int) -> str:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    return clean[:limit]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
