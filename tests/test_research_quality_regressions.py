from __future__ import annotations

import json
from urllib.error import URLError

import server
from equity_research.adr_profiles import adr_profile_for
from equity_research.driver_templates import TEMPLATES, template_for_event
from equity_research.idea_engine import build_driver_analysis
from equity_research.local_secrets import validate_provider_keys
from equity_research.models import ChangeEvent, Citation, FinancialMetric, NetworkProbeStatus
from equity_research.network_diagnostics import build_network_diagnostic_report
from equity_research.sample_data import demo_result
from equity_research.thesis_synthesis import build_prompt_pack, synthesize_ic_thesis


class FixtureProvider:
    provider_name = "fixture_llm"
    model = "fixture-model"

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def complete_json(self, prompt_pack: dict) -> dict:
        return self.payload


def test_golden_demo_outputs_have_story_sections_and_do_not_bypass_gates() -> None:
    for ticker in ["AAPL", "BABA", "TSLA", "GS", "NVDA", "JPM"]:
        result = demo_result(ticker)

        assert result.research_scout.status == "Available"
        assert result.research_scout.questions
        assert result.ic_one_pager.decision
        assert result.ic_one_pager.next_best_action
        assert result.ic_one_pager.causal_bridge
        assert result.ic_one_pager.market_capture
        assert "Research Scout" in result.memo_markdown
        assert "Market Capture Readiness" in result.memo_markdown
        assert "LLM guardrail" in result.memo_markdown
        assert "point-in-time" in result.market_capture_readiness.point_in_time_rule

        for idea in result.ideas:
            if idea.stage == "High-Conviction":
                assert idea.gate_result is not None
                assert idea.gate_result.research_ready
                assert idea.gate_result.high_conviction
            if idea.direction == "Watch":
                assert idea.stage in {"Candidate", "Watch"}


def test_driver_templates_cover_all_core_thesis_families_with_falsification_sources() -> None:
    expected = {"revenue", "margin", "opex", "share_count", "debt", "guidance", "regulation", "management"}
    assert expected <= set(TEMPLATES)
    for key in expected:
        template = TEMPLATES[key]
        assert template.why_it_matters
        assert template.confirm_evidence
        assert template.falsify_evidence
        assert template.next_source

    event_cases = {
        "revenue": ChangeEvent("financial_kpi", "Revenue changed", "", 3, "positive", "2026-01-01", "SEC", metrics={"metric_name": "Revenue"}),
        "margin": ChangeEvent("financial_kpi", "Gross Profit changed", "", 3, "positive", "2026-01-01", "SEC", metrics={"metric_name": "Gross Profit"}),
        "share_count": ChangeEvent("financial_kpi", "Shares changed", "", 3, "neutral", "2026-01-01", "SEC", metrics={"metric_name": "Shares"}),
        "debt": ChangeEvent("financial_kpi", "Debt changed", "", 3, "negative", "2026-01-01", "SEC", metrics={"metric_name": "Long-term Debt"}),
        "guidance": ChangeEvent("guidance", "Guidance language changed", "", 3, "positive", "2026-01-01", "SEC"),
        "regulation": ChangeEvent("risk", "Regulation risk escalated", "", 3, "negative", "2026-01-01", "SEC"),
        "management": ChangeEvent("management", "Management credibility changed", "", 3, "neutral", "2026-01-01", "Transcript"),
    }
    for expected_key, event in event_cases.items():
        assert template_for_event(event).driver_key == expected_key


def test_gross_margin_peer_readthrough_requires_operating_metrics_not_below_line_items() -> None:
    event = ChangeEvent(
        "financial_kpi",
        "Gross Profit changed +49.7%",
        "Gross profit increased.",
        4,
        "positive",
        "2026-06-30",
        "SEC Companyfacts",
        metrics={"metric_name": "Gross Profit", "yoy_change_pct": 49.7},
    )
    metrics = [
        _metric("Revenue", 120, 100, 20),
        _metric("Gross Profit", 48, 32, 50),
        _metric("Interest Expense", 30, 10, 200),
        _metric("Income Tax Expense", 38, 20, 90),
        _metric("Shares", 120, 100, 20),
    ]

    analysis = build_driver_analysis(event, metrics)

    assert analysis.primary_driver == "Gross margin / mix"
    assert "Peer gross margin %" in analysis.peer_metric_checks
    assert "Peer COGS/revenue" in analysis.peer_metric_checks
    assert not any("Interest" in item or "tax" in item.lower() for item in analysis.peer_metric_checks)
    assert not any("Higher financing cost" == factor.cause for factor in analysis.factors)
    assert not any("Higher tax expense" == factor.cause for factor in analysis.factors)


