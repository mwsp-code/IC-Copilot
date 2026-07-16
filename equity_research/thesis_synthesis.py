from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, unquote_plus, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from . import config
from .analysis import format_number
from .models import (
    ActionPlan,
    BringYourOwnDataStatus,
    BudgetPolicy,
    CalibrationReport,
    Citation,
    CompanyEconomics,
    CompanyIdentity,
    CreditLens,
    DataQualityReport,
    EvidenceLedger,
    EvidenceSufficiency,
    EvidenceWorkOrder,
    EventWorkflow,
    ExpectationsBridge,
    ExternalEvidenceBundle,
    HistoricalReferenceSet,
    LLMRunManifest,
    LanguageAudit,
    LlmComparison,
    LlmGuardrailCheck,
    LlmReviewResult,
    ManagementCredibility,
    ManagementSourcePackage,
    SourceLanguageExcerpt,
    ThesisBrief,
    ThesisCluster,
    ThesisCritique,
    ThesisValidationReport,
    TradeIdea,
    ValuationResult,
    ClaimValidationResult,
    ResearchSourcePlan,
    ResearchQuestion,
    ResearchScoutReport,
    LlmExtractionManifest,
    WisburgResearchLens,
)
from .wisburg_lens import lens_to_prompt_payload


PROMPT_VERSION = "ic-copilot-v1"
SUFFICIENCY_STATUSES = ("Convincing", "Promising but incomplete", "Weak", "No thesis")


class LlmProvider(Protocol):
    provider_name: str
    model: str

    def complete_json(self, prompt_pack: dict) -> dict:
        ...


@dataclass
class ThesisSynthesisResult:
    thesis_brief: ThesisBrief
    thesis_critique: ThesisCritique
    evidence_sufficiency: EvidenceSufficiency
    action_plan: list[ActionPlan]
    llm_manifest: LLMRunManifest
    llm_reviews: list[LlmReviewResult]
    llm_comparison: LlmComparison
    language_audit: LanguageAudit


