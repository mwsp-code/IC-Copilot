from __future__ import annotations

import re

from .models import (
    CompanyEconomics,
    ResearchQuestion,
    ResearchSourcePlan,
    ThesisCluster,
    TradeIdea,
)


INVESTABLE_STAGES = {"High-Conviction", "Investable"}


def build_research_questions(
    ideas: list[TradeIdea],
    clusters: list[ThesisCluster],
    economics: CompanyEconomics,
    source_plan: ResearchSourcePlan | None = None,
    *,
    limit: int = 8,
) -> list[ResearchQuestion]:
    by_id = {idea.idea_id: idea for idea in ideas}
    questions: list[ResearchQuestion] = []
    for cluster in clusters:
        related = [by_id[item] for item in cluster.idea_ids if item in by_id]
        if not related:
            continue
        top = related[0]
        missing_links = _missing_links(top, cluster)
        if cluster.stage in INVESTABLE_STAGES and not missing_links:
            continue
        driver = cluster.driver_name or _driver_name(top)
        required = _required_evidence(top, cluster, economics)
        next_sources = _next_sources(top, cluster, source_plan)
        market_needs = _market_capture_needs(top)
        primary_sources = _primary_source_types(top, cluster)
        acceptance = _acceptance_criteria(top, cluster, required, market_needs)
        falsification = _falsification_tests(top, cluster)
        workplan = _workplan_steps(top, cluster, primary_sources, next_sources, market_needs)
        answerability_status, answerability_score, answerability_gaps, decision_rule = _answerability(
            top, cluster, required, next_sources, market_needs, primary_sources, acceptance, falsification,
        )
        if not any((missing_links, required, next_sources, market_needs)):
            continue
        questions.append(
            ResearchQuestion(
                question_id=_question_id(cluster.cluster_id, top.idea_id),
                title=_question_title(cluster, top, driver),
                priority=_priority(top, cluster, missing_links, market_needs),
                status=_status(cluster, top),
                driver_name=driver,
                source_signal=_source_signal(top),
                why_it_matters=_why_driver_matters(driver, economics, top),
                missing_links=missing_links,
                required_evidence=required,
                next_sources=next_sources,
                market_capture_needs=market_needs,
                answerability_status=answerability_status,
                answerability_score=answerability_score,
                answerability_gaps=answerability_gaps,
                decision_rule=decision_rule,
                hypothesis=_hypothesis(top, cluster, driver),
                minimum_evidence_package=_minimum_evidence_package(required, primary_sources, market_needs),
                answer_format=_answer_format(top, driver),
                stop_condition=_stop_condition(falsification, market_needs),
                promotion_criteria=_promotion_criteria(top),
                primary_source_types=primary_sources,
                acceptance_criteria=acceptance,
                falsification_tests=falsification,
                workplan_steps=workplan,
                related_idea_ids=list(cluster.idea_ids),
                equity_lens=(top.equity_credit_lens or {}).get("equity", ""),
                credit_lens=(top.equity_credit_lens or {}).get("credit", ""),
            )
        )
        if len(questions) >= limit:
            break
    return questions


def _missing_links(top: TradeIdea, cluster: ThesisCluster) -> list[str]:
    rows: list[str] = []
    if top.thesis_audit_chain:
        rows.extend(top.thesis_audit_chain.broken_links)
    if top.gate_result:
        rows.extend(top.gate_result.research_ready_failed[:4])
    rows.extend(cluster.evidence_gaps[:4])
    return _dedupe(rows)[:8]


