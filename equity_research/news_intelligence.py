from __future__ import annotations

import hashlib
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Iterable

from .analysis import snippet
from .models import (
    BridgeComponent,
    CausalBridge,
    CausalBridgeSourceNeed,
    ChangeEvent,
    Citation,
    ExternalEvidenceBundle,
    NewsClaim,
    NewsSourceObservation,
    PrimarySourceObservation,
    ResearchSourcePlan,
    ResearchSourceRequest,
    SourceCorroborationResult,
    TradeIdea,
)


NEWS_SOURCE_TYPES = {
    "licensed_newswire",
    "reputable_publisher",
    "public_news_metadata",
    "narrative_saturation",
}

PRIMARY_SOURCE_TYPES_BY_DRIVER: dict[str, tuple[str, ...]] = {
    "revenue_demand": ("sec_filing", "issuer_ir", "presentation", "industry_official_dataset"),
    "margin_opex": ("sec_filing", "issuer_ir", "presentation", "labor_employment", "trade_import_export"),
    "cash_fcf": ("sec_filing", "issuer_ir", "presentation"),
    "debt_liquidity": ("sec_filing", "issuer_ir", "macro_market"),
    "share_count_capital_return": ("sec_filing", "issuer_ir", "presentation"),
    "guidance_expectations": ("issuer_ir", "earnings_transcript", "consensus_manual"),
    "regulation_legal": ("regulator_court", "sec_filing", "issuer_ir"),
    "product_safety": ("product_safety", "issuer_ir", "sec_filing"),
    "patent_ip": ("patent_ip", "issuer_ir", "sec_filing"),
    "government_contract": ("government_contract", "sec_filing", "issuer_ir"),
    "trade_supply_chain": ("trade_import_export", "sec_filing", "issuer_ir"),
    "labor_cost": ("labor_employment", "sec_filing", "issuer_ir"),
    "macro_fx_rates": ("macro_market", "sec_filing", "issuer_ir"),
    "positioning_liquidity": ("macro_market",),
    "external_narrative": ("sec_filing", "issuer_ir", "presentation", "consensus_manual"),
}


SOURCE_NEED_LABELS: dict[str, tuple[str, str]] = {
    "regulator_court": ("Official regulator/court record", "Official action, filing, docket, order, settlement, or enforcement detail."),
    "product_safety": ("Official product/safety source", "Agency notice, approval, recall, safety event, or issuer confirmation."),
    "patent_ip": ("Official patent/IP source", "Patent, trial, approval, IP dispute, or official product-pipeline record."),
    "government_contract": ("Official contract award", "Award, modification, agency procurement notice, or budget source."),
    "trade_import_export": ("Official trade/supply-chain source", "Import/export, tariff, export-control, customs, or official trade data."),
    "labor_employment": ("Official labor/cost source", "WARN, BLS, OSHA, NLRB, or official employment/cost record."),
    "industry_official_dataset": ("Official industry dataset", "Industry volume, pricing, utilization, or demand data."),
    "sec_filing": ("SEC filing or exhibit", "Filing section, XBRL fact, risk language, or filed exhibit."),
    "issuer_ir": ("Issuer release or IR source", "Issuer release, results deck, KPI table, transcript, or investor-day material."),
    "presentation": ("Issuer presentation", "Segment bridge, KPI table, margin bridge, or capital-allocation slide."),
    "earnings_transcript": ("Earnings transcript", "Prepared remarks, Q&A, guidance quote, or management cross-check."),
    "consensus_manual": ("Consensus snapshot", "Point-in-time EPS, revenue, target, or recommendation revision."),
    "macro_market": ("Macro/market source", "Price, benchmark, rate, FX, volume, positioning, or factor context."),
}


