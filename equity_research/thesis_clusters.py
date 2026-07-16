from __future__ import annotations

import re
from collections import defaultdict

from .models import CompanyEconomics, ConsensusPackage, ThesisCluster, TradeIdea, ValuationResult


STAGE_RANK = {"High-Conviction": 3, "Investable": 3, "Research-Ready": 2, "Candidate": 1}


def build_thesis_clusters(
    ideas: list[TradeIdea],
    economics: CompanyEconomics,
    valuation: ValuationResult,
    consensus: ConsensusPackage | None = None,
) -> list[ThesisCluster]:
    grouped: dict[str, list[TradeIdea]] = defaultdict(list)
    for idea in ideas:
        driver = _driver_name(idea)
        cluster_id = _cluster_id(driver, idea.direction)
        idea.thesis_cluster_id = cluster_id
        idea.thesis_cluster_label = f"{idea.direction} {driver}"
        grouped[cluster_id].append(idea)

    clusters: list[ThesisCluster] = []
    for cluster_id, cluster_ideas in grouped.items():
        ordered = sorted(cluster_ideas, key=lambda item: item.score.total if item.score else 0, reverse=True)
        top = ordered[0]
        driver = _driver_name(top)
        stage = max((idea.stage for idea in ordered), key=lambda value: STAGE_RANK.get(value, 0))
        status = _cluster_status(stage, ordered)
        display_score = _cluster_score(top)
        clusters.append(ThesisCluster(
            cluster_id=cluster_id,
            label=_cluster_label(top, driver),
            status=status,
            stage=stage,
            direction=top.direction,
            score=display_score,
            idea_ids=[idea.idea_id for idea in ordered],
            driver_name=driver,
            thesis=_cluster_thesis(top, driver, economics),
            supporting_evidence=_supporting_evidence(ordered),
            counter_thesis=top.strongest_counter_thesis,
            valuation_bridge=_valuation_bridge(valuation),
            priced_in=_priced_in(top, consensus),
            monitor_checklist=_monitor_checklist(ordered),
            evidence_gaps=_evidence_gaps(ordered, economics),
            conviction_chain_status=top.conviction_chain.status if top.conviction_chain else "Not built",
            why_now=_why_now(top),
            what_must_be_true=list(top.conviction_chain.what_must_be_true[:5]) if top.conviction_chain else [],
            what_would_falsify=list(top.conviction_chain.what_would_falsify[:5]) if top.conviction_chain else [],
            next_research_actions=list(top.conviction_chain.next_research_actions[:5]) if top.conviction_chain else [],
        ))
    return sorted(clusters, key=lambda item: (STAGE_RANK.get(item.stage, 0), item.score or 0), reverse=True)


def _driver_name(idea: TradeIdea) -> str:
    event = idea.source_events[0] if idea.source_events else None
    return str((event.metrics or {}).get("economic_driver") or idea.thesis_cluster_label or idea.signal_family or "Unmapped")


