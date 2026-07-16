from __future__ import annotations

import tempfile
from dataclasses import asdict
from pathlib import Path

from equity_research.causal_thesis_graph import build_causal_thesis_graphs
from equity_research.external_evidence import WisburgEvidenceProvider
from equity_research.models import (
    ClaimValidationResult,
    CompanyIdentity,
    FinancialMetric,
    ManagementSourcePackage,
    ResearchSourcePlan,
    WisburgResearchLens,
    WisburgStructuredClaim,
)
from equity_research.research_store import ResearchStore
from equity_research.sample_data import demo_result
from equity_research.thesis_synthesis import build_prompt_pack
from equity_research.wisburg_intelligence import corroborate_wisburg_lens
from equity_research.wisburg_lens import (
    build_wisburg_lens,
    enrich_source_plan_with_wisburg,
)
from equity_research.wisburg_monitor import compare_wisburg_lenses, generate_wisburg_alerts


class DetailWisburgClient:
    def list_tools(self) -> list[dict]:
        return [
            {"name": "list-company-reports"},
            {"name": "get-report-detail"},
        ]

    def list_tool(self, tool_name: str, query: str, first: int) -> dict:
        return {
            "content": [{
                "type": "text",
                "text": (
                    "Found 1 report:\n\n"
                    "[901] Fictional Securities: ExampleCo cloud outlook\n"
                    "  date: 2026-07-10T08:00:00+00:00\n"
                    "  Cloud revenue and valuation assumptions changed."
                ),
            }]
        }

    def get_report_detail(self, report_id: str, category: str) -> dict:
        assert report_id == "901"
        assert category == "company"
        return {
            "content": [{
                "type": "text",
                "text": (
                    "## Summary\n"
                    "ExampleCo FY2027 cloud revenue forecast was raised to CNY 48.4 billion "
                    "from CNY 40.0 billion as demand improved.\n"
                    "Target price was cut from USD 208 to USD 192 because the valuation multiple contracted.\n"
                    "## Appendix\n"
                    + ("FULL_PAYLOAD_SENTINEL " * 250)
                ),
            }]
        }


def _identity() -> CompanyIdentity:
    return CompanyIdentity("EXM", "0000000001", "ExampleCo")


def _lens_from_detail_provider() -> WisburgResearchLens:
    package = WisburgEvidenceProvider(
        api_key="fixture-key",
        enabled=True,
        client=DetailWisburgClient(),
        max_items=4,
        max_detail_items=2,
    ).fetch(_identity(), [])
    return build_wisburg_lens(_identity(), package)


def test_entitlement_audit_and_structured_revision_extraction_are_bounded() -> None:
    provider = WisburgEvidenceProvider(
        api_key="fixture-key",
        enabled=True,
        client=DetailWisburgClient(),
        max_items=4,
        max_detail_items=2,
    )
    package = provider.fetch(_identity(), [])
    lens = build_wisburg_lens(_identity(), package)

    assert lens.coverage_audit is not None
    assert lens.coverage_audit.tool_discovery_status == "confirmed"
    assert lens.coverage_audit.detailed_items == 1
    assert lens.reports[0].detail_status == "structured_extract_available"
    assert len(lens.reports[0].capped_excerpt) <= 1800
    assert "FULL_PAYLOAD_SENTINEL" not in str(asdict(lens))

    target = next(item for item in lens.revisions if item.metric == "Target Price")
    cloud = next(item for item in lens.revisions if item.metric == "Cloud Revenue")
    assert (target.previous_value, target.current_value) == (208.0, 192.0)
    assert (cloud.previous_value, cloud.current_value) == (40.0, 48.4)
    assert cloud.fiscal_period == "FY2027"
    assert all(item.eligibility == "external_non_consensus" for item in lens.revisions)


def test_primary_corroboration_creates_executable_registered_source_work_order() -> None:
    lens = _lens_from_detail_provider()
    metrics = [
        FinancialMetric(
            "Cloud Revenue", 47.9, "CNY bn", "2026-06-30",
            filed="2026-07-08", source_url="https://issuer.example/results",
        )
    ]
    corroborate_wisburg_lens(
        lens,
        metrics,
        ClaimValidationResult("EXM", "Available"),
        ManagementSourcePackage("EXM", "Unavailable"),
    )

    cloud = next(item for item in lens.structured_claims if item.metric == "Cloud Revenue")
    target = next(item for item in lens.structured_claims if item.metric == "Target Price")
    assert cloud.corroboration_status == "Underlying driver corroborated; forecast unverified"
    assert cloud.primary_evidence_ids
    assert target.corroboration_status == "External opinion; primary check not applicable"
    assert lens.research_tasks

    plan = enrich_source_plan_with_wisburg(
        ResearchSourcePlan("EXM", "Available", "2026-07-15", "fixture"),
        lens,
    )
    task_requests = [item for item in plan.requests if item.provider == "Wisburg research intelligence"]
    assert task_requests
    assert all(item.source_type in {"presentation", "sec_filing", "consensus_manual", "regulator_release"} for item in task_requests)


