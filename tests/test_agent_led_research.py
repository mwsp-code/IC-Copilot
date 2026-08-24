from __future__ import annotations

from dataclasses import asdict

from equity_research.application.agent_runtime import (
    ResearchAgentRuntime,
    _context_evidence_ids,
    budget_for_research,
    session_from_payload,
)
from equity_research.application.research_agenda import (
    ResearchAgendaBuilder,
    ResearchQuestionLibrary,
)
from equity_research.application.research_tools import (
    ContextResearchToolGateway,
)
from equity_research.application.source_capabilities import (
    SourceCapabilityRegistry,
    build_source_capability_registry,
)
from equity_research.domain.agent_research import (
    ResearchQuestionTemplate,
    ResearchToolCall,
    SourceCapability,
)
from equity_research.models import (
    FinancialMetric,
    LlmProviderProfile,
    NormalizedSourceRecord,
    SourceRetrievalLifecycle,
)
from equity_research.infrastructure.provider_registry import (
    ProviderResolutionRequest,
    resolve_research_providers,
)
from equity_research.sample_data import demo_result


class _Retriever:
    def __init__(self, metrics=()):
        self.metrics = list(metrics)
        self.records = []
        self.registered_source_catalog = []
        self.sec_client = object()
        self.calls = []

    def retrieve(
        self,
        *,
        request_id,
        source_type,
        query,
        period="",
        result_limit=12,
    ):
        self.calls.append((source_type, query))
        if source_type == "sec_filing":
            return [], SourceRetrievalLifecycle(
                request_id=request_id,
                source_id="",
                source_type=source_type,
                status="unavailable",
                failure_code="document_not_found",
                history=["planned", "unavailable"],
                message="The first filing query did not match.",
            )
        record = NormalizedSourceRecord(
            source_id="evidence-aapl-cash-flow",
            ticker="AAPL",
            entity_name="Apple Inc.",
            security_id="AAPL",
            source_family="primary_company",
            source_type="issuer_ir_report",
            document_id="aapl-fy-results",
            source_url="https://example.com/aapl-results",
            publication_date="2026-07-30",
            reporting_period="2026-06-27",
            section="Cash flow",
            page_or_table="Consolidated Statements of Cash Flows",
            exact_excerpt=(
                "Operating cash flow remained above net income after working "
                "capital movements and capital expenditure."
            ),
            metric_or_claim="cash conversion",
            value=None,
            unit="USD",
            currency="USD",
            source_tier=1,
            origin_group="apple.com",
            licensing_policy="public_issuer_document",
            retrieval_status="fetched_live",
            parser_status="parsed",
            validation_status="validated",
            citation_id="citation-aapl-cash-flow",
            content_hash="content-aapl-cash-flow",
        )
        return [record], SourceRetrievalLifecycle(
            request_id=request_id,
            source_id=record.source_id,
            source_type=source_type,
            status="validated",
            history=["planned", "fetched", "parsed", "validated"],
            message="One issuer record was retrieved and validated.",
        )