def _required_evidence(
    top: TradeIdea,
    cluster: ThesisCluster,
    economics: CompanyEconomics,
) -> list[str]:
    driver = (cluster.driver_name or _driver_name(top)).lower()
    rows: list[str] = []
    if "source excerpt" in _lowered_missing(top, cluster) or "validated claim" in _lowered_missing(top, cluster):
        rows.append("Exact source excerpt with URL, filing section, period, and citation snippet.")
    if "business driver" in _lowered_missing(top, cluster) or cluster.driver_name == "Unmapped":
        rows.append("Map the source signal to a material company or industry driver from the playbook.")
    if "valuation" in _lowered_missing(top, cluster):
        rows.append("Bull/base/bear operating assumptions and explicit exit-value anchor.")
    if "monitor" in _lowered_missing(top, cluster):
        rows.append("Machine-readable confirmation and break criteria with metric, operator, threshold, deadline, and source.")
    if any(token in driver for token in ("gross", "margin", "mix")):
        rows.append("Margin bridge: revenue, COGS, pricing/ASP, volume, mix, incentives, warranty, input cost, and segment margin where available.")
    elif any(token in driver for token in ("revenue", "demand", "sales")):
        rows.append("Demand bridge: volume, price, segment revenue, customer/channel evidence, and official industry demand data.")
    elif any(token in driver for token in ("cash", "debt", "liquidity", "credit")):
        rows.append("Credit/liquidity bridge: cash quality, debt maturity, interest burden, covenants, rating/spread evidence, and FCF conversion.")
    elif any(token in driver for token in ("share", "dilution", "buyback")):
        rows.append("Share reconciliation: ordinary shares vs ADS, ADR ratio, weighted-average basis, split/corporate action, and buyback table.")
    elif any(token in driver for token in ("guidance", "expectation")):
        rows.append("Management guidance bridge: exact metric, period, range/value, currency/unit, speaker/source, and post-event consensus revision.")
    if economics.industry_playbook.key_kpis:
        rows.append("Playbook KPI cross-check: " + ", ".join(economics.industry_playbook.key_kpis[:5]) + ".")
    return _dedupe(rows)[:8]


def _next_sources(
    top: TradeIdea,
    cluster: ThesisCluster,
    source_plan: ResearchSourcePlan | None,
) -> list[str]:
    rows: list[str] = []
    if top.next_source_to_check:
        rows.append(top.next_source_to_check)
    if top.thesis_audit_chain:
        rows.extend(top.thesis_audit_chain.next_actions[:5])
    rows.extend(cluster.next_research_actions[:5])
    if source_plan:
        for request in source_plan.requests:
            text = f"{request.title} [{request.source_type}]: {request.reason_to_inspect}"
            if _source_request_relevant(request, top, cluster):
                rows.append(text)
    return _dedupe(rows)[:8]


def _market_capture_needs(top: TradeIdea) -> list[str]:
    capture = top.market_capture
    if not capture:
        return ["Add event-window price reaction and point-in-time consensus snapshots."]
    if capture.capture_mode == "Price-only":
        return [
            "Optional for priced-in/uncaptured claim: add official point-in-time EPS/revenue/target/recommendation revisions."
        ]
    rows = list(capture.data_gaps)
    if capture.category == "Unknown":
        if capture.price_reaction_pct is None:
            rows.append("Missing event-specific price reaction.")
        if capture.consensus_revision_pct is None:
            rows.append("Missing official point-in-time EPS/revenue/target/recommendation revisions.")
    return _dedupe(rows)[:6]


def _answerability(
    top: TradeIdea,
    cluster: ThesisCluster,
    required_evidence: list[str],
    next_sources: list[str],
    market_needs: list[str],
    primary_sources: list[str],
    acceptance: list[str],
    falsification: list[str],
) -> tuple[str, int, list[str], str]:
    gaps: list[str] = []
    score = 0
    if required_evidence:
        score += 20
    else:
        gaps.append("Define driver-specific evidence needed to answer the question.")
    if primary_sources:
        score += 15
    else:
        gaps.append("Name at least one registered primary source type to inspect.")
    if next_sources:
        score += 15
    else:
        gaps.append("Add a concrete next source or source-plan request.")
    if acceptance:
        score += 15
    else:
        gaps.append("Define acceptance criteria for a yes/no research answer.")
    if falsification:
        score += 15
    else:
        gaps.append("Define falsification tests before drawing a thesis conclusion.")
    mandatory_market_needs = [need for need in market_needs if not need.lower().startswith("optional for")]
    if mandatory_market_needs:
        gaps.append("Resolve point-in-time market-capture inputs before calling the idea priced-in or uncaptured.")
    else:
        score += 10
    driver = cluster.driver_name or _driver_name(top)
    if driver and driver != "Unmapped":
        score += 10
    else:
        gaps.append("Map the source signal to a material company or industry driver.")
    status = _answerability_status(score, gaps, mandatory_market_needs, driver)
    decision_rule = _decision_rule(top, driver, mandatory_market_needs)
    return status, min(score, 100), _dedupe(gaps)[:6], decision_rule