def observation_from_payload(payload: dict, observed_at: str | None = None) -> NewsSourceObservation:
    observed = observed_at or _now()
    ticker = str(payload.get("ticker") or "").strip().upper()
    provider = str(payload.get("provider") or payload.get("source") or "Manual news import").strip()
    headline = str(payload.get("headline") or payload.get("title") or "").strip()
    url = str(payload.get("url") or payload.get("source_url") or "").strip()
    published_at = str(payload.get("published_at") or payload.get("source_as_of") or payload.get("event_date") or "").strip() or None
    source_family = _source_family(provider, str(payload.get("source_family") or ""))
    licensing_policy = str(payload.get("licensing_policy") or _default_licensing(source_family))
    excerpt = str(payload.get("excerpt") or payload.get("summary") or payload.get("claimed_fact") or "").strip()
    may_store_full_text = bool(payload.get("may_store_full_text", False))
    if not may_store_full_text:
        excerpt = snippet(excerpt, max_chars=700)
    topic_tags = _list(payload.get("topic_tags") or payload.get("tags"))
    observation_id = _stable_id("news-observation", ticker, provider, headline, url, published_at or observed)
    return NewsSourceObservation(
        observation_id=observation_id,
        ticker=ticker,
        provider=provider,
        source_family=source_family,
        headline=headline,
        url=url,
        published_at=published_at,
        observed_at=observed,
        source_tier=int(payload.get("source_tier") or _default_tier(source_family)),
        licensing_policy=licensing_policy,
        may_store_full_text=may_store_full_text,
        excerpt=excerpt,
        language=str(payload.get("language") or "unknown"),
        entity_match=str(payload.get("entity_match") or "ticker"),
        topic_tags=topic_tags,
        retrieval_manifest={
            "import_mode": str(payload.get("import_mode") or "manual"),
            "raw_payload_policy": "full_text_not_stored" if not may_store_full_text else "user_confirmed_storage_allowed",
        },
    )


def claim_from_observation(
    observation: NewsSourceObservation,
    *,
    company: str = "",
    event_type: str = "",
    affected_driver: str = "",
    claimed_fact: str = "",
    confidence: str = "Medium",
) -> NewsClaim:
    driver = driver_family(affected_driver or " ".join(observation.topic_tags + [observation.headline, observation.excerpt]))
    fact = claimed_fact or observation.excerpt or observation.headline
    claim_id = _stable_id("news-claim", observation.observation_id, driver, fact)
    citation = Citation(
        source=observation.provider,
        url=observation.url,
        filed=observation.published_at,
        snippet=snippet(fact, max_chars=360),
        retrieved_at=observation.observed_at,
        source_tier=observation.source_tier,
    )
    return NewsClaim(
        claim_id=claim_id,
        observation_id=observation.observation_id,
        ticker=observation.ticker,
        company=company or observation.ticker,
        event_type=event_type or _event_type_from_driver(driver),
        affected_driver=driver,
        claimed_fact=snippet(fact, max_chars=700),
        event_date=observation.published_at,
        source_tier=observation.source_tier,
        confidence=confidence,
        required_corroboration=[SOURCE_NEED_LABELS.get(item, (item, item))[1] for item in PRIMARY_SOURCE_TYPES_BY_DRIVER.get(driver, ("sec_filing", "issuer_ir"))],
        citation=citation,
        status="News detected",
        allowed_stage="Candidate",
        source_family=observation.source_family,
        created_at=observation.observed_at,
    )


def build_news_intelligence(
    ticker: str,
    company: str,
    external_evidence: ExternalEvidenceBundle | None,
    stored_claims: Iterable[dict] | None = None,
) -> list[NewsClaim]:
    claims: list[NewsClaim] = []
    for payload in stored_claims or []:
        claims.append(news_claim_from_payload(payload))
    for item in (external_evidence.evidence if external_evidence else []):
        if item.source_type not in {"narrative_saturation"}:
            continue
        observation = NewsSourceObservation(
            observation_id=_stable_id("external-news", ticker, item.provider, item.title, item.source_as_of or item.observed_at),
            ticker=ticker.upper(),
            provider=item.provider,
            source_family="narrative_saturation",
            headline=item.title,
            url=item.citation.url if item.citation else "",
            published_at=item.source_as_of,
            observed_at=item.observed_at,
            source_tier=max(item.source_tier, 4),
            licensing_policy=item.licensing_policy,
            excerpt=snippet(item.summary, max_chars=500),
            topic_tags=list(item.tags),
            retrieval_manifest={"import_mode": "external_evidence_context", "raw_payload_policy": "metadata_and_excerpt_only"},
        )
        claim = claim_from_observation(
            observation,
            company=company,
            event_type="narrative_context",
            affected_driver="external_narrative",
            claimed_fact=item.summary,
            confidence=item.confidence,
        )
        claim.status = "Narrative context only"
        claims.append(claim)
    return _dedupe_claims(claims)