class _IterativeProvider:
    provider_name = "deepseek"
    model = "deepseek-v4-pro"
    last_usage = {"input_tokens": 10, "output_tokens": 5}
    last_cost_usd = 0.01

    def __init__(self):
        self.prompts = []

    def complete_json(self, prompt):
        self.prompts.append(prompt)
        active = list(prompt["active_questions"])
        if len(self.prompts) == 1:
            return _turn(actions=[{
                "tool_name": "search_sources",
                "question_id": active[0],
                "arguments": {
                    "query": "AAPL recurring cash conversion working capital capex",
                    "source_families": ["sec_filing"],
                },
                "objective": "Find an exact filing disclosure.",
                "reason": "Begin with the primary filing.",
            }])
        if len(self.prompts) == 2:
            return _turn(actions=[{
                "tool_name": "search_sources",
                "question_id": active[0],
                "arguments": {
                    "query": "Apple cash-flow statement recurring conversion",
                    "source_families": ["issuer_ir_report"],
                    "confirming_evidence": ["working capital and capex bridge"],
                    "contradicting_evidence": ["temporary working-capital release"],
                },
                "objective": "Replan through an issuer report.",
                "reason": "The first registered source route returned no match.",
            }])
        evidence_ids = [
            evidence_id
            for observation in prompt["recent_observations"]
            for evidence_id in observation.get("evidence_ids", [])
        ]
        return _turn(
            completed=True,
            stopping_reason="direct answers supported by admitted issuer evidence",
            updates=[{
                "question_id": item["question_id"],
                "status": "answered",
                "answer": "Cash conversion is supported by the admitted issuer cash-flow disclosure.",
                "confidence": "Medium",
                "confidence_reason": "The primary disclosure is admitted; additional periods would improve recurrence confidence.",
                "mechanism": "Operating cash flow remains above earnings after investment.",
                "evidence_ids": evidence_ids,
                "contrary_evidence_ids": [],
                "key_findings": ["Issuer cash-flow evidence was admitted."],
                "strongest_limitation": "Only the selected period was independently opened.",
                "remaining_work": [],
                "changed_conclusions": [],
            } for item in prompt["agenda"]],
        )


def _turn(*, actions=(), updates=(), completed=False, stopping_reason=""):
    return {
        "session_summary": "Research turn complete.",
        "completed": completed,
        "stopping_reason": stopping_reason,
        "next_actions": list(actions),
        "question_updates": list(updates),
        "notebook_entries": [],
    }


def _context():
    return {
        "ticker": "AAPL",
        "company": "Apple Inc.",
        "security_key": "cik:0000320193",
        "searchable_evidence_catalog": [],
        "normalized_sources": [],
        "claim_eligible_evidence_ids": [],
        "allowed_source_types": ["sec_filing", "issuer_ir_report"],
        "source_registry_version": "test",
    }


def _capabilities():
    return SourceCapabilityRegistry([
        SourceCapability(
            capability_id="sec",
            label="SEC filings",
            source_families=("sec_filing",),
            operations=("search", "retrieve"),
            status="ready",
        ),
        SourceCapability(
            capability_id="issuer",
            label="Issuer reports",
            source_families=("issuer_ir_report",),
            operations=("search", "retrieve"),
            status="ready",
        ),
    ])


def test_comprehensive_question_library_preserves_accumulated_agenda():
    keys = {item.question_key for item in ResearchQuestionLibrary().all()}
    assert {
        "cash_generation_capital_allocation",
        "reverse_valuation_expectations",
        "market_priced_in",
        "historical_analogs_playbooks",
    } <= keys


def test_focused_cash_question_loads_material_dependencies():
    agenda = ResearchAgendaBuilder().build(
        ticker="AAPL",
        company_name="Apple Inc.",
        research_profile="deep_initiation",
        selected_objective=(
            "Test whether AAPL cash conversion remains recurring after working "
            "capital, capex, taxes, financing, and capital returns."
        ),
    )
    keys = {item.question_key for item in agenda.items}
    assert "cash_generation_capital_allocation" in keys
    assert "financial_statement_integrity" in keys
    assert agenda.selected_question_id


def test_existing_cash_wording_reuses_the_library_research_intent():
    agenda = ResearchAgendaBuilder().build(
        ticker="AAPL",
        company_name="Apple Inc.",
        research_profile="deep_initiation",
        selected_objective="Test whether AAPL cash conversion is recurring.",
        existing_questions=[{
            "question": (
                "Test whether AAPL cash conversion remains recurring after "
                "working capital, capex, taxes, financing, and capital returns."
            )
        }],
    )
    cash_items = [
        item for item in agenda.items
        if item.question_key == "cash_generation_capital_allocation"
    ]
    assert len(cash_items) == 1
    assert cash_items[0].intent_id