def _answerability_status(
    score: int,
    gaps: list[str],
    market_needs: list[str],
    driver: str,
) -> str:
    if driver == "Unmapped" or any("driver" in gap.lower() for gap in gaps):
        return "Driver mapping needed"
    if market_needs and score >= 65:
        return "Answerable after market-capture inputs"
    if score >= 85:
        return "Ready to answer"
    if score >= 65:
        return "Answerable with listed workplan"
    if score >= 40:
        return "Needs more source planning"
    return "Under-specified"


def _decision_rule(top: TradeIdea, driver: str, market_needs: list[str]) -> str:
    direction = top.direction if top.direction in {"Long", "Short"} else "the proposed direction"
    capture_clause = (
        "and point-in-time price/consensus data no longer blocks market-capture assessment"
        if market_needs else
        "and market-capture data is either resolved or not material to the thesis"
    )
    return (
        f"Answer yes only if primary evidence confirms {driver}, the causal bridge explains why it matters, "
        f"the strongest counter-thesis is addressed, {capture_clause}, and payoff/monitor assumptions support {direction}. "
        "Otherwise keep the idea as Watch/Candidate."
    )


def _hypothesis(top: TradeIdea, cluster: ThesisCluster, driver: str) -> str:
    direction = top.direction if top.direction in {"Long", "Short"} else cluster.direction or "Watch"
    verb = "supports" if direction == "Long" else "pressures" if direction == "Short" else "may affect"
    signal = _source_signal(top)
    thesis_label = top.title or cluster.label or top.idea_id
    return (
        f"Test whether the source-linked signal ({signal}) {verb} '{thesis_label}' through "
        f"the {driver} driver, after checking primary evidence, counter-evidence, and market capture."
    )


def _minimum_evidence_package(
    required_evidence: list[str],
    primary_sources: list[str],
    market_needs: list[str],
) -> list[str]:
    rows: list[str] = []
    if required_evidence:
        rows.append("Driver evidence: " + required_evidence[0])
    if primary_sources:
        rows.append("Primary source check: " + primary_sources[0])
    if len(primary_sources) > 1:
        rows.append("Corroborating source check: " + primary_sources[1])
    if market_needs:
        rows.append("Market-capture input: " + market_needs[0])
    rows.append("Counter-thesis and falsification check documented before promotion.")
    return _dedupe(rows)[:5]


def _answer_format(top: TradeIdea, driver: str) -> str:
    direction = top.direction if top.direction else "proposed direction"
    return (
        f"Return a yes/no/insufficient answer: whether {driver} supports {direction}, "
        "with citations, affected KPI/period/unit, causal bridge, counter-thesis, market-capture diagnosis, "
        "and the exact promotion or rejection reason."
    )


def _stop_condition(falsification: list[str], market_needs: list[str]) -> str:
    if falsification:
        return "Stop or downgrade the idea if: " + falsification[0]
    if market_needs:
        return "Stop before promotion if point-in-time price or consensus evidence cannot be sourced."
    return "Stop before promotion if no primary source confirms the driver-specific causal bridge."


def _promotion_criteria(top: TradeIdea) -> list[str]:
    rows = [
        "Validated source claim with citation and period.",
        "Mapped material business driver and causal bridge.",
        "Current entry price and payoff assumptions.",
        "Counter-thesis documented and contradiction-tested.",
        "Machine-readable monitor criteria.",
    ]
    if top.market_capture and top.market_capture.category == "Unknown":
        rows.append("Market-capture status resolved or explicitly marked as not required for the thesis.")
    return rows


