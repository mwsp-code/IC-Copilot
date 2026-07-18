from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from .sample_data import demo_result


DEFAULT_BENCHMARK_TICKERS = ("AAPL", "NVDA", "BABA", "TSLA", "GS")


@dataclass(frozen=True)
class BenchmarkCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class ResearchIntegrityCase:
    case_id: str
    ticker: str
    case_type: str
    case_title: str
    source: str
    score: int
    checks: tuple[BenchmarkCheck, ...] = field(default_factory=tuple)


def run_fixture_benchmark(
    tickers: Iterable[str] = DEFAULT_BENCHMARK_TICKERS,
    events_per_ticker: int = 5,
) -> dict:
    """Measure structural research integrity on sanitized, no-network fixtures.

    This is deliberately not a return-prediction benchmark. It checks whether the
    app preserves the evidence chain required to audit a thesis.
    """

    del events_per_ticker  # Kept for backward-compatible callers; each ticker has five fixed integrity cases.
    cases: list[ResearchIntegrityCase] = []
    ticker_summaries: list[dict] = []
    for raw_ticker in tickers:
        ticker = raw_ticker.upper().strip()
        result = demo_result(ticker)
        ranked_events = sorted(
            result.events,
            key=lambda event: (
                int(event.severity or 0),
                bool(event.citations),
                bool(event.event_date),
            ),
            reverse=True,
        )
        ideas = result.ideas
        top_event = ranked_events[0] if ranked_events else None
        top_idea = max(
            ideas,
            key=lambda idea: (
                bool(idea.citations),
                getattr(getattr(idea, "score", None), "total", 0),
                bool(idea.monitor_items),
            ),
            default=None,
        )
        high_conviction = [
            idea for idea in ideas
            if "high" in str(idea.stage or "").lower() and "conviction" in str(idea.stage or "").lower()
        ]
        unsupported_high_conviction = [
            idea.idea_id for idea in high_conviction
            if not idea.citations
            or not idea.strongest_counter_thesis
            or idea.strongest_counter_thesis == "Not yet evaluated."
            or not idea.monitor_items
        ]
        case_specs = (
            (
                "event_grounding",
                "Source event is dated, cited, and economically interpreted",
                top_event.source if top_event else "Unknown",
                (
                    BenchmarkCheck("event_present", top_event is not None, "A material source event is present."),
                    BenchmarkCheck(
                        "citation_bound",
                        bool(top_event and top_event.citations and any(citation.url for citation in top_event.citations)),
                        "At least one source URL is attached to the event.",
                    ),
                    BenchmarkCheck("event_dated", bool(top_event and top_event.event_date), "The event has an explicit date."),
                    BenchmarkCheck(
                        "economic_relevance",
                        bool(top_event and (top_event.why_this_matters or "").strip()),
                        "The event states why it could affect the investment case.",
                    ),
                    BenchmarkCheck(
                        "source_identified",
                        bool(top_event and (top_event.source or "").strip()),
                        "A filing, issuer, or normalized source is identified.",
                    ),
                ),
            ),
            (
                "thesis_grounding",
                "Thesis remains connected to source evidence",
                "TradeIdea",
                (
                    BenchmarkCheck("idea_present", top_idea is not None, "At least one research idea is present."),
                    BenchmarkCheck("idea_cited", bool(top_idea and top_idea.citations), "The idea includes citations."),
                    BenchmarkCheck("source_events", bool(top_idea and top_idea.source_events), "The idea retains source events."),
                    BenchmarkCheck("thesis_written", bool(top_idea and top_idea.thesis.strip()), "A thesis statement is present."),
                    BenchmarkCheck(
                        "bounded_direction",
                        bool(top_idea and top_idea.direction in {"Long", "Short", "Neutral", "Watch"}),
                        "Direction is one of Long, Short, Neutral, or Watch.",
                    ),
                ),
            ),
            (
                "counter_thesis",
                "Counter-thesis and falsification remain visible",
                "TradeIdea",
                (
                    BenchmarkCheck(
                        "counter_evaluated",
                        bool(top_idea and top_idea.strongest_counter_thesis)
                        and top_idea.strongest_counter_thesis != "Not yet evaluated.",
                        "The strongest counter-thesis was evaluated.",
                    ),
                    BenchmarkCheck("idea_cited", bool(top_idea and top_idea.citations), "Counter-analysis remains source-bound."),
                    BenchmarkCheck(
                        "next_source",
                        bool(top_idea and (top_idea.next_source_to_check or top_idea.monitor_items)),
                        "A falsification source or monitor is identified.",
                    ),
                    BenchmarkCheck(
                        "driver_mapped",
                        bool(top_idea and (top_idea.causal_bridge_status or top_idea.driver_template_summary)),
                        "The thesis has a causal-driver mapping or explicit bridge status.",
                    ),
                    BenchmarkCheck(
                        "stage_visible",
                        bool(top_idea and top_idea.stage),
                        "The research stage is explicit.",
                    ),
                ),
            ),
            (
                "monitor_and_payoff",
                "Monitoring, market context, and payoff gaps are explicit",
                "TradeIdea",
                (
                    BenchmarkCheck(
                        "monitor_or_work_order",
                        bool(top_idea and (top_idea.monitor_items or top_idea.next_source_to_check)),
                        "A monitor rule or executable next source is attached.",
                    ),
                    BenchmarkCheck("market_capture", bool(top_idea and top_idea.market_capture), "Market-capture mode is recorded."),
                    BenchmarkCheck("score_present", bool(top_idea and top_idea.score), "Research completeness is scored."),
                    BenchmarkCheck("direction_rationale", bool(top_idea and top_idea.direction_rationale), "Direction has a rationale."),
                    BenchmarkCheck(
                        "payoff_or_gap",
                        bool(top_idea and (top_idea.payoff_model or top_idea.gate_result or top_idea.next_source_to_check)),
                        "A payoff model or explicit payoff gap is recorded.",
                    ),
                ),
            ),
            (
                "promotion_integrity",
                "Promotion gates cannot hide unsupported conviction",
                "Deterministic gate",
                (
                    BenchmarkCheck("no_unsupported_high_conviction", not unsupported_high_conviction, "No unsupported idea is promoted."),
                    BenchmarkCheck("validation_present", bool(result.validated_claims), "Claim validation results are attached."),
                    BenchmarkCheck("evidence_ledger", bool(result.evidence_ledger), "An evidence ledger is attached."),
                    BenchmarkCheck("run_manifest", bool(result.run_manifest), "A reproducible run manifest is attached."),
                    BenchmarkCheck("one_pager", bool(result.ic_one_pager), "The IC one-pager is generated from the same result."),
                ),
            ),
        )
        for index, (case_type, title, source, checks) in enumerate(case_specs, start=1):
            score = round(sum(check.passed for check in checks) / len(checks) * 100)
            cases.append(ResearchIntegrityCase(
                case_id=f"{ticker}-{index:02d}",
                ticker=ticker,
                case_type=case_type,
                case_title=title,
                source=source,
                score=score,
                checks=checks,
            ))
        ticker_summaries.append({
            "ticker": ticker,
            "events_evaluated": len(ranked_events),
            "ideas_evaluated": len(ideas),
            "citation_bound_ideas": sum(bool(idea.citations) for idea in ideas),
            "counter_thesis_coverage": sum(
                bool(idea.strongest_counter_thesis)
                and idea.strongest_counter_thesis != "Not yet evaluated."
                for idea in ideas
            ),
            "monitor_rule_coverage": sum(bool(idea.monitor_items) for idea in ideas),
            "high_conviction_ideas": len(high_conviction),
            "unsupported_high_conviction_ids": unsupported_high_conviction,
        })

    case_payloads = [
        {
            **asdict(case),
            "checks": [asdict(check) for check in case.checks],
        }
        for case in cases
    ]
    check_count = sum(len(case.checks) for case in cases)
    passed_count = sum(check.passed for case in cases for check in case.checks)
    unsupported_ids = [
        idea_id
        for summary in ticker_summaries
        for idea_id in summary["unsupported_high_conviction_ids"]
    ]
    status = "passed" if cases and passed_count == check_count and not unsupported_ids else "attention_required"
    return {
        "benchmark": "IC Copilot Research Integrity Benchmark",
        "version": "0.1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fixture_only": True,
        "network_required": False,
        "scope_note": (
            "Structural evidence-chain benchmark on sanitized fixtures; it does not measure "
            "forecast accuracy or investment returns."
        ),
        "status": status,
        "integrity_cases": len(cases),
        "checks_passed": passed_count,
        "checks_total": check_count,
        "pass_rate_pct": round(passed_count / check_count * 100, 1) if check_count else 0.0,
        "unsupported_high_conviction_ids": unsupported_ids,
        "tickers": ticker_summaries,
        "cases": case_payloads,
    }