def test_question_templates_honor_security_archetypes():
    library = ResearchQuestionLibrary(templates=(
        ResearchQuestionTemplate(
            question_key="bank_capital",
            domain="Bank capital",
            title="Is bank capital resilient?",
            objective="Test CET1 and credit losses.",
            decision_relevance="Defines bank downside resilience.",
            applicable_archetypes=("bank",),
        ),
        ResearchQuestionTemplate(
            question_key="operating_margin",
            domain="Operating margin",
            title="Are margins durable?",
            objective="Test operating margins.",
            decision_relevance="Defines operating leverage.",
            applicable_archetypes=("operating_company",),
        ),
    ))
    agenda = ResearchAgendaBuilder(library).build(
        ticker="JPM",
        company_name="JPMorgan Chase",
        research_profile="adaptive_ic",
        security_archetype="bank",
    )
    assert {item.question_key for item in agenda.items} == {"bank_capital"}


def test_agent_replans_after_first_connector_failure_and_answers():
    context = _context()
    retriever = _Retriever()
    gateway = ContextResearchToolGateway(
        context,
        _capabilities(),
        retrieval_engine=retriever,
    )
    agenda = ResearchAgendaBuilder().build(
        ticker="AAPL",
        company_name="Apple Inc.",
        research_profile="deep_initiation",
        selected_objective="Test whether AAPL cash conversion is recurring.",
    )
    provider = _IterativeProvider()
    session = ResearchAgentRuntime(
        gateway,
        budget=budget_for_research(depth="answer_oriented", focused=True),
    ).run(
        provider=provider,
        agenda=agenda,
        role="primary_research_agent",
    )

    assert [item[0] for item in retriever.calls[:2]] == [
        "sec_filing", "issuer_ir_report",
    ]
    assert session.status == "completed"
    assert session.answers
    assert all(item.evidence_ids for item in session.answers)
    assert "evidence-aapl-cash-flow" in context["claim_eligible_evidence_ids"]
    assert session_from_payload(asdict(session)).answers[0].status == "answered"


def test_cash_conversion_tool_builds_multi_period_reproducible_bridge():
    metrics = []
    for period, net_income, ocf, capex in (
        ("2025-09-27", 100.0, 120.0, 15.0),
        ("2026-09-26", 110.0, 130.0, 18.0),
    ):
        metrics.extend([
            FinancialMetric("Net Income", net_income, "USD", period, duration_days=365),
            FinancialMetric("Operating Cash Flow", ocf, "USD", period, duration_days=365),
            FinancialMetric("Capital Expenditure", capex, "USD", period, duration_days=365),
        ])
    context = _context()
    for metric in metrics:
        evidence_id = f"evidence-{metric.name}-{metric.period_end}"
        context["searchable_evidence_catalog"].append({
            "evidence_id": evidence_id,
            "citation_id": f"citation-{evidence_id}",
            "source_type": "sec_filing",
            "source_family": "primary_company",
            "source_tier": 1,
            "period": metric.period_end,
            "metric": metric.name,
            "value": metric.value,
            "unit": metric.unit,
            "summary": f"{metric.name}: {metric.value}",
            "exact_excerpt": f"{metric.name}: {metric.value}",
            "retrieval_status": "fetched_live",
            "parser_status": "parsed",
            "validation_status": "validated",
            "licensing_policy": "public_filing_excerpt",
        })
        context["claim_eligible_evidence_ids"].append(evidence_id)
    gateway = ContextResearchToolGateway(
        context,
        _capabilities(),
        retrieval_engine=_Retriever(metrics),
    )
    observation = gateway.execute(ResearchToolCall(
        call_id="bridge",
        tool_name="compute_financial_bridge",
        question_id="cash",
        arguments={"bridge_kind": "cash_conversion"},
    ))
    assert observation.status == "completed"
    assert len(observation.content) == 2
    assert observation.content[0]["free_cash_flow"] == 112.0
    assert observation.content[1]["free_cash_flow"] == 105.0
    assert observation.decision_eligible is True