def _primary_source_types(top: TradeIdea, cluster: ThesisCluster) -> list[str]:
    driver = (cluster.driver_name or _driver_name(top)).lower()
    rows: list[str] = []
    if any(token in driver for token in ("gross", "margin", "mix")):
        rows.extend([
            "Issuer MD&A margin discussion",
            "Earnings release or results deck",
            "Segment margin or COGS table",
            "Official input-cost or production data when applicable",
        ])
    elif any(token in driver for token in ("revenue", "demand", "sales")):
        rows.extend([
            "Issuer segment revenue disclosure",
            "Earnings call demand commentary",
            "Official industry demand dataset",
            "Customer/channel evidence when source-linked",
        ])
    elif any(token in driver for token in ("cash", "debt", "liquidity", "credit")):
        rows.extend([
            "Cash flow statement",
            "Debt footnote and maturity table",
            "Credit facility or covenant disclosure",
            "Rating action or credit-spread evidence",
        ])
    elif any(token in driver for token in ("share", "dilution", "buyback")):
        rows.extend([
            "Weighted-average share table",
            "Period-end share count disclosure",
            "Buyback table",
            "ADR ratio, split, or corporate-action record",
        ])
    elif any(token in driver for token in ("guidance", "expectation")):
        rows.extend([
            "Issuer guidance release or transcript excerpt",
            "Exact metric/period/range disclosure",
            "Point-in-time consensus snapshot",
            "Post-event revision snapshot",
        ])
    elif any(token in driver for token in ("regulatory", "legal", "litigation", "policy")):
        rows.extend([
            "Issuer filing or 8-K/6-K disclosure",
            "Official regulator release",
            "Court or docket reference",
            "Settlement/order text where available",
        ])
    elif "management" in driver:
        rows.extend([
            "Earnings-call transcript",
            "Prior promise or guidance source",
            "Subsequent filing or KPI outcome",
            "Management claim cross-check",
        ])
    else:
        rows.extend([
            "Issuer filing or official release",
            "Relevant playbook KPI disclosure",
            "Primary source that confirms the affected driver",
        ])
    if top.market_capture and top.market_capture.category == "Unknown":
        rows.append("Point-in-time consensus and event-window price data")
    return _dedupe(rows)[:8]


def _acceptance_criteria(
    top: TradeIdea,
    cluster: ThesisCluster,
    required_evidence: list[str],
    market_needs: list[str],
) -> list[str]:
    driver = cluster.driver_name or _driver_name(top)
    rows = [
        "Current and prior source excerpts identify metric, period, unit, value, direction, and citation.",
        f"The evidence maps cleanly to {driver} and explains the economic mechanism, not only a keyword count.",
    ]
    if required_evidence:
        rows.append("All driver-specific evidence needs are either satisfied or explicitly marked unavailable with source status.")
    if market_needs:
        rows.append("Market-capture status names which price/consensus snapshots are available, missing, stale, or unofficial.")
    if top.direction == "Short":
        rows.append("Short direction has explicit negative company evidence or bearish valuation/payoff evidence.")
    elif top.direction == "Long":
        rows.append("Long direction has explicit positive company evidence or bullish valuation/payoff evidence.")
    rows.append("Counter-thesis is source-linked and no critical contradiction remains unresolved.")
    return _dedupe(rows)[:8]


def _falsification_tests(top: TradeIdea, cluster: ThesisCluster) -> list[str]:
    driver = (cluster.driver_name or _driver_name(top)).lower()
    rows: list[str] = []
    if any(token in driver for token in ("gross", "margin", "mix")):
        rows.extend([
            "Management or filings identify the margin change as temporary, accounting-driven, or mix-neutral.",
            "Peer metric read-through shows the same margin pattern is sector-wide rather than company-specific.",
            "COGS, incentives, warranty, or segment data contradict the proposed margin mechanism.",
        ])
    elif any(token in driver for token in ("revenue", "demand", "sales")):
        rows.extend([
            "Volume, price, or segment disclosures show the revenue change is one-time or non-core.",
            "Industry demand data or peers contradict the demand-improvement interpretation.",
        ])
    elif any(token in driver for token in ("cash", "debt", "liquidity", "credit")):
        rows.extend([
            "Cash increase is restricted, seasonal, working-capital timing, or offset by debt/refinancing risk.",
            "Interest burden, maturities, or covenant evidence contradict the liquidity thesis.",
        ])
    elif any(token in driver for token in ("share", "dilution", "buyback")):
        rows.extend([
            "Share-count movement disappears after ADS/ordinary-share, split, or weighted-average normalization.",
            "Buyback table or issuance disclosure contradicts the dilution/buyback interpretation.",
        ])
    elif any(token in driver for token in ("guidance", "expectation")):
        rows.extend([
            "Exact guidance text is vague, safe-harbor boilerplate, or lacks metric/period/range.",
            "Post-event consensus and price reaction show the guidance change was already captured.",
        ])
    else:
        rows.extend([
            "Primary source evidence fails to confirm the affected driver.",
            "The strongest counter-thesis explains the signal better than the proposed thesis.",
        ])
    if top.market_capture and top.market_capture.category == "Unknown":
        rows.append("Market-capture data shows the evidence was already reflected in consensus or price.")
    return _dedupe(rows)[:8]