def test_llm_invented_target_or_probability_is_rejected_even_with_valid_citation() -> None:
    result = demo_result("AAPL")
    result.ideas[0].stage = "Research-Ready"
    if result.ideas[0].gate_result:
        result.ideas[0].gate_result.research_ready_failed = []
    prompt = build_prompt_pack(
        result.identity,
        result.ideas,
        result.evidence_ledger,
        result.valuation,
        result.data_quality,
        result.management_credibility,
        result.expectations_bridge,
        result.management_sources,
        result.external_evidence,
        result.calibration,
    )
    citation_id = _citation_id_containing(prompt, "margin")

    synthesized = synthesize_ic_thesis(
        result.identity,
        result.ideas,
        result.evidence_ledger,
        result.valuation,
        result.data_quality,
        result.management_credibility,
        result.expectations_bridge,
        result.management_sources,
        result.external_evidence,
        result.calibration,
        FixtureProvider({
            "verdict": "Buy with 80% probability",
            "thesis": "Margin improved and the stock should reach a $300 price target.",
            "variant_perception": "The market underestimates margin expansion.",
            "evidence_chain": [{"claim": "Margin improved.", "citation_ids": [citation_id]}],
        }),
    )

    assert synthesized.thesis_brief.source == "deterministic"
    assert synthesized.llm_manifest.status == "Rejected"
    assert synthesized.llm_manifest.llm_execution_status == "available"
    assert synthesized.llm_manifest.llm_guardrail_status == "rejected"


def test_seeded_large_adr_profiles_include_china_depth_and_global_context() -> None:
    china = ["BABA", "JD", "PDD", "BIDU", "NTES", "TCOM", "NIO", "XPEV", "LI", "YUMC"]
    global_names = ["TSM", "ASML", "NVO", "INFY", "HDB", "TM", "SONY", "PBR", "VALE"]
    for ticker in china:
        profile = adr_profile_for(ticker)
        assert profile is not None, ticker
        assert profile.segment_drivers, ticker
        assert profile.issuer_ir_sources, ticker
        assert "issuer_ir" in profile.source_priority, ticker
        assert any(item in profile.benchmark_tickers for item in ("KWEB", "MCHI", "HSTECH", "CNH")), ticker
    for ticker in global_names:
        profile = adr_profile_for(ticker)
        assert profile is not None, ticker
        assert profile.segment_drivers, ticker
        assert profile.issuer_ir_sources, ticker


def test_provider_validation_redacts_raw_keys_from_network_errors() -> None:
    secret = "sk-test-secret-should-not-leak"

    def failing_fetcher(url: str, timeout_seconds: int):
        raise URLError(f"request failed for {url} using {secret}")

    results = validate_provider_keys({"FRED_API_KEY": secret}, fetch_json=failing_fetcher)

    assert len(results) == 1
    assert results[0].status == "network_error"
    assert secret not in results[0].message
    assert "[redacted]" in results[0].message


def test_network_diagnostics_classifies_general_outbound_block_without_provider_entitlement_confusion() -> None:
    probes = [
        NetworkProbeStatus(
            provider,
            host,
            "tcp_443",
            "failed",
            "connection_refused",
            "Connection refused.",
            True,
        )
        for provider, host in [
            ("Alpha Vantage", "www.alphavantage.co"),
            ("Finnhub", "finnhub.io"),
            ("SEC EDGAR", "www.sec.gov"),
            ("FRED", "api.stlouisfed.org"),
            ("Neutral HTTPS", "example.com"),
        ]
    ]

    report = build_network_diagnostic_report(probes, proxy_state={}, observed_at="2026-07-12T00:00:00+00:00")

    assert report.network_class == "general_outbound_block"
    assert any("network" in action.lower() or "vpn" in action.lower() for action in report.suggested_actions)


def test_demo_payload_is_json_serializable_and_exposes_required_research_panels() -> None:
    payload = server._jsonable(demo_result("BABA"))
    encoded = json.dumps(payload)

    assert not encoded.lstrip().startswith("<")
    for field in [
        "research_scout",
        "market_capture_readiness",
        "llm_run_manifest",
        "ic_one_pager",
        "thesis_clusters",
        "peer_metric_readthrough",
        "profiling",
    ]:
        assert field in payload
    assert "llm_execution_status" in payload["llm_run_manifest"]
    assert "llm_guardrail_status" in payload["llm_run_manifest"]
    assert "consensus_advisor" in payload["market_capture_readiness"]
    assert "autofill_plan" in payload["market_capture_readiness"]


def _metric(name: str, value: float, previous: float, yoy: float) -> FinancialMetric:
    return FinancialMetric(
        name,
        value,
        "USD",
        "2026-06-30",
        previous_value=previous,
        yoy_change_pct=yoy,
    )


def _citation_id_containing(prompt: dict, token: str) -> str:
    needle = token.lower()
    for citation_id, payload in prompt["citations"].items():
        haystack = " ".join(
            str(payload.get(key) or "")
            for key in ("snippet", "original_excerpt", "section", "source")
        ).lower()
        if needle in haystack:
            return citation_id
    return next(iter(prompt["citations"]))
