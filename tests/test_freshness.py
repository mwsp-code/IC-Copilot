from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from equity_research.freshness import build_research_freshness


def _base_inputs() -> dict:
    return {
        "run_manifest": SimpleNamespace(
            generated_at="2026-07-30T20:00:00+00:00"
        ),
        "filings": [SimpleNamespace(
            form="10-Q",
            filing_date="2026-07-29",
            report_date="2026-06-30",
        )],
        "recent_market_context": SimpleNamespace(
            price_as_of="2026-07-30",
            source="Official price fixture",
        ),
        "consensus": SimpleNamespace(
            status="Available",
            provider="FMP",
            target=SimpleNamespace(
                observed_at="2026-07-30T19:00:00+00:00"
            ),
            recommendations=None,
            estimates=[],
            surprises=[],
            observations=[],
        ),
        "news_intelligence": SimpleNamespace(articles=[
            SimpleNamespace(published_at="2026-07-30T18:00:00+00:00")
        ]),
        "external_evidence": SimpleNamespace(evidence=[
            SimpleNamespace(source_as_of="2026-07-15")
        ]),
        "llm_run_manifest": SimpleNamespace(
            generated_at="2026-07-30T20:05:00+00:00",
            provider="deepseek",
            llm_execution_status="completed",
        ),
        "dual_model_research": None,
    }


def test_live_freshness_separates_each_source_cutoff() -> None:
    summary = build_research_freshness(
        **_base_inputs(),
        completed_at="2026-07-30T20:10:00+00:00",
        now=datetime(2026, 7, 30, 21, tzinfo=timezone.utc),
    )

    rows = {item.component: item for item in summary.components}
    assert summary.overall_status == "Current"
    assert summary.run_completed_at == "2026-07-30T20:10:00+00:00"
    assert rows["Market price"].status == "Current"
    assert rows["Filings"].source_as_of == "2026-07-29"
    assert rows["Consensus"].source_as_of == "2026-07-30T19:00:00+00:00"


def test_weekend_does_not_make_friday_price_stale() -> None:
    inputs = _base_inputs()
    inputs["recent_market_context"].price_as_of = "2026-07-31"

    summary = build_research_freshness(
        **inputs,
        completed_at="2026-08-01T12:00:00+00:00",
        now=datetime(2026, 8, 2, 12, tzinfo=timezone.utc),
    )

    rows = {item.component: item for item in summary.components}
    assert rows["Market price"].status == "Current"


def test_missing_consensus_is_partial_without_misdating_other_sources() -> None:
    inputs = _base_inputs()
    inputs["consensus"] = SimpleNamespace(
        status="Unavailable",
        provider="",
        target=None,
        recommendations=None,
        estimates=[],
        surprises=[],
        observations=[],
    )

    summary = build_research_freshness(
        **inputs,
        completed_at="2026-07-30T20:10:00+00:00",
        now=datetime(2026, 7, 30, 21, tzinfo=timezone.utc),
    )

    rows = {item.component: item for item in summary.components}
    assert summary.overall_status == "Partial"
    assert rows["Consensus"].status == "Unavailable"
    assert rows["Market price"].status == "Current"


def test_frozen_demo_never_presents_fixture_as_live_current_data() -> None:
    inputs = _base_inputs()
    inputs["demo_case"] = SimpleNamespace(refreshed_at="2026-07-24")

    summary = build_research_freshness(
        **inputs,
        now=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )

    assert summary.overall_status == "Frozen demo"
    assert summary.result_type == "frozen_demo"
    assert all(
        item.status in {"Frozen demo", "Unavailable"}
        for item in summary.components
    )


def test_published_demo_uses_admitted_cutoffs_and_labels_other_inputs() -> None:
    inputs = _base_inputs()
    inputs["demo_case"] = SimpleNamespace(refreshed_at="2026-07-16")
    inputs["ticker"] = "AAPL"
    inputs["hybrid_research"] = SimpleNamespace(
        ticker="AAPL",
        generated_at="2026-08-10T18:39:17+00:00",
        completed_at="2026-08-10T18:59:34+00:00",
        published_at="2026-08-10T19:07:30+00:00",
        execution_backend=SimpleNamespace(label="Codex local: GPT-5.6 Sol"),
        normalized_sources=[
            SimpleNamespace(
                source_id="filing",
                ticker="AAPL",
                validation_status="validated",
                source_type="sec_filing",
                source_family="sec_filing",
                metric_or_claim="Revenue",
                publication_date="2026-07-31",
                reporting_period="2026-06-27",
                observed_at="2026-08-10T18:40:21+00:00",
                origin_group="sec.gov",
                form="10-Q",
            ),
            SimpleNamespace(
                source_id="price",
                ticker="AAPL",
                validation_status="validated",
                source_type="quantitative_historical_price",
                source_family="historical_quantitative",
                metric_or_claim="Adjusted Close",
                publication_date="2026-08-07",
                reporting_period="2026-08-07",
                observed_at="2026-08-10T18:40:20+00:00",
                origin_group="Tiingo EOD prices",
                form="",
            ),
        ],
    )

    summary = build_research_freshness(**inputs)

    rows = {item.component: item for item in summary.components}
    assert rows["Research run"].source_as_of == "2026-08-10T18:59:34+00:00"
    assert rows["Filings"].source_as_of == "2026-07-31"
    assert rows["Market price"].source_as_of == "2026-08-07"
    assert rows["Market price"].source == "Tiingo EOD prices"
    assert rows["Consensus"].status == "Illustrative only"
    assert rows["LLM research"].source == "Codex local: GPT-5.6 Sol"