def _workplan_steps(
    top: TradeIdea,
    cluster: ThesisCluster,
    primary_sources: list[str],
    next_sources: list[str],
    market_needs: list[str],
) -> list[str]:
    rows = [
        "Validate the exact source claim and reject boilerplate, accounting-footnote, or stale-period signals.",
        "Complete the driver-specific causal bridge with current/prior metrics and cited source excerpts.",
    ]
    if primary_sources:
        rows.append(f"Pull primary source first: {primary_sources[0]}.")
    if next_sources:
        rows.append(f"Check next planned source: {next_sources[0]}.")
    if market_needs:
        rows.append("Seed or fetch point-in-time market-capture inputs before calling the idea uncaptured.")
    if cluster.stage != "Research-Ready":
        rows.append("Keep as Watch/Candidate until acceptance criteria pass; do not promote from signal strength alone.")
    else:
        rows.append("If acceptance and falsification tests pass, rerun gates for High-Conviction eligibility.")
    return _dedupe(rows)[:7]


def _status(cluster: ThesisCluster, top: TradeIdea) -> str:
    if top.thesis_audit_chain and top.thesis_audit_chain.status == "Incomplete":
        return "Research question"
    if cluster.stage == "Research-Ready":
        return "Thesis upgrade question"
    return "Early research question"


def _priority(
    top: TradeIdea,
    cluster: ThesisCluster,
    missing_links: list[str],
    market_needs: list[str],
) -> str:
    if cluster.stage == "Research-Ready" or (top.score and top.score.total >= 65):
        return "High"
    if any("Market capture" in item for item in missing_links) or market_needs:
        return "Medium"
    return "Low"


def _question_title(cluster: ThesisCluster, top: TradeIdea, driver: str) -> str:
    if cluster.driver_name == "Unmapped":
        return f"What source evidence would map this {top.signal_family or 'signal'} to a real business driver?"
    direction = top.direction if top.direction not in {"Watch", ""} else "investment"
    return f"Can {driver} support a {direction} thesis?"


def _source_signal(top: TradeIdea) -> str:
    event = top.source_events[0] if top.source_events else None
    if not event:
        return top.title
    return f"{event.title}: {event.summary}"


def _why_driver_matters(driver: str, economics: CompanyEconomics, top: TradeIdea) -> str:
    for item in economics.drivers:
        if item.name == driver or item.name.lower() in driver.lower() or driver.lower() in item.name.lower():
            return item.why_it_matters or item.current_evidence
    event = top.source_events[0] if top.source_events else None
    if event and event.metrics.get("driver_why_it_matters"):
        return str(event.metrics["driver_why_it_matters"])
    return f"This question should be tested against the {economics.industry_playbook.industry_label} playbook before it becomes an idea."


def _driver_name(top: TradeIdea) -> str:
    event = top.source_events[0] if top.source_events else None
    return str((event.metrics or {}).get("economic_driver") or top.thesis_cluster_label or top.signal_family or "Unmapped")


def _lowered_missing(top: TradeIdea, cluster: ThesisCluster) -> str:
    return " | ".join(_missing_links(top, cluster)).lower()


def _source_request_relevant(request, top: TradeIdea, cluster: ThesisCluster) -> bool:
    haystack = " ".join([
        request.title,
        request.reason_to_inspect,
        request.expected_evidence_type,
        request.confirms_or_disproves,
    ]).lower()
    driver = (cluster.driver_name or _driver_name(top)).lower()
    signal = (top.signal_family or "").lower()
    if driver != "unmapped" and any(token and token in haystack for token in re.split(r"[^a-z0-9]+", driver)):
        return True
    return bool(signal and signal in haystack)


def _question_id(cluster_id: str, idea_id: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", f"{cluster_id}-{idea_id}".lower()).strip("-")
    return f"rq-{slug}"[:80]


def _dedupe(values: list[str]) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        rows.append(text)
    return rows