def primary_observations_from_payloads(payloads: Iterable[dict]) -> list[PrimarySourceObservation]:
    observations: list[PrimarySourceObservation] = []
    for payload in payloads:
        fields = PrimarySourceObservation.__dataclass_fields__
        normalized = {key: value for key, value in payload.items() if key in fields}
        citation = normalized.get("citation")
        if isinstance(citation, dict):
            normalized["citation"] = Citation(**{key: value for key, value in citation.items() if key in Citation.__dataclass_fields__})
        try:
            observations.append(PrimarySourceObservation(**normalized))
        except TypeError:
            continue
    return observations


def news_claim_from_payload(payload: dict) -> NewsClaim:
    return _claim_from_dict(payload)


def source_needs_for_claim(claim: NewsClaim) -> list[CausalBridgeSourceNeed]:
    source_types = PRIMARY_SOURCE_TYPES_BY_DRIVER.get(claim.affected_driver, ("sec_filing", "issuer_ir"))
    needs: list[CausalBridgeSourceNeed] = []
    for source_type in source_types:
        label, expected = SOURCE_NEED_LABELS.get(source_type, (source_type, source_type))
        needs.append(
            CausalBridgeSourceNeed(
                need_id=_stable_id("source-need", claim.claim_id, source_type),
                ticker=claim.ticker,
                driver_family=claim.affected_driver,
                source_type=source_type,
                source_family="primary_source" if source_type not in {"consensus_manual", "macro_market"} else "market_context",
                priority="High" if source_type in {"regulator_court", "product_safety", "government_contract", "sec_filing", "issuer_ir"} else "Medium",
                reason=f"News claim needs {label.lower()} before it can support an investable thesis.",
                expected_evidence=expected,
                confirms_or_disproves=f"Confirms or disproves: {claim.claimed_fact}",
                related_claim_id=claim.claim_id,
            )
        )
    return needs


def enrich_source_plan_with_news(plan: ResearchSourcePlan, claims: list[NewsClaim]) -> ResearchSourcePlan:
    if not claims:
        return plan
    existing = {request.request_id for request in plan.requests}
    for claim in claims:
        for need in source_needs_for_claim(claim):
            request_id = _stable_id("news-source-request", claim.claim_id, need.source_type)
            if request_id in existing:
                continue
            existing.add(request_id)
            plan.requests.append(
                ResearchSourceRequest(
                    request_id=request_id,
                    source_type=need.source_type,
                    title=f"Corroborate news claim with {need.source_type.replace('_', ' ')}",
                    reason_to_inspect=need.reason,
                    expected_evidence_type=need.expected_evidence,
                    priority=need.priority,
                    cost_latency="Free/paid depending on provider and source access",
                    confirms_or_disproves=need.confirms_or_disproves,
                    status="planned",
                    provider="news_intelligence",
                )
            )
    plan.requests = plan.requests[:12]
    if plan.status == "Unavailable" and plan.requests:
        plan.status = "Available"
    if "News claims require primary-source corroboration before promotion." not in plan.data_gaps:
        plan.data_gaps.append("News claims require primary-source corroboration before promotion.")
    return plan