def _cluster_id(driver: str, direction: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", f"{direction}-{driver}".lower()).strip("-")
    return slug or "unmapped"


def _cluster_status(stage: str, ideas: list[TradeIdea]) -> str:
    if any(_is_watch_or_unmapped(idea) for idea in ideas):
        if any(_driver_name(idea) == "Unmapped" for idea in ideas):
            return "Needs driver mapping"
        return "Watch item / source validation"
    if stage in {"High-Conviction", "Investable"}:
        return "IC-ready"
    if stage == "Research-Ready":
        return "Promising but incomplete"
    if any(_driver_name(idea) != "Unmapped" for idea in ideas):
        return "Candidate with mapped driver"
    return "Signal-only candidate"


def _cluster_label(top: TradeIdea, driver: str) -> str:
    if top.direction == "Watch":
        return f"Watch item: {driver}"
    if driver == "Unmapped":
        return f"Unmapped signal: {top.signal_family or top.title}"
    return f"{top.direction} thesis cluster: {driver}"


def _cluster_score(top: TradeIdea) -> int | None:
    if not top.score:
        return None
    if _is_watch_or_unmapped(top):
        return min(top.score.total, 40)
    return top.score.total


def _cluster_thesis(top: TradeIdea, driver: str, economics: CompanyEconomics) -> str:
    industry = economics.industry_playbook.industry_label
    if _is_watch_or_unmapped(top):
        return (
            f"This is not an investable thesis yet. The signal is grouped around {driver} "
            f"in the {industry} playbook, but it first needs exact source validation, driver mapping, "
            "or normalization before the app should infer direction or payoff."
        )
    return (
        f"{top.title} is grouped around {driver} in the {industry} playbook. "
        f"{top.thesis}"
    )


def _supporting_evidence(ideas: list[TradeIdea]) -> list[str]:
    rows: list[str] = []
    for idea in ideas[:4]:
        event = idea.source_events[0] if idea.source_events else None
        if event:
            rows.append(f"{event.title}: {event.summary}")
    return rows


def _valuation_bridge(valuation: ValuationResult) -> list[str]:
    if valuation.status != "Available":
        return [f"Valuation unavailable: {'; '.join(valuation.missing_data[:3]) or valuation.status}"]
    rows = [
        f"{case.name}: {case.method}; fair value {case.fair_value:.2f} {valuation.currency}"
        for case in valuation.cases
        if case.fair_value is not None
    ]
    if valuation.expected_return_pct is not None:
        rows.append(f"Probability-weighted expected return: {valuation.expected_return_pct:+.1f}%")
    return rows[:5]


def _priced_in(top: TradeIdea, consensus: ConsensusPackage | None) -> str:
    if _is_watch_or_unmapped(top):
        return "Not assessed. Validate the claim and map the business driver before asking whether the signal is priced in."
    if not top.market_capture:
        return "Unknown; price and consensus reaction were not available."
    if top.market_capture.capture_mode == "Price-only":
        note = (
            "Price-only: event price reaction is available, but point-in-time analyst expectation revision is not. "
            "Use this for price-reaction context only; do not claim uncaptured/not-priced-in without consensus snapshots."
        )
        if top.market_capture.required_inputs:
            note += " Next inputs: " + "; ".join(top.market_capture.required_inputs[:3])
        return note
    note = top.market_capture.diagnosis or top.market_capture.explanation
    if top.market_capture.explanation and top.market_capture.explanation not in note:
        note += " " + top.market_capture.explanation
    if top.market_capture.data_gaps:
        note += " Data needed: " + "; ".join(top.market_capture.data_gaps[:3])
    if top.market_capture.required_inputs:
        note += " Next inputs: " + "; ".join(top.market_capture.required_inputs[:3])
    if consensus and consensus.status != "Available":
        note += f" Consensus status: {consensus.status}."
    if consensus and getattr(consensus, "unofficial_only", False):
        note += " Consensus is unofficial-only, so market capture cannot be classified as definitive."
    return note


def _monitor_checklist(ideas: list[TradeIdea]) -> list[str]:
    checklist: list[str] = []
    for idea in ideas:
        for item in idea.monitor_items:
            text = f"{item.criterion}: confirm if {item.confirm_trigger}; break if {item.break_trigger}"
            if text not in checklist:
                checklist.append(text)
            if len(checklist) >= 6:
                return checklist
    return checklist


def _why_now(top: TradeIdea) -> str:
    event = top.source_events[0] if top.source_events else None
    event_date = event.event_date if event else None
    capture = top.market_capture.category if top.market_capture else "Unknown"
    driver = _driver_name(top)
    if _is_watch_or_unmapped(top):
        return "Follow up now because the source signal is incomplete, unmapped, or normalization-sensitive."
    if event_date:
        return (
            f"{driver} changed in source evidence dated {event_date}; "
            f"market-capture status is {capture}."
        )
    return f"{driver} is flagged as thesis-relevant, but the event date is missing."


def _is_watch_or_unmapped(idea: TradeIdea) -> bool:
    event = idea.source_events[0] if idea.source_events else None
    status = str((event.metrics or {}).get("thesis_grade_status") or idea.thesis_grade_status or "")
    return (
        idea.direction == "Watch"
        or status in {"Watch Item", "Not thesis-grade"}
        or bool((event.metrics or {}).get("normalization_required"))
        or _driver_name(idea) == "Unmapped"
    )


def _evidence_gaps(ideas: list[TradeIdea], economics: CompanyEconomics) -> list[str]:
    gaps: list[str] = []
    for idea in ideas[:4]:
        if idea.gate_result:
            gaps.extend(idea.gate_result.research_ready_failed[:3])
        event = idea.source_events[0] if idea.source_events else None
        if event and event.metrics.get("economic_driver") == "Unmapped":
            gaps.append("Signal is not mapped to a material company or industry driver.")
    gaps.extend(economics.data_gaps[:3])
    return list(dict.fromkeys(gaps))[:8]