def test_wisburg_claims_are_citation_bound_in_llm_pack_but_do_not_raise_causal_score() -> None:
    result = demo_result("AAPL")
    idea = result.ideas[0]
    driver = (
        idea.driver_analysis.primary_driver
        if idea.driver_analysis and idea.driver_analysis.primary_driver
        else idea.thesis_cluster_label or "Cash generation"
    )
    citation = result.ideas[0].citations[0]
    claim = WisburgStructuredClaim(
        claim_id="wisburg-claim-1",
        report_key="company:901",
        ticker="AAPL",
        claim_type="estimate",
        statement=f"External analysts expect improvement in {driver}.",
        driver=driver,
        direction="positive",
        source_as_of="2026-07-10",
        source_tier=3,
        citation=citation,
        corroboration_status="Primary context corroborated",
    )
    lens = WisburgResearchLens(
        "AAPL", "Available", "2026-07-15T00:00:00+00:00",
        structured_claims=[claim],
    )
    prompt = build_prompt_pack(
        result.identity, result.ideas, result.evidence_ledger, result.valuation,
        result.data_quality, result.management_credibility, result.expectations_bridge,
        result.management_sources, result.external_evidence, result.calibration,
        wisburg_lens=lens,
    )
    row = prompt["evidence"]["wisburg:wisburg-claim-1"]
    assert row["allowed_stage"] == "Candidate"
    assert row["citation_id"] in prompt["citations"]
    assert any("never official consensus" in rule for rule in prompt["rules"])

    baseline = build_causal_thesis_graphs(
        "AAPL", [idea], result.validated_claims, result.company_model,
        result.valuation, result.market_implied_expectations, result.evidence_closure,
    )[0]
    contextual = build_causal_thesis_graphs(
        "AAPL", [idea], result.validated_claims, result.company_model,
        result.valuation, result.market_implied_expectations, result.evidence_closure,
        wisburg_lens=lens,
    )[0]
    assert contextual.edges[0].score == baseline.edges[0].score
    assert any("External opinion" in item for item in contextual.edges[0].evidence)


def test_daily_monitor_and_normalized_store_track_external_revisions_without_consensus_claim() -> None:
    current = _lens_from_detail_provider()
    corroborate_wisburg_lens(
        current,
        [],
        ClaimValidationResult("EXM", "Available"),
        ManagementSourcePackage("EXM", "Unavailable"),
    )
    prior = asdict(current)
    prior["observed_at"] = "2026-07-14T00:00:00+00:00"
    prior["revisions"] = []
    prior["corroboration"] = [
        {**item, "status": "Primary context corroborated"}
        for item in prior["corroboration"]
    ]
    delta = compare_wisburg_lenses(current, prior)

    assert delta.status == "Changed"
    assert delta.new_revision_ids
    assert delta.corroboration_changes

    with tempfile.TemporaryDirectory() as temporary:
        store = ResearchStore(Path(temporary) / "research.db")
        store.save_wisburg_lens(current)
        alerts = generate_wisburg_alerts(delta, store)
        assert store.latest_wisburg_coverage("EXM")["authentication_status"] == "authenticated"
        assert store.list_wisburg_reports("EXM")
        assert store.list_wisburg_claims("EXM")
        assert store.list_wisburg_revisions("EXM")
        assert store.list_wisburg_research_tasks("EXM")
        serialized = str({
            "reports": store.list_wisburg_reports("EXM"),
            "claims": store.list_wisburg_claims("EXM"),
            "revisions": store.list_wisburg_revisions("EXM"),
        })

    revision_alerts = [alert for alert in alerts if alert.alert_type == "wisburg_external_revision"]
    assert revision_alerts
    assert all("not official" in alert.message.lower() or "not an official" in alert.message.lower() for alert in revision_alerts)
    assert "fixture-key" not in serialized
    assert "FULL_PAYLOAD_SENTINEL" not in serialized