def build_corroboration_results(
    ticker: str,
    claims: list[NewsClaim],
    primary_observations: list[PrimarySourceObservation] | None = None,
) -> list[SourceCorroborationResult]:
    observations = primary_observations or []
    results: list[SourceCorroborationResult] = []
    observed_at = _now()
    for claim in claims:
        matches = [
            item for item in observations
            if item.ticker.upper() == claim.ticker.upper()
            and (claim.claim_id in item.corroborates_claim_ids or item.driver_family == claim.affected_driver)
        ]
        contradictions = [
            item for item in observations
            if item.ticker.upper() == claim.ticker.upper() and claim.claim_id in item.contradicts_claim_ids
        ]
        if contradictions:
            status = "Contradicted by primary source"
            explanation = "At least one primary source contradicts the news claim."
            matched = [item.observation_id for item in contradictions]
            gaps: list[str] = []
        elif matches:
            status = "Primary source checked"
            explanation = "At least one primary source supports or directly checks the news claim."
            matched = [item.observation_id for item in matches]
            gaps = []
        else:
            status = "Primary corroboration missing"
            explanation = "News is a source lead only until a primary source confirms or disproves it."
            matched = []
            gaps = claim.required_corroboration
        results.append(
            SourceCorroborationResult(
                result_id=_stable_id("corroboration", claim.claim_id, status),
                ticker=ticker.upper(),
                claim_id=claim.claim_id,
                status=status,
                driver_family=claim.affected_driver,
                primary_source_status=status,
                explanation=explanation,
                required_sources=[need.source_type for need in source_needs_for_claim(claim)],
                matched_observation_ids=matched,
                gaps=gaps,
                observed_at=observed_at,
            )
        )
    return results


def generate_news_candidate_ideas(ticker: str, company: str, claims: list[NewsClaim]) -> list[TradeIdea]:
    ideas: list[TradeIdea] = []
    for claim in claims:
        event = ChangeEvent(
            category="news_claim",
            title=f"News watch: {claim.event_type.replace('_', ' ').title()}",
            summary=claim.claimed_fact,
            severity=3 if claim.source_tier <= 3 else 2,
            direction="mixed",
            event_date=claim.event_date,
            source=claim.source_family,
            citations=[claim.citation] if claim.citation else [],
            metrics={
                "economic_driver": claim.affected_driver,
                "driver_materiality": "Medium",
                "thesis_grade_status": "Watch Item",
                "not_thesis_grade_reason": "News requires primary-source corroboration before promotion.",
                "news_claim_id": claim.claim_id,
            },
            why_this_matters="News can identify a possible driver, but primary evidence must confirm it before it becomes investable.",
        )
        idea = TradeIdea(
            idea_id=_stable_id("news-idea", claim.claim_id),
            title=f"Watch {claim.ticker}: {claim.affected_driver.replace('_', ' ')} news requires corroboration",
            direction="Watch",
            structure="News-led research candidate",
            thesis=(
                f"{company or claim.ticker} has a news-reported possible driver: {claim.claimed_fact} "
                "This is not an investable thesis until primary sources confirm or disprove it."
            ),
            horizon="Event-driven",
            catalyst="Primary-source corroboration or contradiction",
            variant_perception="Unavailable until primary evidence, price reaction, and expectations data are connected.",
            source_events=[event],
            citations=[claim.citation] if claim.citation else [],
            stage="Candidate",
            signal_family="news_intelligence",
            thesis_grade_status="Watch Item",
            direction_rationale="No Long/Short direction is assigned because news-only claims require primary-source corroboration.",
            news_claim_ids=[claim.claim_id],
            primary_source_status="Primary corroboration missing",
            corroboration_gaps=list(claim.required_corroboration),
            next_source_to_check="; ".join(claim.required_corroboration[:2]) or "Find primary corroboration.",
            bridge_status="Unsupported",
            bridge_direction_rationale="News-only evidence cannot establish causality or trade direction.",
        )
        ideas.append(idea)
    return ideas