def test_capability_prompt_never_contains_local_credential(monkeypatch):
    from equity_research import config

    secret = "never-send-this-provider-secret"
    monkeypatch.setattr(config, "FMP_API_KEY", secret)
    registry = build_source_capability_registry(
        _context(),
        retrieval_engine=_Retriever(),
        local_codex_ready=True,
    )
    prompt = str(registry.prompt_catalog())
    assert secret not in prompt
    assert "credential_reference" not in prompt
    assert any(
        item["capability_id"] == "fmp" and item["status"] == "ready"
        for item in registry.prompt_catalog()
    )

    class FailingRetriever(_Retriever):
        def retrieve(self, **_kwargs):
            raise RuntimeError(
                f"https://provider.example/data?apikey={secret}&symbol=AAPL"
            )

    observation = ContextResearchToolGateway(
        _context(),
        _capabilities(),
        retrieval_engine=FailingRetriever(),
    ).execute(ResearchToolCall(
        call_id="redaction-check",
        tool_name="search_sources",
        question_id="cash",
        arguments={
            "query": "AAPL cash conversion",
            "source_families": ["sec_filing"],
        },
    ))
    assert secret not in str(observation)
    assert "apikey=[redacted]" in observation.summary


def test_primary_agent_provider_is_resolved_when_independent_qa_is_off():
    class Secrets:
        def get(self, _reference):
            return "local-secret-never-sent-to-model"

    profile = LlmProviderProfile(
        profile_id="deepseek-primary",
        display_name="DeepSeek primary",
        provider_preset="deepseek",
        model="deepseek-v4-pro",
        base_url="https://api.deepseek.com",
        key_configured=True,
        secret_ref="profile-secret",
    )
    resolved = resolve_research_providers(ProviderResolutionRequest(
        ticker="AAPL",
        research_profile="deep_initiation",
        primary_profile_id=profile.profile_id,
        primary_profile=profile,
        secondary_profile=None,
        secrets_manager=Secrets(),
        enable_llm=True,
        enable_secondary_review=False,
        enable_independent_qa=False,
        auditor_backend_id="codex_local",
        configured_secondary_available=False,
        local_codex_status="unavailable",
    ))
    assert resolved.primary_research is not None
    assert resolved.auditor is None


def test_cash_bridge_keeps_same_end_date_durations_separate():
    metrics = []
    for duration, net_income, ocf, capex in (
        (90, 25.0, 31.0, 4.0),
        (365, 100.0, 125.0, 16.0),
    ):
        metrics.extend([
            FinancialMetric("Net Income", net_income, "USD", "2026-09-26", duration_days=duration),
            FinancialMetric("Operating Cash Flow", ocf, "USD", "2026-09-26", duration_days=duration),
            FinancialMetric("Capital Expenditure", capex, "USD", "2026-09-26", duration_days=duration),
        ])
    context = _context()
    for metric in metrics:
        evidence_id = f"evidence-{metric.name}-{metric.duration_days}"
        context["searchable_evidence_catalog"].append({
            "evidence_id": evidence_id,
            "citation_id": f"citation-{evidence_id}",
            "source_type": "sec_filing",
            "source_family": "primary_company",
            "source_tier": 1,
            "period": metric.period_end,
            "duration_days": metric.duration_days,
            "metric": metric.name,
            "value": metric.value,
            "unit": metric.unit,
            "summary": f"{metric.name}: {metric.value}",
            "exact_excerpt": f"{metric.name}: {metric.value}",
            "retrieval_status": "fetched_live",
            "parser_status": "parsed",
            "validation_status": "validated",
            "licensing_policy": "public_filing_excerpt",
        })
        context["claim_eligible_evidence_ids"].append(evidence_id)
    observation = ContextResearchToolGateway(
        context,
        _capabilities(),
        retrieval_engine=_Retriever(metrics),
    ).execute(ResearchToolCall(
        call_id="duration-bridge",
        tool_name="compute_financial_bridge",
        question_id="cash",
        arguments={"bridge_kind": "cash_conversion"},
    ))
    assert {(item["duration_days"], item["free_cash_flow"]) for item in observation.content} == {
        (90, 27.0),
        (365, 109.0),
    }