def render_benchmark_markdown(report: dict) -> str:
    lines = [
        "# IC Copilot Research Integrity Benchmark",
        "",
        f"- Status: **{report['status']}**",
        f"- Research-integrity cases: **{report['integrity_cases']}**",
        f"- Structural checks: **{report['checks_passed']}/{report['checks_total']} ({report['pass_rate_pct']:.1f}%)**",
        "- Network required: **No**",
        "",
        "> This benchmark measures evidence-chain integrity on sanitized fixtures. It does not measure forecast accuracy or returns.",
        "",
        "## Coverage",
        "",
        "| Ticker | Events | Ideas | Citation-bound ideas | Counter-thesis coverage | Monitor coverage | Unsupported high-conviction |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for summary in report["tickers"]:
        lines.append(
            f"| {summary['ticker']} | {summary['events_evaluated']} | {summary['ideas_evaluated']} | "
            f"{summary['citation_bound_ideas']} | {summary['counter_thesis_coverage']} | "
            f"{summary['monitor_rule_coverage']} | {len(summary['unsupported_high_conviction_ids'])} |"
        )
    lines.extend(["", "## Integrity Checks", "", "| Case | Score | Check | Source |", "|---|---:|---|---|"])
    for case in report["cases"]:
        lines.append(
            f"| {case['case_id']} | {case['score']} | {case['case_title']} | {case['source']} |"
        )
    lines.extend([
        "",
        "## Reproduce",
        "",
        "```bash",
        "python scripts/run_benchmark.py",
        "```",
        "",
    ])
    return "\n".join(lines)