def attach_causal_bridges(
    ticker: str,
    ideas: list[TradeIdea],
    news_claims: list[NewsClaim],
    corroboration: list[SourceCorroborationResult],
) -> list[CausalBridge]:
    claims_by_id = {claim.claim_id: claim for claim in news_claims}
    corroboration_by_claim = {item.claim_id: item for item in corroboration}
    bridges: list[CausalBridge] = []
    observed_at = _now()
    for idea in ideas:
        driver = _driver_from_idea(idea)
        related_claims = [claims_by_id[item] for item in idea.news_claim_ids if item in claims_by_id]
        bridge_status = "Partial"
        corroboration_status = "Not applicable"
        source_needs: list[CausalBridgeSourceNeed] = []
        gaps: list[str] = []
        if related_claims:
            statuses = [corroboration_by_claim.get(claim.claim_id) for claim in related_claims]
            if any(item and item.status == "Contradicted by primary source" for item in statuses):
                bridge_status = "Contradicted"
                corroboration_status = "Contradicted by primary source"
            elif all(item and item.status == "Primary source checked" for item in statuses):
                bridge_status = "Partial"
                corroboration_status = "Primary source checked"
            else:
                bridge_status = "Unsupported"
                corroboration_status = "Primary corroboration missing"
                for claim in related_claims:
                    source_needs.extend(source_needs_for_claim(claim))
                    gaps.extend(claim.required_corroboration)
        bridge = CausalBridge(
            bridge_id=_stable_id("causal-bridge", ticker, idea.idea_id, driver),
            ticker=ticker.upper(),
            idea_id=idea.idea_id,
            driver_family=driver,
            status=bridge_status,
            thesis_direction=idea.direction,
            explanation=_bridge_explanation(driver, bridge_status, bool(related_claims)),
            required_primary_sources=list(PRIMARY_SOURCE_TYPES_BY_DRIVER.get(driver, ("sec_filing", "issuer_ir"))),
            supporting_news_claims=[claim.claim_id for claim in related_claims],
            corroboration_status=corroboration_status,
            components=[
                BridgeComponent(
                    "Source signal",
                    "Passed" if idea.citations or idea.source_events else "Missing",
                    [idea.source_events[0].summary] if idea.source_events else [],
                    [] if idea.source_events else ["Attach source-linked evidence."],
                ),
                BridgeComponent(
                    "Primary corroboration",
                    "Passed" if corroboration_status == "Primary source checked" else "Missing" if related_claims else "Not applicable",
                    [],
                    gaps[:3],
                ),
            ],
            source_needs=source_needs,
            data_gaps=list(dict.fromkeys(gaps)),
            observed_at=observed_at,
        )
        idea.causal_bridge = bridge
        idea.bridge_status = bridge.status
        idea.primary_source_status = corroboration_status if related_claims else idea.primary_source_status
        if bridge.data_gaps:
            idea.corroboration_gaps = list(dict.fromkeys(idea.corroboration_gaps + bridge.data_gaps))
        if idea.signal_family == "news_intelligence" and corroboration_status != "Primary source checked":
            idea.stage = "Candidate"
            idea.bridge_direction_rationale = "News-only claims remain Candidate until primary sources corroborate the causal bridge."
        elif not idea.bridge_direction_rationale:
            idea.bridge_direction_rationale = bridge.explanation
        bridges.append(bridge)
    return bridges


def driver_family(value: str) -> str:
    lower = value.lower()
    if any(token in lower for token in ("regulat", "court", "lawsuit", "legal", "antitrust", "doj", "ftc", "sec investigation")):
        return "regulation_legal"
    if any(token in lower for token in ("recall", "safety", "fda", "faa", "nhtsa", "cpsc", "approval")):
        return "product_safety"
    if any(token in lower for token in ("patent", "intellectual", "ip", "trial", "pipeline")):
        return "patent_ip"
    if any(token in lower for token in ("contract", "award", "procurement", "government demand")):
        return "government_contract"
    if any(token in lower for token in ("tariff", "export", "import", "supply chain", "customs", "bis")):
        return "trade_supply_chain"
    if any(token in lower for token in ("labor", "layoff", "warn", "union", "wage", "osha", "nlrb")):
        return "labor_cost"
    if any(token in lower for token in ("cash", "free cash", "fcf")):
        return "cash_fcf"
    if any(token in lower for token in ("debt", "liquidity", "refinanc", "credit")):
        return "debt_liquidity"
    if any(token in lower for token in ("share", "buyback", "repurchase", "dilution")):
        return "share_count_capital_return"
    if any(token in lower for token in ("margin", "cost", "opex", "expense")):
        return "margin_opex"
    if any(token in lower for token in ("guidance", "estimate", "consensus", "expectation")):
        return "guidance_expectations"
    if any(token in lower for token in ("rate", "fx", "macro", "inflation", "cnh", "rmb")):
        return "macro_fx_rates"
    if any(token in lower for token in ("volume", "short", "liquidity", "positioning", "options")):
        return "positioning_liquidity"
    if any(token in lower for token in ("revenue", "demand", "sales", "customer", "orders")):
        return "revenue_demand"
    if any(token in lower for token in ("narrative", "news", "sentiment")):
        return "external_narrative"
    return "external_narrative"


