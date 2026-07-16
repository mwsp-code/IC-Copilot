from __future__ import annotations

from equity_research.performance import ResearchProfiler


def test_profiler_reports_bottleneck_treatments_without_disabling_quality_gates() -> None:
    profiler = ResearchProfiler(enabled=True)
    profiler.start()
    profiler.checkpoint("entity_and_filings")
    profiler.checkpoint("thesis_synthesis")

    report = profiler.finish()

    assert report.status == "Available"
    assert report.bottlenecks
    assert any("SEC/filings" in item for item in report.treatments)
    assert any("LLM" in item for item in report.treatments)
    assert any("do not disable source collection or thesis gates" in item for item in report.notes)
