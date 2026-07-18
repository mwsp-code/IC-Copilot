from __future__ import annotations

import json

from equity_research.benchmarking import (
    DEFAULT_BENCHMARK_TICKERS,
    render_benchmark_markdown,
    run_fixture_benchmark,
)


def test_fixture_benchmark_covers_25_no_network_integrity_cases() -> None:
    report = run_fixture_benchmark()

    assert report["fixture_only"] is True
    assert report["network_required"] is False
    assert report["integrity_cases"] == 25
    assert {item["ticker"] for item in report["tickers"]} == set(DEFAULT_BENCHMARK_TICKERS)
    assert report["checks_total"] == 125
    assert not report["unsupported_high_conviction_ids"]


def test_benchmark_report_is_serializable_and_explicit_about_scope() -> None:
    report = run_fixture_benchmark(("AAPL",))
    payload = json.dumps(report)
    markdown = render_benchmark_markdown(report)

    assert "does not measure forecast accuracy" in report["scope_note"]
    assert "AAPL" in payload
    assert "does not measure forecast accuracy or returns" in markdown