def _driver_from_idea(idea: TradeIdea) -> str:
    if idea.source_events:
        event = idea.source_events[0]
        return driver_family(str(event.metrics.get("economic_driver") or event.metrics.get("metric_name") or event.category))
    return driver_family(idea.signal_family or idea.title)


def _bridge_explanation(driver: str, status: str, has_news: bool) -> str:
    if has_news and status == "Unsupported":
        return "News identified a possible driver, but primary-source corroboration is missing."
    if status == "Contradicted":
        return "Primary-source evidence contradicts the proposed causal bridge."
    if status == "Partial":
        return f"The {driver.replace('_', ' ')} bridge has source evidence but may need more driver-specific proof."
    return f"The {driver.replace('_', ' ')} bridge is not yet established."


def _event_type_from_driver(driver: str) -> str:
    return {
        "regulation_legal": "regulatory_or_legal_event",
        "product_safety": "product_or_safety_event",
        "patent_ip": "ip_or_pipeline_event",
        "government_contract": "government_contract_event",
        "trade_supply_chain": "trade_or_supply_chain_event",
        "labor_cost": "labor_or_cost_event",
    }.get(driver, "external_news_event")


def _source_family(provider: str, explicit: str) -> str:
    if explicit:
        return explicit
    lower = provider.lower()
    if lower == "ap" or any(name in lower for name in ("reuters", "associated press", "ap ", "dow jones", "bloomberg", "factiva")):
        return "licensed_newswire"
    if any(name in lower for name in ("ft", "financial times", "wsj", "nikkei", "caixin", "scmp", "new york times")):
        return "reputable_publisher"
    if "gdelt" in lower:
        return "narrative_saturation"
    return "public_news_metadata"


def _default_tier(source_family: str) -> int:
    return 3 if source_family in {"licensed_newswire", "reputable_publisher"} else 4


def _default_licensing(source_family: str) -> str:
    if source_family in {"licensed_newswire", "reputable_publisher"}:
        return "licensed_metadata_excerpt_only"
    return "metadata_and_excerpt_only"


def _claim_from_dict(payload: dict) -> NewsClaim:
    fields = NewsClaim.__dataclass_fields__
    normalized = {key: value for key, value in payload.items() if key in fields}
    citation = normalized.get("citation")
    if isinstance(citation, dict):
        normalized["citation"] = Citation(**{key: value for key, value in citation.items() if key in Citation.__dataclass_fields__})
    return NewsClaim(**normalized)


def _list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.replace("|", ",").split(",") if item.strip()]
    return []


def _dedupe_claims(claims: list[NewsClaim]) -> list[NewsClaim]:
    seen: set[str] = set()
    result: list[NewsClaim] = []
    for claim in claims:
        if claim.claim_id in seen:
            continue
        seen.add(claim.claim_id)
        result.append(claim)
    return result


def _stable_id(*parts: object) -> str:
    digest = hashlib.sha1("|".join(str(part or "") for part in parts).encode("utf-8")).hexdigest()
    return digest[:12]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def as_payload(value) -> dict:
    return asdict(value)