def test_compact_metadata_does_not_make_an_unadmitted_id_citable():
    identifiers = _context_evidence_ids({
        "evidence_metadata": [{
            "evidence_id": "quarantined-lead",
            "validation_status": "quarantined",
        }, {
            "evidence_id": "explicitly-eligible",
            "claim_eligible": True,
        }],
        "claim_eligible_evidence_ids": ["admitted-from-gateway"],
    })

    assert identifiers == {"explicitly-eligible", "admitted-from-gateway"}


def test_reverse_valuation_tool_recomputes_explicit_assumption_overrides():
    result = demo_result("AAPL")
    current = result.market_implied_expectations
    context = _context()
    context["decision_context"] = {
        "market_implied_expectations": asdict(current),
        "valuation": asdict(result.valuation),
    }
    context["_market_implied_inputs"] = {
        "identity": result.identity,
        "metrics": result.metrics,
        "current_price": current.current_price,
        "valuation": result.valuation,
        "company_model": result.company_model,
        "price_source": current.price_source,
        "price_as_of": current.price_as_of,
    }
    observation = ContextResearchToolGateway(
        context,
        _capabilities(),
        retrieval_engine=_Retriever(result.metrics),
    ).execute(ResearchToolCall(
        call_id="reverse-valuation",
        tool_name="compute_reverse_valuation",
        question_id="priced-in",
        arguments={"assumption_overrides": {"discount_rate_pct": 12.0}},
    ))

    payload = observation.content[0]
    assert observation.status == "completed"
    assumptions = {
        item["key"]: item["value"]
        for item in payload["market_implied_expectations"]["assumptions"]
    }
    assert assumptions["discount_rate_pct"] == 12.0
    assert "Recomputed" in payload["calculation_policy"]


def test_margin_and_liquidity_bridges_resolve_normalized_filing_inputs():
    metrics = [
        FinancialMetric("Revenue", 400.0, "USD", "2026-09-26", duration_days=365),
        FinancialMetric("Gross Profit", 180.0, "USD", "2026-09-26", duration_days=365),
        FinancialMetric("Operating Income", 120.0, "USD", "2026-09-26", duration_days=365),
        FinancialMetric("Cash", 50.0, "USD", "2026-09-26"),
        FinancialMetric("Current Assets", 140.0, "USD", "2026-09-26"),
        FinancialMetric("Current Liabilities", 100.0, "USD", "2026-09-26"),
        FinancialMetric("Current Debt", 20.0, "USD", "2026-09-26"),
        FinancialMetric("Long-term Debt", 80.0, "USD", "2026-09-26"),
    ]
    context = _context()
    for index, metric in enumerate(metrics):
        evidence_id = f"bridge-evidence-{index}"
        context["searchable_evidence_catalog"].append({
            "evidence_id": evidence_id,
            "citation_id": f"citation-{index}",
            "source_type": "sec_filing",
            "source_family": "primary_company",
            "source_tier": 1,
            "period": metric.period_end,
            "duration_days": metric.duration_days,
            "metric": metric.name,
            "value": metric.value,
            "unit": metric.unit,
            "summary": metric.name,
            "exact_excerpt": metric.name,
            "retrieval_status": "fetched_live",
            "parser_status": "parsed",
            "validation_status": "validated",
            "licensing_policy": "public_filing_excerpt",
        })
        context["claim_eligible_evidence_ids"].append(evidence_id)
    gateway = ContextResearchToolGateway(
        context,
        _capabilities(),
        retrieval_engine=_Retriever(metrics),
    )
    margin = gateway.execute(ResearchToolCall(
        call_id="margin",
        tool_name="compute_financial_bridge",
        question_id="drivers",
        arguments={"bridge_kind": "margin"},
    ))
    liquidity = gateway.execute(ResearchToolCall(
        call_id="liquidity",
        tool_name="compute_financial_bridge",
        question_id="credit",
        arguments={"bridge_kind": "liquidity"},
    ))

    assert margin.content[0]["gross_margin_pct"] == 45.0
    assert margin.content[0]["operating_margin_pct"] == 30.0
    assert liquidity.content[0]["net_debt"] == 50.0
    assert liquidity.content[0]["current_ratio"] == 1.4
    assert margin.decision_eligible and liquidity.decision_eligible