class OpenAICompatibleProvider:
    provider_name = "openai_compatible"

    def __init__(
        self,
        api_key: str = "",
        model: str = "",
        base_url: str = "",
        provider_name: str | None = None,
        timeout_seconds: int | None = None,
        fetch_json=None,
    ) -> None:
        self.api_key = api_key
        self.model = model or "gpt-4.1-mini"
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.provider_name = provider_name or self.provider_name
        self.timeout_seconds = timeout_seconds or config.LLM_TIMEOUT_SECONDS
        self.fetch_json = fetch_json or _post_json

    def complete_json(self, prompt_pack: dict) -> dict:
        if not self.api_key and not self.base_url.startswith("http://localhost"):
            raise RuntimeError("LLM API key is not configured.")
        payload = {
            "model": self.model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an investment committee copilot. Use only the supplied evidence pack. "
                        "Do not invent facts, price targets, probabilities, citations, or recommendations. "
                        "If evidence is insufficient, set verdict to 'No convincing thesis yet'. "
                        "Return JSON only. Every evidence_chain item must be an object with claim and citation_ids."
                    ),
                },
                {"role": "user", "content": json.dumps(prompt_pack, ensure_ascii=True)},
            ],
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        response = self.fetch_json(f"{self.base_url}/chat/completions", payload, headers, self.timeout_seconds)
        content = (((response.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        if not content:
            raise RuntimeError("LLM provider returned no message content.")
        return json.loads(content)


class AnthropicProvider:
    provider_name = "anthropic"

    def __init__(
        self,
        api_key: str = "",
        model: str = "",
        base_url: str = "",
        timeout_seconds: int | None = None,
        fetch_json=None,
    ) -> None:
        self.api_key = api_key
        self.model = model or "claude-3-5-sonnet-latest"
        self.base_url = (base_url or "https://api.anthropic.com/v1").rstrip("/")
        self.timeout_seconds = timeout_seconds or config.LLM_SECONDARY_TIMEOUT_SECONDS
        self.fetch_json = fetch_json or _post_json

    def complete_json(self, prompt_pack: dict) -> dict:
        if not self.api_key:
            raise RuntimeError("Anthropic API key is not configured.")
        payload = {
            "model": self.model,
            "max_tokens": 1600,
            "temperature": 0.1,
            "system": (
                "You are an investment committee copilot. Use only the supplied evidence pack. "
                "Return JSON only. Do not invent facts, targets, probabilities, or citations. "
                "Every evidence_chain item must be an object with claim and citation_ids."
            ),
            "messages": [{"role": "user", "content": json.dumps(prompt_pack, ensure_ascii=True)}],
        }
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        response = self.fetch_json(f"{self.base_url}/messages", payload, headers, self.timeout_seconds)
        blocks = response.get("content") or []
        text = ""
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                text += str(block.get("text") or "")
        if not text:
            raise RuntimeError("Anthropic provider returned no text content.")
        return json.loads(text)


class UnavailableLlmProvider:
    def __init__(self, provider_name: str, model: str, message: str) -> None:
        self.provider_name = provider_name
        self.model = model
        self.message = message

    def complete_json(self, prompt_pack: dict) -> dict:
        raise RuntimeError(self.message)


def provider_from_config(
    *,
    enabled: bool | None = None,
    provider: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> LlmProvider | None:
    use_llm = config.ENABLE_LLM_THESIS if enabled is None else enabled
    if not use_llm:
        return None
    provider_name = (provider or config.LLM_PROVIDER or "openai_compatible").strip().lower()
    selected_model = model or _model_for_provider(provider_name)
    selected_base_url = base_url or _base_url_for_provider(provider_name)
    selected_key = api_key if api_key is not None else _api_key_for_provider(provider_name)
    if provider_name in {"qwen", "kimi", "custom_openai_compatible"} and not selected_base_url:
        return None
    if provider_name in {"openai", "openai_compatible", "qwen", "kimi", "deepseek", "custom_openai_compatible", "ollama", "local"}:
        return OpenAICompatibleProvider(
            api_key=selected_key,
            model=selected_model or _model_for_provider(provider_name),
            base_url=selected_base_url,
            provider_name=provider_name,
            timeout_seconds=config.LLM_TIMEOUT_SECONDS,
        )
    if provider_name == "anthropic":
        return AnthropicProvider(
            api_key=selected_key,
            model=selected_model or "claude-3-5-sonnet-latest",
            base_url=selected_base_url or "https://api.anthropic.com/v1",
            timeout_seconds=config.LLM_TIMEOUT_SECONDS,
        )
    return None


def synthesize_ic_thesis(
    identity: CompanyIdentity,
    ideas: list[TradeIdea],
    evidence: EvidenceLedger,
    valuation: ValuationResult,
    data_quality: DataQualityReport,
    management: ManagementCredibility,
    expectations: ExpectationsBridge,
    management_sources: ManagementSourcePackage,
    external_evidence: ExternalEvidenceBundle,
    calibration: CalibrationReport,
    provider: LlmProvider | None = None,
    secondary_provider: LlmProvider | None = None,
    enable_secondary: bool = True,
    secondary_min_stage: str = "Research-Ready",
    language_policy: str = "bilingual_audit",
    historical_references: HistoricalReferenceSet | None = None,
    thesis_validation: ThesisValidationReport | None = None,
    budget_policy: BudgetPolicy | None = None,
    manual_data_status: BringYourOwnDataStatus | None = None,
    company_economics: CompanyEconomics | None = None,
    credit_lens: CreditLens | None = None,
    thesis_clusters: list[ThesisCluster] | None = None,
    research_questions: list[ResearchQuestion] | None = None,
    research_scout: ResearchScoutReport | None = None,
    validated_claims: ClaimValidationResult | None = None,
    source_plan: ResearchSourcePlan | None = None,
    llm_extraction_manifest: LlmExtractionManifest | None = None,
    event_workflow: EventWorkflow | None = None,
    wisburg_lens: WisburgResearchLens | None = None,
    evidence_work_order: EvidenceWorkOrder | None = None,
) -> ThesisSynthesisResult:
    prompt_pack = build_prompt_pack(
        identity, ideas, evidence, valuation, data_quality, management,
        expectations, management_sources, external_evidence, calibration,
        historical_references=historical_references,
        thesis_validation=thesis_validation,
        budget_policy=budget_policy,
        manual_data_status=manual_data_status,
        company_economics=company_economics,
        credit_lens=credit_lens,
        thesis_clusters=thesis_clusters,
        research_questions=research_questions,
        research_scout=research_scout,
        validated_claims=validated_claims,
        source_plan=source_plan,
        llm_extraction_manifest=llm_extraction_manifest,
        event_workflow=event_workflow,
        wisburg_lens=wisburg_lens,
        evidence_work_order=evidence_work_order,
        language_policy=language_policy,
    )
    deterministic = deterministic_thesis(
        identity, ideas, evidence, valuation, data_quality, management,
        expectations, management_sources, external_evidence, calibration,
        company_economics=company_economics,
        thesis_clusters=thesis_clusters,
        evidence_work_order=evidence_work_order,
        source="deterministic",
    )
    deterministic.language_audit = build_language_audit(prompt_pack, language_policy)
    if provider is None:
        reviews = _secondary_review_if_needed(
            secondary_provider,
            prompt_pack,
            deterministic,
            enabled=enable_secondary,
            min_stage=secondary_min_stage,
        )
        deterministic.llm_manifest = _manifest_with_guardrails(
            prompt_pack,
            provider="disabled",
            model="none",
            status="Disabled",
            redacted_config={"enabled": "false"},
            message="LLM thesis synthesis is disabled; deterministic IC brief was used.",
            secondary_reviews=len(reviews),
        )
        deterministic.llm_reviews = reviews
        deterministic.llm_comparison = _comparison_from_reviews(deterministic, reviews)
        return deterministic
    if deterministic.evidence_sufficiency.status in {"Weak", "No thesis"}:
        deterministic.llm_manifest = _manifest_with_guardrails(
            prompt_pack,
            provider=getattr(provider, "provider_name", "unknown"),
            model=getattr(provider, "model", "unknown"),
            status="Skipped",
            redacted_config={"provider": getattr(provider, "provider_name", "unknown"), "api_key": "[redacted]"},
            message=(
                "LLM synthesis skipped because deterministic evidence is weak. "
                "The app does not use an LLM to polish weak evidence into an investment thesis."
            ),
        )
        deterministic.thesis_brief.data_gaps.append(
            "LLM synthesis skipped until evidence reaches Research-Ready quality."
        )
        deterministic.llm_comparison = _comparison_from_reviews(deterministic, [])
        return deterministic

    try:
        payload = provider.complete_json(prompt_pack)
        parsed = _parse_llm_payload(payload, prompt_pack, deterministic)
        parsed.language_audit = deterministic.language_audit
        reviews = _secondary_review_if_needed(
            secondary_provider,
            prompt_pack,
            parsed,
            enabled=enable_secondary,
            min_stage=secondary_min_stage,
        )
        parsed.llm_manifest = _manifest_with_guardrails(
            prompt_pack,
            provider=provider.provider_name,
            model=provider.model,
            status="Available",
            redacted_config={"provider": provider.provider_name, "model": provider.model, "api_key": "[redacted]"},
            message="LLM synthesis accepted after citation and output guardrails.",
            secondary_reviews=len(reviews),
            timeout_seconds=getattr(provider, "timeout_seconds", None),
        )
        parsed.llm_reviews = reviews
        parsed.llm_comparison = _comparison_from_reviews(parsed, reviews)
        return parsed
    except Exception as exc:
        failure = _classify_llm_exception(exc)
        deterministic.thesis_brief.data_gaps.append(
            "LLM provider did not produce an accepted synthesis; deterministic brief used."
            if failure["status"] != "Rejected"
            else "LLM synthesis rejected by evidence guardrails; deterministic brief used."
        )
        reviews = _secondary_review_if_needed(
            secondary_provider,
            prompt_pack,
            deterministic,
            enabled=enable_secondary,
            min_stage=secondary_min_stage,
        )
        deterministic.llm_manifest = _manifest_with_guardrails(
            prompt_pack,
            provider=getattr(provider, "provider_name", "unknown"),
            model=getattr(provider, "model", "unknown"),
            status=failure["status"],
            redacted_config={"provider": getattr(provider, "provider_name", "unknown"), "api_key": "[redacted]"},
            message=f'{failure["message_prefix"]}: {_redact(str(exc))}',
            secondary_reviews=len(reviews),
            failure_class=failure["failure_class"],
            retryable=bool(failure["retryable"]),
            provider_health=str(failure["provider_health"]),
            timeout_seconds=getattr(provider, "timeout_seconds", None),
        )
        deterministic.llm_reviews = reviews
        deterministic.llm_comparison = _comparison_from_reviews(deterministic, reviews)
        return deterministic


def deterministic_thesis(
    identity: CompanyIdentity,
    ideas: list[TradeIdea],
    evidence: EvidenceLedger,
    valuation: ValuationResult,
    data_quality: DataQualityReport,
    management: ManagementCredibility,
    expectations: ExpectationsBridge,
    management_sources: ManagementSourcePackage,
    external_evidence: ExternalEvidenceBundle,
    calibration: CalibrationReport,
    company_economics: CompanyEconomics | None = None,
    thesis_clusters: list[ThesisCluster] | None = None,
    evidence_work_order: EvidenceWorkOrder | None = None,
    source: str = "deterministic",
) -> ThesisSynthesisResult:
    top = _top_idea(ideas)
    sufficiency = _evidence_sufficiency(top, evidence, valuation, data_quality, management, calibration)
    if not top:
        brief = ThesisBrief(
            status="No thesis",
            verdict="No convincing thesis yet",
            idea_id=None,
            title=f"{identity.ticker}: no investable thesis generated",
            stage="No thesis",
            direction="Neutral",
            thesis="No material source-linked setup passed the research-ready bar.",
            variant_perception="No variant view can be stated without a source-linked setup.",
            source=source,
            data_gaps=list(sufficiency.data_gaps),
        )
        critique = ThesisCritique(
            strongest_counter_thesis="No thesis exists yet; the main risk is over-interpreting fragmented data.",
            missing_evidence=list(sufficiency.data_gaps),
            what_would_falsify=["No material filing, consensus, valuation, or price-attribution evidence emerges after refresh."],
        )
        return ThesisSynthesisResult(
            brief,
            critique,
            sufficiency,
            [],
            _empty_manifest(source),
            [],
            LlmComparison("Not requested", verdict=brief.verdict),
            LanguageAudit("bilingual_audit"),
        )

    action_plan = _action_plan_from_idea(top)
    evidence_chain = _evidence_chain(top, evidence)
    citations = _top_citations(top)
    verdict = _verdict_from_sufficiency(sufficiency)
    best_cluster = _cluster_for_top(top, thesis_clusters)
    economics_prefix = _economics_prefix(company_economics, best_cluster)
    if sufficiency.status in {"Weak", "No thesis"} or top.stage == "Candidate":
        gap_text = "; ".join(sufficiency.data_gaps[:4]) or "missing corroborating evidence"
        work_order_gaps = _work_order_gaps(evidence_work_order)
        brief = ThesisBrief(
            status=sufficiency.status,
            verdict="No convincing thesis yet",
            idea_id=top.idea_id,
            title=f"{identity.ticker}: strongest candidate is not IC-ready",
            stage=top.stage,
            direction="Neutral",
            thesis=(
                f"{economics_prefix}The strongest current candidate is '{top.title}', but it should remain an "
                f"investigation item rather than an investment thesis because {gap_text}."
            ),
            variant_perception="No variant perception should be stated until the candidate has corroborating evidence, price/consensus context, and a valuation or payoff anchor.",
            evidence_chain=evidence_chain,
            citations=citations,
            supporting_idea_ids=_cluster_idea_ids(top, ideas),
            source=source,
            data_gaps=list(sufficiency.data_gaps) + work_order_gaps,
        )
        critique = ThesisCritique(
            strongest_counter_thesis=(
                top.strongest_counter_thesis
                if top.strongest_counter_thesis and top.strongest_counter_thesis != "Not yet evaluated."
                else "The detected signal may be disclosure noise or already captured by consensus and price."
            ),
            key_uncertainties=_key_uncertainties(top, valuation, data_quality, management_sources, external_evidence),
            missing_evidence=list(sufficiency.data_gaps) + work_order_gaps,
            what_would_falsify=[item.break_trigger for item in top.monitor_items[:4]] or ["No corroborating evidence emerges after refresh."],
        )
        return ThesisSynthesisResult(
            brief,
            critique,
            sufficiency,
            action_plan,
            _empty_manifest(source),
            [],
            LlmComparison("Not requested", verdict=brief.verdict),
            LanguageAudit("bilingual_audit"),
        )
    brief = ThesisBrief(
        status=sufficiency.status,
        verdict=verdict,
        idea_id=top.idea_id,
        title=top.title,
        stage=top.stage,
        direction=top.direction,
        thesis=f"{economics_prefix}{top.thesis}",
        variant_perception=(
            f"{best_cluster.priced_in} {top.variant_perception}"
            if best_cluster and best_cluster.priced_in != "Unknown" else top.variant_perception
        ),
        evidence_chain=evidence_chain,
        citations=citations,
        supporting_idea_ids=_cluster_idea_ids(top, ideas),
        source=source,
        data_gaps=list(sufficiency.data_gaps),
    )
    critique = ThesisCritique(
        strongest_counter_thesis=top.strongest_counter_thesis or evidence.strongest_counter_thesis,
        key_uncertainties=_key_uncertainties(top, valuation, data_quality, management_sources, external_evidence),
        missing_evidence=list(sufficiency.data_gaps),
        what_would_falsify=[item.break_trigger for item in top.monitor_items[:4]] or ["Future evidence fails to confirm the source-linked thesis."],
    )
    return ThesisSynthesisResult(
        brief,
        critique,
        sufficiency,
        action_plan,
        _empty_manifest(source),
        [],
        LlmComparison("Not requested", verdict=brief.verdict),
        LanguageAudit("bilingual_audit"),
    )


def build_prompt_pack(
    identity: CompanyIdentity,
    ideas: list[TradeIdea],
    evidence: EvidenceLedger,
    valuation: ValuationResult,
    data_quality: DataQualityReport,
    management: ManagementCredibility,
    expectations: ExpectationsBridge,
    management_sources: ManagementSourcePackage,
    external_evidence: ExternalEvidenceBundle,
    calibration: CalibrationReport,
    historical_references: HistoricalReferenceSet | None = None,
    thesis_validation: ThesisValidationReport | None = None,
    budget_policy: BudgetPolicy | None = None,
    manual_data_status: BringYourOwnDataStatus | None = None,
    company_economics: CompanyEconomics | None = None,
    credit_lens: CreditLens | None = None,
    thesis_clusters: list[ThesisCluster] | None = None,
    research_questions: list[ResearchQuestion] | None = None,
    research_scout: ResearchScoutReport | None = None,
    validated_claims: ClaimValidationResult | None = None,
    source_plan: ResearchSourcePlan | None = None,
    llm_extraction_manifest: LlmExtractionManifest | None = None,
    event_workflow: EventWorkflow | None = None,
    wisburg_lens: WisburgResearchLens | None = None,
    evidence_work_order: EvidenceWorkOrder | None = None,
    language_policy: str = "bilingual_audit",
) -> dict:
    citations: dict[str, dict] = {}
    evidence_rows: dict[str, dict] = {}
    for item in evidence.items[:24]:
        evidence_id = item.evidence_id
        citation_id = None
        if item.citation:
            citation_id = _citation_id(item.citation)
            citations[citation_id] = _citation_payload(item.citation)
        source_language = _detect_language(item.statement)
        evidence_rows[evidence_id] = {
            "stance": item.stance,
            "statement": item.statement[:900],
            "source_language": source_language,
            "original_excerpt": item.statement[:900],
            "translated_summary": None,
            "source_tier": item.source_tier,
            "source_type": item.source_type,
            "materiality": item.materiality,
            "citation_id": citation_id,
        }
    idea_rows = []
    for idea in ideas[:8]:
        idea_rows.append({
            "idea_id": idea.idea_id,
            "title": idea.title,
            "stage": idea.stage,
            "direction": idea.direction,
            "thesis": idea.thesis,
            "variant_perception": idea.variant_perception,
            "score": idea.score.total if idea.score else None,
            "score_cap": idea.score.score_cap_reason if idea.score else None,
            "market_capture": idea.market_capture.category if idea.market_capture else "Unknown",
            "counter_thesis": idea.strongest_counter_thesis,
            "thesis_cluster_id": idea.thesis_cluster_id,
            "thesis_cluster_label": idea.thesis_cluster_label,
            "economic_driver": (
                idea.source_events[0].metrics.get("economic_driver")
                if idea.source_events else None
            ),
            "driver_materiality": (
                idea.source_events[0].metrics.get("driver_materiality")
                if idea.source_events else None
            ),
            "driver_attribution": asdict(idea.driver_attribution) if idea.driver_attribution else None,
            "driver_analysis": asdict(idea.driver_analysis) if idea.driver_analysis else None,
            "conviction_chain": asdict(idea.conviction_chain) if idea.conviction_chain else None,
            "peer_metric_readthrough": [asdict(item) for item in idea.peer_metric_readthrough[:5]],
            "causal_bridge_status": idea.causal_bridge_status,
            "equity_credit_lens": dict(idea.equity_credit_lens),
            "llm_contribution": dict(idea.llm_contribution),
            "validated_claim_ids": idea.validated_claim_ids,
            "thesis_grade_status": idea.thesis_grade_status,
            "direction_rationale": idea.direction_rationale,
            "monitor_items": [asdict(item) for item in idea.monitor_items[:4]],
        })
        for citation in idea.citations[:6]:
            citations[_citation_id(citation)] = _citation_payload(citation)
    external_evidence_items = _external_evidence_payload(external_evidence)
    for item in external_evidence.evidence[:16]:
        if item.citation:
            citations[_citation_id(item.citation)] = _citation_payload(item.citation)
    if wisburg_lens:
        for report in wisburg_lens.reports[:12]:
            if report.citation:
                citations[_citation_id(report.citation)] = _citation_payload(report.citation)
        for claim in wisburg_lens.structured_claims[:20]:
            citation_id = _citation_id(claim.citation) if claim.citation else None
            if claim.citation and citation_id:
                citations[citation_id] = _citation_payload(claim.citation)
            evidence_rows[f"wisburg:{claim.claim_id}"] = {
                "stance": "external_context",
                "statement": claim.statement[:700],
                "source_language": _detect_language(claim.statement),
                "original_excerpt": claim.statement[:700],
                "translated_summary": None,
                "source_tier": claim.source_tier,
                "source_type": "wisburg_external_research",
                "materiality": "context_only",
                "citation_id": citation_id,
                "evidence_label": claim.evidence_label,
                "allowed_stage": claim.allowed_stage,
                "corroboration_status": claim.corroboration_status,
            }
        for revision in wisburg_lens.revisions[:12]:
            if revision.citation:
                citations[_citation_id(revision.citation)] = _citation_payload(revision.citation)
    prompt_pack = {
        "prompt_version": PROMPT_VERSION,
        "task": (
            "Create an IC-ready thesis brief. Use only evidence/citation IDs in this pack. "
            "Return JSON with verdict, thesis, variant_perception, evidence_chain, strongest_counter_thesis, "
            "key_uncertainties, missing_evidence, what_would_falsify, and action_plan."
        ),
        "output_schema": {
            "verdict": "string",
            "thesis": "string",
            "variant_perception": "string",
            "evidence_chain": [
                {"claim": "string", "citation_ids": ["one or more IDs from citations"]}
            ],
            "strongest_counter_thesis": "string",
            "key_uncertainties": ["string"],
            "missing_evidence": ["string"],
            "what_would_falsify": ["string"],
            "action_plan": [
                {
                    "criterion": "string",
                    "source_field": "string",
                    "metric": "string or null",
                    "operator": "string or null",
                    "threshold": "number or null",
                    "deadline": "string or null",
                    "confirm_trigger": "string",
                    "break_trigger": "string",
                    "cadence": "string",
                }
            ],
        },
        "language_policy": language_policy,
        "company": asdict(identity),
        "ideas": idea_rows,
        "evidence": evidence_rows,
        "citations": citations,
        "valuation": {
            "status": valuation.status,
            "template": valuation.template,
            "cases": [asdict(case) for case in valuation.cases[:3]],
            "expected_return_pct": valuation.expected_return_pct,
            "missing_data": valuation.missing_data,
        },
        "data_quality": asdict(data_quality),
        "management": {
            "status": management.status,
            "score": management.score,
            "data_gaps": management.data_gaps,
        },
        "expectations": {
            "status": expectations.status,
            "headline": expectations.headline,
            "data_gaps": expectations.data_gaps,
        },
        "management_claims": [
            {
                "claim_id": claim.claim_id,
                "status": claim.status,
                "statement": claim.statement[:700],
                "source_tier": claim.source_tier,
                "citation_id": _citation_id(claim.citation),
            }
            for claim in management_sources.claims[:12]
        ],
        "external_evidence_status": {
            "status": external_evidence.status,
            "data_gaps": external_evidence.data_gaps[:10],
        },
        "external_evidence_items": external_evidence_items,
        "calibration": asdict(calibration),
        "budget_policy": asdict(budget_policy) if budget_policy else _missing_budget_payload(),
        "manual_data_status": asdict(manual_data_status) if manual_data_status else _missing_manual_payload(),
        "company_economics": asdict(company_economics) if company_economics else _missing_economics_payload(),
        "credit_lens": asdict(credit_lens) if credit_lens else _missing_credit_lens_payload(),
        "thesis_clusters": [asdict(cluster) for cluster in (thesis_clusters or [])[:6]],
        "research_questions": [asdict(question) for question in (research_questions or [])[:8]],
        "research_scout": _research_scout_payload(research_scout),
        "validated_claims": asdict(validated_claims) if validated_claims else _missing_claims_payload(),
        "source_plan": asdict(source_plan) if source_plan else _missing_source_plan_payload(),
        "llm_extraction_manifest": asdict(llm_extraction_manifest) if llm_extraction_manifest else _missing_extraction_payload(),
        "event_workflow": asdict(event_workflow) if event_workflow else _missing_event_workflow_payload(),
        "evidence_work_order": _evidence_work_order_payload(evidence_work_order),
        "wisburg_lens": lens_to_prompt_payload(wisburg_lens),
        "historical_references": _historical_reference_payload(historical_references),
        "thesis_validation": _thesis_validation_payload(thesis_validation),
        "conviction_inputs": _conviction_input_payload(
            ideas, evidence, data_quality, valuation, management_sources,
            external_evidence, calibration, historical_references,
        ),
        "rules": [
            "Do not invent facts, price targets, probabilities, or citations.",
            "Every evidence_chain item must cite one or more allowed citation IDs.",
            "If evidence is insufficient, verdict must be 'No convincing thesis yet'.",
            "Historical references are analog context only. Do not treat sparse analogs as calibrated probability evidence.",
            "Use thesis_validation to separate corroborated, contradictory, mixed, and missing evidence channels.",
            "Start from company_economics and thesis_clusters. Do not treat a signal as thesis-relevant unless it maps to a material business or industry driver.",
            "Use credit_lens for credit analyst context; it is not a rating opinion and cannot override valuation or evidence gates.",
            "Use research_questions to explain missing evidence and next-source checks; do not phrase them as trade recommendations.",
            "Use research_scout to build a company, sector, peer, and geography story from registered source needs; do not treat unanswered scout questions as facts.",
            "Use each idea's conviction_chain to explain the causal path from source change to driver, KPI impact, valuation/payoff, expectation gap, catalyst, and falsification tests.",
            "Use peer_metric_readthrough to discuss operating peer evidence separately from stock-price sympathy moves.",
            "Use llm_contribution only as process disclosure; it is not evidence and cannot promote an idea.",
            "Use validated_claims as the authority for exact changed text, direction, and thesis-grade status.",
            "Do not promote Watch Item or Not thesis-grade claims; recommend source_plan follow-up instead.",
            "Use event_workflow for concrete next-source checks and monitoring actions; do not invent workflow tasks outside it.",
            "Use evidence_work_order as the prioritized analyst agenda. Do not treat open work-order items as completed evidence.",
            "Respect budget_policy and manual_data_status. Missing paid data is an evidence gap, not a reason to invent facts.",
            "Use conviction_inputs to disclose process gaps; do not turn process gaps into positive evidence.",
            "Use external_evidence_items, including Wisburg, only to sharpen context, counter-thesis, narrative saturation, and next-source checks; never as primary proof or standalone High-Conviction support.",
            "Wisburg structured claims may be discussed only when their citation_id is present in the allowed citations map; describe their corroboration_status explicitly.",
            "Use wisburg_lens for outside-analyst debate, bounded structured claims, and source suggestions only; Wisburg revisions, targets, ratings, and estimates are external analyst context, never official consensus.",
            "Preserve source-language excerpts. If Chinese evidence is present, separate original excerpts from translated or paraphrased summaries.",
        ],
    }
    return _sanitize_prompt_pack(prompt_pack)


def _missing_budget_payload() -> dict:
    return {
        "mode": "Unknown",
        "cost_target": "Unknown",
        "description": "Budget policy was not attached to this run.",
        "enabled_sources": [],
        "warnings": ["Budget policy unavailable."],
    }


def _missing_manual_payload() -> dict:
    return {
        "status": "Unavailable",
        "base_dir": "",
        "sources": [],
        "data_gaps": ["Manual/BYOD data scan was not run."],
    }


def _missing_economics_payload() -> dict:
    return {
        "status": "Unavailable",
        "business_model": "Company economics model was not built.",
        "drivers": [],
        "data_gaps": ["No company economics model was attached to this run."],
    }


def _missing_credit_lens_payload() -> dict:
    return {
        "status": "Unavailable",
        "risk_level": "Unknown",
        "summary": "Credit lens was not attached to this run.",
        "metrics": [],
        "positives": [],
        "risks": [],
        "required_evidence": [],
        "data_gaps": ["No structured credit analysis was attached to this run."],
    }


def _missing_claims_payload() -> dict:
    return {
        "status": "Unavailable",
        "claims": [],
        "data_gaps": ["Claim validation was not attached to this run."],
    }


def _missing_source_plan_payload() -> dict:
    return {
        "status": "Unavailable",
        "requests": [],
        "outcomes": [],
        "data_gaps": ["Source planning was not attached to this run."],
    }


def _missing_event_workflow_payload() -> dict:
    return {
        "status": "Unavailable",
        "items": [],
        "data_gaps": ["Event workflow was not attached to this run."],
    }


def _research_scout_payload(report: ResearchScoutReport | None) -> dict:
    if report is None:
        return {
            "status": "Unavailable",
            "summary": "Research Scout was not attached to this run.",
            "questions": [],
            "company_story_axes": [],
            "sector_story_axes": [],
            "geography_story_axes": [],
            "peer_story_axes": [],
            "data_gaps": ["Research Scout unavailable."],
        }
    return {
        "status": report.status,
        "summary": report.summary,
        "provider": report.provider,
        "generated_at": report.generated_at,
        "questions": [asdict(question) for question in report.questions[:10]],
        "company_story_axes": report.company_story_axes[:6],
        "sector_story_axes": report.sector_story_axes[:6],
        "geography_story_axes": report.geography_story_axes[:6],
        "peer_story_axes": report.peer_story_axes[:4],
        "data_gaps": report.data_gaps[:6],
    }


def _evidence_work_order_payload(work_order: EvidenceWorkOrder | None) -> dict:
    if work_order is None:
        return {
            "status": "Unavailable",
            "summary": "Evidence work order was not attached to this run.",
            "items": [],
            "data_gaps": ["Evidence work order unavailable."],
        }
    return {
        "status": work_order.status,
        "summary": work_order.summary,
        "items": [
            {
                "work_id": item.work_id,
                "priority": item.priority,
                "channel": item.channel,
                "action": item.action,
                "source_type": item.source_type,
                "expected_output": item.expected_output,
                "why_it_matters": item.why_it_matters,
                "origin": item.origin,
                "related_idea_ids": item.related_idea_ids[:5],
                "blocks_research_ready": item.blocks_research_ready,
                "blocks_high_conviction": item.blocks_high_conviction,
                "cost_latency": item.cost_latency,
                "acceptance_criteria": item.acceptance_criteria[:3],
                "falsification_tests": item.falsification_tests[:3],
            }
            for item in work_order.items[:12]
        ],
        "data_gaps": work_order.data_gaps[:8],
    }


def _missing_extraction_payload() -> dict:
    return {
        "provider": "none",
        "model": "none",
        "prompt_version": "none",
        "status": "Unavailable",
        "messages": ["LLM extraction manifest was not attached to this run."],
    }


def _external_evidence_payload(external_evidence: ExternalEvidenceBundle) -> list[dict]:
    rows: list[dict] = []
    for item in external_evidence.evidence[:16]:
        citation_id = _citation_id(item.citation) if item.citation else None
        language = next((tag for tag in item.tags if tag in {"en", "zh"}), _detect_language(item.summary))
        rows.append({
            "provider": item.provider,
            "source_type": item.source_type,
            "title": item.title[:240],
            "summary": item.summary[:900],
            "source_as_of": item.source_as_of,
            "source_tier": item.source_tier,
            "official": item.official,
            "confidence": item.confidence,
            "citation_id": citation_id,
            "source_language": language,
            "licensing_policy": item.licensing_policy,
            "high_conviction_role": (
                "context_only" if item.disqualifies_high_conviction else "supporting_context"
            ),
        })
    return rows


def _thesis_validation_payload(
    thesis_validation: ThesisValidationReport | None,
) -> dict:
    if thesis_validation is None:
        return {
            "status": "Unavailable",
            "summary": "Thesis validation matrix was not run.",
            "checks": [],
            "required_next_evidence": ["Run thesis validation before relying on source-channel corroboration."],
            "next_evidence_actions": [],
        }
    return {
        "status": thesis_validation.status,
        "score": thesis_validation.score,
        "summary": thesis_validation.summary,
        "top_idea_id": thesis_validation.top_idea_id,
        "top_idea_title": thesis_validation.top_idea_title,
        "checks": [
            {
                "channel": check.channel,
                "status": check.status,
                "score": check.score,
                "evidence": check.evidence,
                "implication": check.implication,
                "gaps": check.gaps,
                "source_tier": check.source_tier,
                "citation_count": check.citation_count,
            }
            for check in thesis_validation.checks
        ],
        "strongest_supports": thesis_validation.strongest_supports,
        "strongest_contradictions": thesis_validation.strongest_contradictions,
        "required_next_evidence": thesis_validation.required_next_evidence,
        "next_evidence_actions": [
            {
                "channel": item.channel,
                "priority": item.priority,
                "action": item.action,
                "source": item.source,
                "why_it_matters": item.why_it_matters,
                "blocks_high_conviction": item.blocks_high_conviction,
            }
            for item in thesis_validation.next_evidence_actions
        ],
    }


def _conviction_input_payload(
    ideas: list[TradeIdea],
    evidence: EvidenceLedger,
    data_quality: DataQualityReport,
    valuation: ValuationResult,
    management_sources: ManagementSourcePackage,
    external_evidence: ExternalEvidenceBundle,
    calibration: CalibrationReport,
    historical_references: HistoricalReferenceSet | None,
) -> dict:
    top = _top_idea(ideas)
    top_claim = next((claim for claim in evidence.claims if top and claim.idea_id == top.idea_id), None)
    top_support_ids = set(top_claim.supporting_evidence_ids if top_claim else [])
    top_supports = [item for item in evidence.items if item.evidence_id in top_support_ids]
    top_unresolved_counters = _unresolved_counters_for_idea(top, evidence)
    primary_support_count = sum(1 for item in top_supports if item.source_tier == 1)
    return {
        "top_idea_stage": top.stage if top else "No thesis",
        "top_idea_score": top.score.total if top and top.score else None,
        "primary_support_count": primary_support_count,
        "support_count": len(top_supports),
        "unresolved_material_contradictions": len(top_unresolved_counters),
        "data_quality_status": data_quality.status,
        "primary_source_coverage_pct": data_quality.primary_source_coverage_pct,
        "point_in_time_complete": data_quality.point_in_time_complete,
        "valuation_status": valuation.status,
        "management_claim_count": len(management_sources.claims),
        "management_cross_check_count": len(management_sources.cross_checks),
        "official_external_evidence_count": sum(1 for item in external_evidence.evidence if item.official),
        "historical_reference_status": historical_references.status if historical_references else "Unavailable",
        "historical_resolved_sample_size": historical_references.sample_size if historical_references else 0,
        "calibration_status": calibration.status,
        "calibration_sample_size": calibration.sample_size,
    }


def _cluster_for_top(
    top: TradeIdea | None,
    thesis_clusters: list[ThesisCluster] | None,
) -> ThesisCluster | None:
    if not top or not thesis_clusters:
        return None
    return next(
        (cluster for cluster in thesis_clusters if top.idea_id in cluster.idea_ids),
        thesis_clusters[0] if thesis_clusters else None,
    )


def _economics_prefix(
    company_economics: CompanyEconomics | None,
    cluster: ThesisCluster | None,
) -> str:
    if not company_economics:
        return ""
    driver = cluster.driver_name if cluster else None
    if not driver or driver == "Unmapped":
        return (
            f"Business context: {company_economics.business_model} "
            "The current evidence is not yet tied to a material driver. "
        )
    return (
        f"Business context: {company_economics.business_model} "
        f"The thesis is anchored to {driver}. "
    )


def _work_order_gaps(work_order: EvidenceWorkOrder | None) -> list[str]:
    if not work_order:
        return []
    return [
        f"Evidence work order [{item.priority}]: {item.action}"
        for item in work_order.items[:4]
        if item.blocks_research_ready or item.blocks_high_conviction
    ]


def _historical_reference_payload(
    historical_references: HistoricalReferenceSet | None,
) -> dict:
    if historical_references is None:
        return {
            "status": "Unavailable",
            "summary": "Historical reference matching was not run.",
            "references": [],
            "data_gaps": ["Historical reference matching was not run."],
        }
    return {
        "status": historical_references.status,
        "scope": historical_references.scope,
        "sample_size": historical_references.sample_size,
        "minimum_sample_size": historical_references.minimum_sample_size,
        "hit_rate_pct": historical_references.hit_rate_pct,
        "mean_realized_return_pct": historical_references.mean_realized_return_pct,
        "summary": historical_references.summary,
        "data_gaps": historical_references.data_gaps[:8],
        "references": [
            {
                "ticker": reference.ticker,
                "idea_title": reference.idea_title,
                "signal_family": reference.signal_family,
                "direction": reference.direction,
                "stage": reference.stage,
                "event_date": reference.event_date,
                "horizon": reference.horizon,
                "similarity_score": reference.similarity_score,
                "match_reasons": reference.match_reasons,
                "realized_return_pct": reference.realized_return_pct,
                "abnormal_return_pct": reference.abnormal_return_pct,
                "outcome_status": reference.outcome_status,
                "confidence": reference.confidence,
            }
            for reference in historical_references.references[:8]
        ],
    }


def _parse_llm_payload(payload: dict, prompt_pack: dict, fallback: ThesisSynthesisResult) -> ThesisSynthesisResult:
    allowed = set(prompt_pack["citations"])
    evidence_chain_raw = payload.get("evidence_chain") or []
    if not isinstance(evidence_chain_raw, list):
        raise ValueError("evidence_chain must be a list.")
    evidence_chain = []
    citation_ids = []
    for item in evidence_chain_raw:
        if isinstance(item, str):
            raise ValueError("Each evidence_chain item must include citation_ids.")
        text = str(item.get("claim") or item.get("text") or "").strip()
        ids = _citation_ids_from_chain_item(item, prompt_pack)
        if not text or not ids:
            raise ValueError("Every evidence_chain item requires text and citation_ids.")
        unknown = [citation_id for citation_id in ids if citation_id not in allowed]
        if unknown:
            raise ValueError(f"Unknown citation id(s): {', '.join(unknown)}")
        if not _claim_supported_by_citations(text, ids, prompt_pack):
            raise ValueError("LLM evidence-chain claim is not supported by the cited excerpt(s).")
        evidence_chain.append(f"{text} [citations: {', '.join(ids)}]")
        citation_ids.extend(ids)
    combined_text = " ".join(str(payload.get(key, "")) for key in ("thesis", "variant_perception", "verdict"))
    if _looks_like_uncited_target_or_probability(combined_text):
        raise ValueError("LLM output appears to include an unsupported target or probability.")
    fallback_thesis = fallback.thesis_brief.thesis
    fallback_variant = fallback.thesis_brief.variant_perception
    fallback_gaps = list(fallback.thesis_brief.data_gaps)
    brief = fallback.thesis_brief
    brief.source = "llm"
    brief.verdict = str(payload.get("verdict") or brief.verdict)
    brief.thesis = str(payload.get("thesis") or brief.thesis)
    brief.variant_perception = str(payload.get("variant_perception") or brief.variant_perception)
    brief.evidence_chain = evidence_chain or brief.evidence_chain
    brief.citations = [_citation_from_payload(prompt_pack["citations"][citation_id]) for citation_id in sorted(set(citation_ids))]
    if fallback.evidence_sufficiency.status in {"Weak", "No thesis"}:
        brief.verdict = "No convincing thesis yet"
        brief.thesis = fallback_thesis
        brief.variant_perception = fallback_variant
        brief.data_gaps = _dedupe(fallback_gaps + ["LLM synthesis cannot upgrade a weak deterministic evidence base."])
    critique = ThesisCritique(
        strongest_counter_thesis=str(payload.get("strongest_counter_thesis") or fallback.thesis_critique.strongest_counter_thesis),
        key_uncertainties=_string_list(payload.get("key_uncertainties")) or fallback.thesis_critique.key_uncertainties,
        missing_evidence=_string_list(payload.get("missing_evidence")) or fallback.thesis_critique.missing_evidence,
        what_would_falsify=_string_list(payload.get("what_would_falsify")) or fallback.thesis_critique.what_would_falsify,
    )
    action_plan = _parse_action_plan(payload.get("action_plan")) or fallback.action_plan
    return ThesisSynthesisResult(
        brief,
        critique,
        fallback.evidence_sufficiency,
        action_plan,
        fallback.llm_manifest,
        list(fallback.llm_reviews),
        fallback.llm_comparison,
        fallback.language_audit,
    )


def _citation_ids_from_chain_item(item: dict, prompt_pack: dict) -> list[str]:
    raw_values = []
    for key in (
        "citation_ids",
        "citation_id",
        "citationIds",
        "citations",
        "source_ids",
        "sourceIds",
        "source_citation_ids",
    ):
        raw_values.extend(_raw_id_values(item.get(key)))
    for key in ("evidence_ids", "evidence_id", "evidenceIds"):
        for evidence_id in _raw_id_values(item.get(key)):
            citation_id = (prompt_pack.get("evidence") or {}).get(str(evidence_id), {}).get("citation_id")
            if citation_id:
                raw_values.append(citation_id)
    allowed = set(prompt_pack.get("citations") or {})
    result = []
    for value in raw_values:
        clean = str(value).strip()
        if clean in allowed:
            result.append(clean)
    return _dedupe(result)


def _claim_supported_by_citations(claim: str, citation_ids: list[str], prompt_pack: dict) -> bool:
    claim_tokens = set(_support_tokens(claim))
    if not claim_tokens:
        return False
    support_text = " ".join(
        _citation_support_text((prompt_pack.get("citations") or {}).get(citation_id, {}))
        for citation_id in citation_ids
    )
    support_tokens = set(_support_tokens(support_text))
    if not support_tokens:
        return False
    overlap = claim_tokens & support_tokens
    if overlap:
        return True
    # Allow common numeric/metric shorthand only when the literal number appears in the cited excerpt.
    claim_numbers = set(re.findall(r"\b\d+(?:\.\d+)?%?\b", claim))
    support_numbers = set(re.findall(r"\b\d+(?:\.\d+)?%?\b", support_text))
    return bool(claim_numbers and claim_numbers & support_numbers)


def _citation_support_text(payload: dict) -> str:
    return " ".join(
        str(payload.get(key) or "")
        for key in ("snippet", "original_excerpt", "translated_summary", "section", "source")
    )


def _support_tokens(text: str) -> list[str]:
    stopwords = {
        "a", "an", "and", "are", "as", "at", "be", "because", "but", "by", "can", "could",
        "company", "current", "evidence", "for", "from", "has", "have", "in", "into", "is",
        "it", "may", "more", "not", "of", "on", "or", "source", "supports", "than", "that",
        "the", "this", "to", "was", "were", "with", "without", "would",
    }
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9]{2,}", text.lower())
    return [token for token in tokens if token not in stopwords]


def _raw_id_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [
            str(value[key])
            for key in ("citation_id", "id", "citationId", "source_id")
            if value.get(key)
        ]
    if isinstance(value, list):
        rows = []
        for item in value:
            rows.extend(_raw_id_values(item))
        return rows
    return [str(value)]


def _parse_action_plan(raw: Any) -> list[ActionPlan]:
    if not isinstance(raw, list):
        return []
    rows = []
    for item in raw[:6]:
        if not isinstance(item, dict):
            continue
        rows.append(ActionPlan(
            criterion=str(item.get("criterion") or "Thesis check"),
            source_field=str(item.get("source_field") or item.get("source") or "manual_review"),
            metric=item.get("metric"),
            operator=item.get("operator"),
            threshold=_float_or_none(item.get("threshold")),
            deadline=item.get("deadline"),
            confirm_trigger=str(item.get("confirm_trigger") or "Evidence confirms thesis direction."),
            break_trigger=str(item.get("break_trigger") or "Evidence contradicts thesis direction."),
            cadence=str(item.get("cadence") or "Daily or after material events"),
        ))
    return rows


def _evidence_sufficiency(
    idea: TradeIdea | None,
    evidence: EvidenceLedger,
    valuation: ValuationResult,
    data_quality: DataQualityReport,
    management: ManagementCredibility,
    calibration: CalibrationReport,
) -> EvidenceSufficiency:
    if not idea:
        return EvidenceSufficiency("No thesis", 0, "No generated idea is available.", ["Run research with source coverage."])
    score = idea.score.total if idea.score else 0
    gaps = []
    if idea.gate_result:
        gaps.extend(idea.gate_result.research_ready_failed[:4])
        gaps.extend(idea.gate_result.high_conviction_failed[:4])
    if valuation.status != "Available":
        gaps.append("Internal valuation is unavailable or incomplete.")
    unresolved_counters = _unresolved_counters_for_idea(idea, evidence)
    if unresolved_counters:
        gaps.extend(
            f"Contradiction to resolve: {item.statement}"
            for item in unresolved_counters[:3]
        )
    if data_quality.status == "Weak":
        gaps.append("Data quality is weak.")
    composite = round(score * 0.65 + data_quality.score * 0.25 + (10 if valuation.status == "Available" else 0))
    if idea.stage == "High-Conviction" and composite >= 75 and not unresolved_counters:
        status = "Convincing"
    elif idea.stage == "Research-Ready" and composite >= 55:
        status = "Promising but incomplete"
    elif score >= 40:
        status = "Weak"
    else:
        status = "No thesis"
    rationale = (
        f"Top idea stage is {idea.stage}; idea score {score}/100; data quality {data_quality.score}/100; "
        f"valuation status {valuation.status}; management status {management.status}."
    )
    return EvidenceSufficiency(status, max(0, min(100, composite)), rationale, _dedupe(gaps))


def _unresolved_counters_for_idea(
    idea: TradeIdea | None,
    evidence: EvidenceLedger,
):
    if not idea:
        return []
    claim = next((item for item in evidence.claims if item.idea_id == idea.idea_id), None)
    if not claim:
        return []
    contradiction_ids = set(claim.contradicting_evidence_ids)
    return [
        item
        for item in evidence.items
        if item.evidence_id in contradiction_ids
        and item.stance == "Contradicts"
        and item.materiality >= 3
        and item.unresolved
    ]


def _top_idea(ideas: list[TradeIdea]) -> TradeIdea | None:
    if not ideas:
        return None
    stage_rank = {"High-Conviction": 3, "Investable": 3, "Research-Ready": 2, "Candidate": 1}
    return sorted(
        ideas,
        key=lambda item: (
            stage_rank.get(item.stage, 0),
            item.score.total if item.score else 0,
            len(item.citations),
        ),
        reverse=True,
    )[0]


def _evidence_chain(idea: TradeIdea, evidence: EvidenceLedger) -> list[str]:
    claim = next((item for item in evidence.claims if item.idea_id == idea.idea_id), None)
    rows = []
    if idea.conviction_chain:
        rows.append(idea.conviction_chain.summary)
        for step in idea.conviction_chain.steps[:5]:
            if step.status == "Complete":
                rows.append(f"{step.label}: {step.statement}")
    if claim:
        support = [
            item for item in evidence.items
            if item.claim_id == claim.claim_id and item.stance == "Supports"
        ]
        for item in support[:4]:
            rows.append(f"{item.statement} (Tier {item.source_tier})")
    if not rows:
        rows = [citation.snippet or citation.section or citation.source for citation in idea.citations[:4]]
    return [row for row in rows if row]


def _action_plan_from_idea(idea: TradeIdea) -> list[ActionPlan]:
    return [
        ActionPlan(
            criterion=item.criterion,
            source_field=item.source_field or item.data_source,
            metric=item.metric,
            operator=item.operator,
            threshold=item.confirm_threshold,
            deadline=item.deadline,
            confirm_trigger=item.confirm_trigger,
            break_trigger=item.break_trigger,
            cadence=item.cadence,
        )
        for item in idea.monitor_items[:6]
    ]


def _key_uncertainties(
    idea: TradeIdea,
    valuation: ValuationResult,
    data_quality: DataQualityReport,
    management_sources: ManagementSourcePackage,
    external_evidence: ExternalEvidenceBundle,
) -> list[str]:
    rows = []
    if idea.market_capture and idea.market_capture.data_gaps:
        rows.extend(idea.market_capture.data_gaps[:3])
    if valuation.missing_data:
        rows.extend(valuation.missing_data[:3])
    rows.extend(issue.message for issue in data_quality.issues[:3])
    rows.extend(management_sources.data_gaps[:3])
    rows.extend(external_evidence.data_gaps[:3])
    return _dedupe(rows) or ["The main uncertainty is whether the detected evidence is material enough to change forward estimates or valuation."]


def _cluster_idea_ids(top: TradeIdea, ideas: list[TradeIdea]) -> list[str]:
    category = top.signal_family or (top.source_events[0].category if top.source_events else "")
    direction = top.direction
    cluster = [
        idea.idea_id for idea in ideas
        if idea.direction == direction and (idea.signal_family == category or (idea.source_events and idea.source_events[0].category == category))
    ]
    for idea in ideas:
        if idea.idea_id in cluster:
            idea.thesis_cluster_id = f"{direction.lower()}:{category or 'general'}"
            idea.thesis_cluster_label = f"{direction} {category.replace('_', ' ') or 'general'} thesis"
    return cluster[:8]


def _verdict_from_sufficiency(sufficiency: EvidenceSufficiency) -> str:
    if sufficiency.status == "Convincing":
        return "Convincing thesis, subject to monitor checks and position sizing discipline."
    if sufficiency.status == "Promising but incomplete":
        return "Promising but incomplete thesis; research-ready, not yet high-conviction."
    if sufficiency.status == "Weak":
        return "Weak thesis; keep as a candidate until missing evidence is resolved."
    return "No convincing thesis yet"


def _top_citations(idea: TradeIdea) -> list[Citation]:
    seen = set()
    rows = []
    for citation in idea.citations:
        key = _citation_id(citation)
        if key in seen:
            continue
        rows.append(citation)
        seen.add(key)
    return rows[:6]


def _citation_id(citation: Citation) -> str:
    base = "|".join(str(value or "") for value in (
        citation.source, citation.url, citation.accession, citation.section, citation.snippet,
    ))
    return "c" + hashlib.sha1(base.encode("utf-8", errors="ignore")).hexdigest()[:10]


def _citation_payload(citation: Citation) -> dict:
    snippet = (citation.snippet or "")[:900]
    language = _detect_language(snippet or citation.section or citation.source)
    return {
        "source": citation.source,
        "url": citation.url,
        "filed": citation.filed,
        "form": citation.form,
        "section": citation.section,
        "snippet": snippet,
        "source_language": language,
        "original_excerpt": snippet,
        "translated_summary": None,
        "accession": citation.accession,
        "source_tier": citation.source_tier,
    }


def _citation_from_payload(payload: dict) -> Citation:
    return Citation(
        source=payload.get("source") or "Unknown",
        url=payload.get("url") or "",
        filed=payload.get("filed"),
        form=payload.get("form"),
        section=payload.get("section"),
        snippet=payload.get("snippet"),
        accession=payload.get("accession"),
        source_tier=payload.get("source_tier"),
    )


def _looks_like_uncited_target_or_probability(text: str) -> bool:
    lower = text.lower()
    target_terms = ("price target", "target price", "fair value is", "probability of success", "% probability", "chance of success")
    return any(term in lower for term in target_terms)


def build_language_audit(prompt_pack: dict, language_policy: str = "bilingual_audit") -> LanguageAudit:
    excerpts: list[SourceLanguageExcerpt] = []
    source_languages: list[str] = []
    chinese_notes: list[str] = []
    flags: list[str] = []
    for citation_id, payload in (prompt_pack.get("citations") or {}).items():
        language = payload.get("source_language") or _detect_language(payload.get("original_excerpt") or "")
        source_languages.append(language)
        excerpt = SourceLanguageExcerpt(
            citation_id=citation_id,
            source_language=language,
            original_excerpt=payload.get("original_excerpt") or payload.get("snippet") or "",
            translated_summary=payload.get("translated_summary"),
            source=payload.get("source") or "",
            url=payload.get("url") or "",
        )
        excerpts.append(excerpt)
        if language in {"zh-Hans", "zh-Hant", "mixed-zh-en"}:
            chinese_notes.append(f"{payload.get('source') or 'Source'} contains Chinese-language evidence; preserve the original excerpt when reviewing the thesis.")
    languages = _dedupe(source_languages) or ["en"]
    if len(languages) > 1:
        flags.append("mixed_language_evidence")
    if any(language in {"zh-Hans", "zh-Hant", "mixed-zh-en"} for language in languages):
        flags.append("chinese_source_present")
    return LanguageAudit(
        policy=language_policy,
        source_languages=languages,
        excerpts=excerpts[:12],
        chinese_source_notes=_dedupe(chinese_notes)[:6],
        flags=flags,
    )


def _secondary_review_if_needed(
    provider: LlmProvider | None,
    prompt_pack: dict,
    primary: ThesisSynthesisResult,
    *,
    enabled: bool,
    min_stage: str,
) -> list[LlmReviewResult]:
    if provider is None or not enabled:
        return []
    if not _stage_meets_minimum(primary.thesis_brief.stage, min_stage):
        return [
            LlmReviewResult(
                role="secondary_reader",
                provider=provider.provider_name,
                model=provider.model,
                status="Skipped",
                generated_at=_utc_now(),
                message=f"Secondary read skipped because top stage {primary.thesis_brief.stage} is below {min_stage}.",
            )
        ]
    review_pack = {
        "prompt_version": f"{PROMPT_VERSION}-secondary-review",
        "task": (
            "Act only as a skeptical secondary IC reader. Review the supplied evidence pack and primary thesis. "
            "Return JSON with summary, disagreements, missed_counter_thesis, unsupported_claims, "
            "language_quality_issues, readability_suggestions, and verdict. Do not invent facts, targets, "
            "probabilities, citations, or recommendations."
        ),
        "primary_thesis": {
            "verdict": primary.thesis_brief.verdict,
            "stage": primary.thesis_brief.stage,
            "thesis": primary.thesis_brief.thesis,
            "variant_perception": primary.thesis_brief.variant_perception,
            "evidence_chain": primary.thesis_brief.evidence_chain,
            "counter_thesis": primary.thesis_critique.strongest_counter_thesis,
            "action_plan": [asdict(item) for item in primary.action_plan],
        },
        "evidence_pack": prompt_pack,
        "rules": [
            "Critique the primary thesis; do not replace deterministic evidence or scores.",
            "No new facts, price targets, probabilities, or citations.",
            "Flag translation or source-language ambiguity explicitly.",
        ],
    }
    try:
        payload = provider.complete_json(review_pack)
        return [_parse_secondary_review(payload, provider)]
    except Exception as exc:
        return [
            LlmReviewResult(
                role="secondary_reader",
                provider=getattr(provider, "provider_name", "unknown"),
                model=getattr(provider, "model", "unknown"),
                status="Rejected",
                generated_at=_utc_now(),
                message=f"Secondary review was not used: {exc}",
            )
        ]


def _parse_secondary_review(payload: dict, provider: LlmProvider) -> LlmReviewResult:
    combined = " ".join(
        str(payload.get(key) or "")
        for key in ("summary", "verdict")
    )
    combined += " " + " ".join(_string_list(payload.get("disagreements")))
    if _looks_like_uncited_target_or_probability(combined):
        raise ValueError("Secondary output appears to include an unsupported target or probability.")
    return LlmReviewResult(
        role="secondary_reader",
        provider=provider.provider_name,
        model=provider.model,
        status="Available",
        summary=str(payload.get("summary") or ""),
        disagreements=_string_list(payload.get("disagreements")),
        missed_counter_thesis=_string_list(payload.get("missed_counter_thesis")),
        unsupported_claims=_string_list(payload.get("unsupported_claims")),
        language_quality_issues=_string_list(payload.get("language_quality_issues")),
        readability_suggestions=_string_list(payload.get("readability_suggestions")),
        generated_at=_utc_now(),
        message=str(payload.get("verdict") or "Secondary review accepted."),
    )


def _comparison_from_reviews(primary: ThesisSynthesisResult, reviews: list[LlmReviewResult]) -> LlmComparison:
    available = [review for review in reviews if review.status == "Available"]
    if not available:
        return LlmComparison(
            status="Primary only" if primary.llm_manifest.status == "Available" else "Deterministic only",
            primary_provider=primary.llm_manifest.provider,
            verdict=primary.thesis_brief.verdict,
        )
    review = available[0]
    disagreements = review.disagreements + review.missed_counter_thesis + review.language_quality_issues
    agreement = "High" if not disagreements and not review.unsupported_claims else "Needs review"
    return LlmComparison(
        status="Compared",
        primary_provider=primary.llm_manifest.provider,
        secondary_provider=review.provider,
        agreement=agreement,
        key_differences=_dedupe(disagreements)[:8],
        unsupported_claims=review.unsupported_claims[:8],
        verdict=review.message or primary.thesis_brief.verdict,
    )


def _stage_meets_minimum(stage: str, minimum: str) -> bool:
    rank = {"No thesis": 0, "Candidate": 1, "Research-Ready": 2, "High-Conviction": 3, "Investable": 3}
    return rank.get(stage, 0) >= rank.get(minimum or "Research-Ready", 2)


def _detect_language(text: str) -> str:
    if not text:
        return "unknown"
    has_cjk = any("\u4e00" <= char <= "\u9fff" for char in text)
    has_ascii = any("a" <= char.lower() <= "z" for char in text)
    if not has_cjk:
        return "en" if has_ascii else "unknown"
    traditional_markers = set("臺灣萬與業東證體後國會經營風險轉發")
    language = "zh-Hant" if any(char in traditional_markers for char in text) else "zh-Hans"
    return "mixed-zh-en" if has_ascii else language


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dedupe(rows: list[str]) -> list[str]:
    seen = set()
    output = []
    for row in rows:
        clean = str(row).strip()
        if clean and clean not in seen:
            output.append(clean)
            seen.add(clean)
    return output


def _estimate_tokens(payload: dict) -> int:
    return max(1, len(json.dumps(payload, ensure_ascii=True)) // 4)


_URL_RE = re.compile(r"https?://[^\s\"'<>]+")
_SK_TOKEN_RE = re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9._-]{8,}\b")
_BEARER_TOKEN_RE = re.compile(r"(?i)\bbearer\s+([A-Za-z0-9._~+/=-]{8,})")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|apikey|token|access_token|registrationkey|userid|user_id|authorization|auth|secret|password)"
    r"\s*[:=]\s*([^\s&\"'<>;,]+)"
)
_SECRET_QUERY_KEYS = {
    "api_key",
    "apikey",
    "key",
    "token",
    "access_token",
    "registrationkey",
    "userid",
    "user_id",
    "authorization",
    "auth",
    "secret",
    "password",
}
_SECRET_CONFIG_NAMES = (
    "ALPHAVANTAGE_API_KEY",
    "FINNHUB_API_KEY",
    "FMP_API_KEY",
    "FRED_API_KEY",
    "BLS_API_KEY",
    "BEA_API_KEY",
    "CENSUS_API_KEY",
    "WISBURG_API_KEY",
    "POLYGON_API_KEY",
    "TIINGO_API_KEY",
    "EODHD_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "QWEN_API_KEY",
    "KIMI_API_KEY",
    "DEEPSEEK_API_KEY",
)


def _configured_secret_values() -> list[str]:
    values: list[str] = []
    for name in _SECRET_CONFIG_NAMES:
        value = str(getattr(config, name, "") or "").strip()
        if len(value) >= 8:
            values.append(value)
    return values


def _sanitize_prompt_pack(value: Any, secrets: tuple[str, ...] | None = None) -> Any:
    if secrets is None:
        secrets = tuple(_configured_secret_values())
    if isinstance(value, dict):
        return {
            _sanitize_prompt_string(str(key), secrets): _sanitize_prompt_pack(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_prompt_pack(item, secrets) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_prompt_pack(item, secrets) for item in value]
    if isinstance(value, str):
        return _sanitize_prompt_string(value, secrets)
    return value


def _sanitize_prompt_string(text: str, secrets: tuple[str, ...] | None = None) -> str:
    clean = text
    for secret in secrets if secrets is not None else _configured_secret_values():
        clean = clean.replace(secret, "[redacted]")
    clean = _URL_RE.sub(lambda match: _sanitize_url_match(match.group(0)), clean)
    clean = _BEARER_TOKEN_RE.sub("Bearer [redacted]", clean)
    clean = _SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}=[redacted]", clean)
    clean = _SK_TOKEN_RE.sub("[redacted]", clean)
    return clean


def _sanitize_url_match(raw_url: str) -> str:
    suffix = ""
    url = raw_url
    while url and url[-1] in ".,);]":
        suffix = url[-1] + suffix
        url = url[:-1]
    return _sanitize_url(url) + suffix


def _sanitize_url(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.query:
        return url
    safe_query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.strip().lower() in _SECRET_QUERY_KEYS:
            safe_query.append((key, "[redacted]"))
        else:
            safe_query.append((key, value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(safe_query), parts.fragment))


def _is_redacted_value(value: str) -> bool:
    normalized = unquote_plus(str(value or "")).strip().strip("'\"").lower()
    return normalized in {"", "[redacted]", "redacted", "[redacted-secret]"}


def _prompt_secret_findings(prompt_pack: dict) -> list[str]:
    serialized = json.dumps(prompt_pack, sort_keys=True, ensure_ascii=True, default=str)
    findings: list[str] = []
    for secret in _configured_secret_values():
        if secret and secret in serialized:
            findings.append("configured_secret_value")
            break
    if _SK_TOKEN_RE.search(serialized):
        findings.append("sk_style_token")
    if _BEARER_TOKEN_RE.search(serialized):
        findings.append("bearer_token")
    for match in _SECRET_ASSIGNMENT_RE.finditer(serialized):
        if not _is_redacted_value(match.group(2)):
            findings.append(f"{match.group(1).lower()}_value")
            break
    return _dedupe(findings)


def _prompt_audit(prompt_pack: dict) -> dict:
    serialized = json.dumps(prompt_pack, sort_keys=True, ensure_ascii=True, default=str)
    counts = {
        "ideas": len(prompt_pack.get("ideas") or []),
        "evidence": len(prompt_pack.get("evidence") or {}),
        "citations": len(prompt_pack.get("citations") or {}),
        "external_evidence_items": len(prompt_pack.get("external_evidence_items") or []),
        "management_claims": len(prompt_pack.get("management_claims") or []),
        "thesis_clusters": len(prompt_pack.get("thesis_clusters") or []),
        "research_questions": len(prompt_pack.get("research_questions") or []),
        "research_scout_questions": len((prompt_pack.get("research_scout") or {}).get("questions") or []),
        "source_plan_requests": len((prompt_pack.get("source_plan") or {}).get("requests") or []),
        "event_workflow_items": len((prompt_pack.get("event_workflow") or {}).get("items") or []),
        "evidence_work_order_items": len((prompt_pack.get("evidence_work_order") or {}).get("items") or []),
        "wisburg_excerpts": len((prompt_pack.get("wisburg_lens") or {}).get("excerpts") or []),
    }
    return {
        "prompt_hash": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        "prompt_context_counts": counts,
        "guardrail_policy": [
            "curated_excerpts_only",
            "citation_ids_required",
            "citation_claim_support_overlap_required",
            "no_invented_targets_probabilities_or_citations",
            "weak_evidence_skips_primary_synthesis",
            "secondary_review_cannot_promote",
            "external_research_context_only",
            "evidence_work_order_items_are_gaps_not_proof",
            "bilingual_audit_preserves_original_excerpts",
        ],
    }


def _guardrail_checks(
    prompt_pack: dict,
    status: str,
    message: str,
    secondary_reviews: int = 0,
    failure_class: str = "",
) -> list[LlmGuardrailCheck]:
    counts = {
        "evidence": len(prompt_pack.get("evidence") or {}),
        "citations": len(prompt_pack.get("citations") or {}),
        "rules": len(prompt_pack.get("rules") or []),
        "ideas": len(prompt_pack.get("ideas") or []),
    }
    secret_findings = _prompt_secret_findings(prompt_pack)
    has_secret = bool(secret_findings)
    weak_skip = status == "Skipped" and "weak" in message.lower()
    accepted = status == "Available"
    provider_failure = status in {"Provider timeout", "Provider error"}
    disabled_or_skipped = status in {"Disabled", "Skipped", "Not requested"}
    disabled_without_prompt = status in {"Disabled", "Not requested"} and not counts["evidence"] and not counts["citations"]
    checks = [
        LlmGuardrailCheck(
            area="Curated evidence boundary",
            status="Not run" if disabled_without_prompt else "Passed" if counts["evidence"] and counts["citations"] and not has_secret else "Failed",
            score=100 if disabled_without_prompt or (counts["evidence"] and counts["citations"] and not has_secret) else 20,
            summary=(
                "LLM was disabled, so no evidence prompt or credentials were transmitted."
                if disabled_without_prompt else
                f"Prompt pack includes {counts['evidence']} curated evidence item(s) and "
                f"{counts['citations']} citation(s); raw secrets detected: {'yes' if has_secret else 'no'}."
            ),
            evidence=[
                f"evidence={counts['evidence']}",
                f"citations={counts['citations']}",
                f"secret_findings={len(secret_findings)}",
            ],
            gaps=[] if disabled_without_prompt or (counts["evidence"] and counts["citations"] and not has_secret) else [
                "Prompt pack must contain curated evidence/citations and no raw credentials."
            ],
            enforcement="LLM receives the prompt pack only after deterministic evidence extraction.",
        ),
        LlmGuardrailCheck(
            area="Citation-constrained output",
            status="Passed" if "citation_ids_required" in _prompt_audit(prompt_pack)["guardrail_policy"] else "Failed",
            score=100 if "citation_ids_required" in _prompt_audit(prompt_pack)["guardrail_policy"] else 30,
            summary="Every LLM evidence-chain claim must cite IDs from the prompt pack and overlap citation support text.",
            evidence=["citation_ids_required", "citation_claim_support_overlap_required"],
            gaps=[],
            enforcement="Parser rejects uncited or unsupported LLM claims and falls back to deterministic synthesis.",
        ),
        LlmGuardrailCheck(
            area="Weak-evidence skip",
            status="Passed" if weak_skip or accepted or disabled_or_skipped or provider_failure else "Partial",
            score=100 if weak_skip or accepted else 85 if provider_failure else 80 if disabled_or_skipped else 55,
            summary=(
                "LLM synthesis is skipped when deterministic evidence is weak."
                if weak_skip else
                "Provider failed before accepted model output; no weak evidence was polished."
                if provider_failure else
                "Run status does not indicate weak-evidence polishing."
            ),
            evidence=[f"status={status}", message],
            gaps=[] if weak_skip or accepted or disabled_or_skipped or provider_failure else [
                "Confirm weak evidence cannot be polished into an investment thesis."
            ],
            enforcement="Evidence sufficiency is checked before primary LLM synthesis runs.",
        ),
        LlmGuardrailCheck(
            area="Provider health boundary",
            status="Passed" if accepted or disabled_or_skipped else "Retryable" if status == "Provider timeout" else "Failed" if status == "Provider error" else "Passed",
            score=100 if accepted or disabled_or_skipped else 75 if status == "Provider timeout" else 55 if status == "Provider error" else 90,
            summary=(
                "LLM provider completed successfully or was intentionally not used."
                if accepted or disabled_or_skipped else
                f"LLM provider did not return usable output before guardrail parsing; failure class={failure_class or 'unknown'}."
            ),
            evidence=[f"status={status}", f"failure_class={failure_class or 'none'}"],
            gaps=[] if accepted or disabled_or_skipped else [
                "Use deterministic IC brief, retry with compressed evidence pack, or select a faster provider/model."
            ],
            enforcement="Transport/provider failures are reported separately from factual guardrail rejection.",
        ),
        LlmGuardrailCheck(
            area="No promotion authority",
            status="Passed",
            score=100,
            summary="LLM output cannot promote candidates, create calibrated probabilities, or override deterministic gates.",
            evidence=["promotion gates live outside thesis_synthesis", "secondary_review_cannot_promote"],
            gaps=[],
            enforcement="Promotion routes use stored deterministic gate audits, not LLM prose.",
        ),
        LlmGuardrailCheck(
            area="Secondary reader boundary",
            status="Passed" if secondary_reviews else "Not run",
            score=90 if secondary_reviews else 70,
            summary=(
                f"{secondary_reviews} secondary review(s) attached; secondary output is critique only."
                if secondary_reviews else
                "Secondary reader did not run for this thesis."
            ),
            evidence=[f"secondary_reviews={secondary_reviews}"],
            gaps=[] if secondary_reviews else ["Secondary critique is optional and may be limited to Research-Ready or High-Conviction ideas."],
            enforcement="Secondary review can flag disagreement but cannot create facts or promote an idea.",
        ),
    ]
    return checks


def _guardrail_score(checks: list[LlmGuardrailCheck]) -> int:
    if not checks:
        return 0
    return round(sum(item.score for item in checks) / len(checks))


def _manifest_with_guardrails(
    prompt_pack: dict,
    *,
    provider: str,
    model: str,
    status: str,
    redacted_config: dict[str, str] | None = None,
    message: str = "",
    secondary_reviews: int = 0,
    failure_class: str = "",
    retryable: bool = False,
    provider_health: str = "",
    timeout_seconds: int | None = None,
) -> LLMRunManifest:
    checks = _guardrail_checks(prompt_pack, status, message, secondary_reviews, failure_class)
    execution_status, guardrail_status = _llm_status_pair(status, failure_class)
    return LLMRunManifest(
        provider=provider,
        model=model,
        prompt_version=PROMPT_VERSION,
        generated_at=_utc_now(),
        status=status,
        llm_execution_status=execution_status,
        llm_guardrail_status=guardrail_status,
        evidence_ids=list(prompt_pack["evidence"].keys()),
        citation_ids=list(prompt_pack["citations"].keys()),
        token_estimate=_estimate_tokens(prompt_pack),
        redacted_config=redacted_config or {},
        message=message,
        guardrail_checks=checks,
        guardrail_score=_guardrail_score(checks),
        failure_class=failure_class,
        retryable=retryable,
        provider_health=provider_health,
        timeout_seconds=timeout_seconds,
        **_prompt_audit(prompt_pack),
    )


def _empty_manifest(source: str) -> LLMRunManifest:
    prompt_pack = {"evidence": {}, "citations": {}, "rules": []}
    checks = _guardrail_checks(prompt_pack, "Not requested", "LLM thesis synthesis was not requested.")
    return LLMRunManifest(
        provider=source,
        model="none",
        prompt_version=PROMPT_VERSION,
        generated_at=_utc_now(),
        status="Not requested",
        llm_execution_status="disabled",
        llm_guardrail_status="not_run",
        guardrail_policy=[
            "curated_excerpts_only",
            "citation_ids_required",
            "citation_claim_support_overlap_required",
            "no_invented_targets_probabilities_or_citations",
        ],
        guardrail_checks=checks,
        guardrail_score=_guardrail_score(checks),
    )


def _llm_status_pair(status: str, failure_class: str = "") -> tuple[str, str]:
    normalized = (status or "").strip().lower()
    failure = (failure_class or "").strip().lower()
    if normalized in {"available"}:
        return "available", "passed"
    if normalized in {"disabled", "not requested"}:
        return "disabled", "not_run"
    if normalized in {"skipped"}:
        return "skipped", "not_run"
    if normalized == "provider timeout" or failure == "timeout":
        return "timeout", "not_run"
    if normalized == "provider error" or failure in {"provider_error", "network_error", "invalid_key", "rate_limited"}:
        if failure in {"invalid_key", "rate_limited"}:
            return failure, "not_run"
        return "provider_error", "not_run"
    if normalized == "rejected" or failure == "guardrail_rejection":
        return "available", "rejected"
    return normalized.replace(" ", "_") or "unknown", "not_run"


def _classify_llm_exception(exc: Exception) -> dict[str, object]:
    text = str(exc).lower()
    timeout_tokens = ("timed out", "timeout", "read operation timed out", "operation timed out")
    if any(token in text for token in timeout_tokens):
        return {
            "status": "Provider timeout",
            "failure_class": "timeout",
            "retryable": True,
            "provider_health": "retryable_timeout",
            "message_prefix": "LLM provider timed out before synthesis was accepted",
        }
    provider_tokens = (
        "request failed",
        "connection",
        "http ",
        "api key",
        "not configured",
        "rate limit",
        "429",
        "401",
        "403",
        "provider returned",
        "no message content",
        "no text content",
    )
    if any(token in text for token in provider_tokens):
        return {
            "status": "Provider error",
            "failure_class": "provider_error",
            "retryable": any(token in text for token in ("rate limit", "429", "connection", "temporarily")),
            "provider_health": "provider_unavailable",
            "message_prefix": "LLM provider failed before synthesis was accepted",
        }
    return {
        "status": "Rejected",
        "failure_class": "guardrail_rejection",
        "retryable": False,
        "provider_health": "provider_completed",
        "message_prefix": "LLM synthesis was rejected by output guardrails",
    }


def _api_key_for_provider(provider_name: str) -> str:
    if provider_name == "anthropic":
        return config.ANTHROPIC_API_KEY
    if provider_name == "qwen":
        return config.QWEN_API_KEY
    if provider_name == "kimi":
        return config.KIMI_API_KEY
    if provider_name == "deepseek":
        return config.DEEPSEEK_API_KEY
    return config.OPENAI_API_KEY


def _base_url_for_provider(provider_name: str) -> str:
    if config.LLM_BASE_URL:
        return config.LLM_BASE_URL
    if provider_name == "anthropic":
        return "https://api.anthropic.com/v1"
    if provider_name in {"ollama", "local"}:
        return "http://localhost:11434/v1"
    if provider_name in {"qwen", "kimi"}:
        return ""
    if provider_name == "deepseek":
        return "https://api.deepseek.com"
    if provider_name == "custom_openai_compatible":
        return ""
    return "https://api.openai.com/v1"


def _model_for_provider(provider_name: str) -> str:
    if provider_name == "anthropic":
        return "claude-3-5-sonnet-latest"
    if provider_name in {"ollama", "local"}:
        return "llama3.1"
    if provider_name == "deepseek":
        return "deepseek-v4-pro"
    return config.LLM_MODEL or "gpt-4.1-mini"


def _post_json(url: str, payload: dict, headers: dict[str, str], timeout_seconds: int) -> dict:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"LLM provider HTTP {exc.code}: {_redact(body)}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"LLM provider request failed: {_redact(str(exc))}") from exc


def _redact(text: str) -> str:
    for secret in (
        config.OPENAI_API_KEY,
        config.ANTHROPIC_API_KEY,
        config.QWEN_API_KEY,
        config.KIMI_API_KEY,
        config.DEEPSEEK_API_KEY,
    ):
        if secret:
            text = text.replace(secret, "[redacted]")
    return text


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
