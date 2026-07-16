from __future__ import annotations

import csv
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .adr_profiles import adr_profile_for
from .models import (
    CompanyEconomics,
    CompanyIdentity,
    EvidenceWorkOrder,
    PeerUniverse,
    ResearchScoutQuestion,
    ResearchScoutReport,
    ResearchSourcePlan,
    TradeIdea,
)


def build_research_scout_report(
    identity: CompanyIdentity,
    ideas: list[TradeIdea],
    economics: CompanyEconomics,
    peer_universe: PeerUniverse,
    source_plan: ResearchSourcePlan,
    evidence_work_order: EvidenceWorkOrder,
) -> ResearchScoutReport:
    """Build source-aware questions that make the app inquisitive without weakening evidence gates."""
    source_requirements = _load_source_requirements(config.SOURCE_REQUIREMENTS_CSV)
    geography_rows = _load_geography_rows(config.GEOGRAPHY_EXPOSURE_PLAYBOOK_CSV)
    adr_profile = adr_profile_for(identity.ticker)
    questions: list[ResearchScoutQuestion] = []

    for idea in ideas[:8]:
        family = _driver_family_for_idea(idea)
        matched = _matching_requirements(source_requirements, family, adr_profile is not None)
        for row in matched[:3]:
            questions.append(_question_from_requirement(identity.ticker, idea, row))

    for request in source_plan.requests[:6]:
        questions.append(ResearchScoutQuestion(
            question_id=_id(identity.ticker, "source_plan", request.request_id),
            lens="source_plan",
            priority=request.priority,
            question=request.title,
            why_it_matters=request.reason_to_inspect,
            source_types=[request.source_type],
            expected_evidence=request.expected_evidence_type,
            confirms_or_disproves=request.confirms_or_disproves,
            current_status=request.status,
            story_use="Turns a detected signal into source-backed company narrative or falsification evidence.",
        ))

    for item in evidence_work_order.items[:6]:
        questions.append(ResearchScoutQuestion(
            question_id=_id(identity.ticker, "work_order", item.work_id),
            lens="evidence_work_order",
            priority=item.priority,
            question=item.action,
            why_it_matters=item.why_it_matters,
            source_types=[item.source_type],
            expected_evidence=item.expected_output,
            confirms_or_disproves="; ".join(item.acceptance_criteria[:2] or item.falsification_tests[:2]),
            current_status="automatic fetch or validation needed",
            related_idea_ids=list(item.related_idea_ids),
            story_use="Closes a named gap before the app upgrades the narrative or conviction.",
        ))

    geography_axes = _geography_axes(identity, adr_profile, geography_rows)
    company_axes = _company_axes(identity, economics)
    sector_axes = _sector_axes(economics)
    peer_axes = _peer_axes(peer_universe)
    questions = _dedupe_questions(questions)[:18]
    data_gaps = []
    if not source_requirements:
        data_gaps.append("No source requirement CSV was found; Research Scout used source-plan/work-order fallbacks.")
    if adr_profile and not geography_axes:
        data_gaps.append("ADR/FPI geography profile exists, but geography exposure playbook rows were unavailable.")
    status = "Available" if questions or company_axes or sector_axes or geography_axes else "Unavailable"
    return ResearchScoutReport(
        ticker=identity.ticker,
        status=status,
        summary=_summary(identity, questions, geography_axes, peer_axes),
        generated_at=_utc_now(),
        questions=questions,
        company_story_axes=company_axes,
        sector_story_axes=sector_axes,
        geography_story_axes=geography_axes,
        peer_story_axes=peer_axes,
        data_gaps=data_gaps,
    )


def _driver_family_for_idea(idea: TradeIdea) -> str:
    event = idea.source_events[0] if idea.source_events else None
    driver = ""
    metric = ""
    if event:
        driver = str((event.metrics or {}).get("economic_driver") or "")
        metric = str((event.metrics or {}).get("metric_name") or "")
    text = f"{idea.signal_family} {idea.title} {driver} {metric}".lower()
    if any(token in text for token in ("gross", "margin", "mix", "cost of revenue")):
        return "margin"
    if any(token in text for token in ("revenue", "sales", "demand", "gmv", "cloud", "commerce")):
        return "revenue"
    if any(token in text for token in ("cash", "liquidity", "free cash flow", "operating cash flow")):
        return "cash_credit"
    if any(token in text for token in ("debt", "leverage", "refinancing", "interest")):
        return "cash_credit"
    if any(token in text for token in ("share", "buyback", "dilution", "ads")):
        return "share_count"
    if any(token in text for token in ("guidance", "outlook", "expectation")):
        return "guidance"
    if any(token in text for token in ("regulation", "legal", "policy", "litigation")):
        return "regulation"
    if any(token in text for token in ("management", "tone", "credibility", "proxy", "agm", "egm")):
        return "management"
    return "financial_kpi"


def _matching_requirements(rows: list[dict[str, str]], family: str, is_adr: bool) -> list[dict[str, str]]:
    matches = []
    for row in rows:
        row_family = (row.get("driver_family") or "").strip().lower()
        geography = (row.get("geography") or "").strip().lower()
        if row_family not in {family.lower(), "all"}:
            continue
        if geography == "adr" and not is_adr:
            continue
        matches.append(row)
    priority = {"High": 0, "Medium": 1, "Low": 2}
    return sorted(matches, key=lambda item: priority.get(item.get("priority") or "Medium", 1))


def _question_from_requirement(ticker: str, idea: TradeIdea, row: dict[str, str]) -> ResearchScoutQuestion:
    source_types = [item.strip() for item in (row.get("source_types") or row.get("source_type") or "").replace(";", "|").split("|") if item.strip()]
    question = (row.get("question") or f"Check {row.get('driver_family', 'driver')} evidence").strip()
    return ResearchScoutQuestion(
        question_id=_id(ticker, idea.idea_id, question),
        lens=(row.get("lens") or row.get("driver_family") or "driver").strip(),
        priority=(row.get("priority") or "Medium").strip(),
        question=question,
        why_it_matters=(row.get("why_it_matters") or "This evidence can confirm or disprove the thesis driver.").strip(),
        source_types=source_types,
        expected_evidence=(row.get("expected_evidence") or "").strip(),
        confirms_or_disproves=(row.get("confirms_or_disproves") or "").strip(),
        current_status="source not attempted",
        related_idea_ids=[idea.idea_id],
        story_use=(row.get("story_use") or "Connects company facts to sector/geography narrative.").strip(),
    )


def _company_axes(identity: CompanyIdentity, economics: CompanyEconomics) -> list[str]:
    rows = [
        economics.business_model,
        f"Material drivers: {', '.join(driver.name for driver in economics.drivers[:6]) or 'unknown'}.",
        f"Playbook source: {economics.industry_playbook.playbook_source}; quality {economics.playbook_quality_score}/100.",
    ]
    if identity.sic_description:
        rows.append(f"SIC context: {identity.sic_description}.")
    return [row for row in rows if row]


def _sector_axes(economics: CompanyEconomics) -> list[str]:
    playbook = economics.industry_playbook
    return [
        f"Sector story: {playbook.industry_label}.",
        f"Key KPIs: {', '.join(playbook.key_kpis[:8]) or 'unknown'}.",
        f"Leading indicators: {', '.join(playbook.leading_indicators[:8]) or 'unknown'}.",
        f"Valuation methods: {', '.join(playbook.valuation_methods[:5]) or 'unknown'}.",
        f"Macro sensitivities: {', '.join(playbook.macro_sensitivities[:6]) or 'unknown'}.",
    ]


def _peer_axes(peer_universe: PeerUniverse) -> list[str]:
    if peer_universe.status != "Configured":
        return [peer_universe.reason or "Curated peer universe is not configured."]
    peers = ", ".join(f"{peer.ticker} ({peer.relationship})" for peer in peer_universe.peers[:8])
    return [
        f"Peer universe: {peers}.",
        f"Peer metrics: {', '.join(peer_universe.key_metrics[:8]) or 'unknown'}.",
        f"Peer provenance: {peer_universe.provenance}.",
    ]


def _geography_axes(identity: CompanyIdentity, adr_profile, rows: list[dict[str, str]]) -> list[str]:
    axes: list[str] = []
    ticker = identity.ticker.upper()
    for row in rows:
        tickers = {item.strip().upper() for item in (row.get("tickers") or "").replace(";", "|").split("|") if item.strip()}
        market = (row.get("market") or "").strip()
        if ticker not in tickers and not (adr_profile and market.lower() in {"china adr", "adr/fpi", "global adr"}):
            continue
        axis = (row.get("story_axis") or "").strip()
        if axis:
            axes.append(axis)
        metrics = (row.get("macro_indicators") or "").strip()
        if metrics:
            axes.append(f"Geography indicators: {metrics}.")
        benchmarks = (row.get("benchmarks") or "").strip()
        if benchmarks:
            axes.append(f"Geography benchmarks: {benchmarks}.")
    if adr_profile:
        axes.append(
            f"ADR/FPI profile: home exchange {adr_profile.home_exchange}; reporting currency "
            f"{adr_profile.reporting_currency}; ADR ratio {adr_profile.ordinary_share_ratio}; "
            f"benchmarks {', '.join(adr_profile.benchmark_tickers) or 'none'}."
        )
    return _dedupe(axes)


def _load_source_requirements(path: Path) -> list[dict[str, str]]:
    return _load_csv_rows(path)


def _load_geography_rows(path: Path) -> list[dict[str, str]]:
    return _load_csv_rows(path)


def _load_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        with path.open("r", newline="", encoding="utf-8-sig") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    except OSError:
        return []


def _dedupe_questions(questions: list[ResearchScoutQuestion]) -> list[ResearchScoutQuestion]:
    seen: set[tuple[str, str]] = set()
    rows: list[ResearchScoutQuestion] = []
    priority = {"High": 0, "Medium": 1, "Low": 2}
    for question in sorted(questions, key=lambda item: (priority.get(item.priority, 1), item.lens, item.question)):
        key = (question.lens, question.question)
        if key in seen:
            continue
        seen.add(key)
        rows.append(question)
    return rows


def _summary(
    identity: CompanyIdentity,
    questions: list[ResearchScoutQuestion],
    geography_axes: list[str],
    peer_axes: list[str],
) -> str:
    if not questions:
        return f"Research Scout has no open source questions for {identity.ticker}; use existing evidence/work orders."
    return (
        f"Research Scout generated {len(questions)} source-aware question(s) for {identity.ticker}, "
        f"covering company, sector, peer, and {'geography' if geography_axes else 'market'} context. "
        f"Peer context {'available' if peer_axes else 'unavailable'}."
    )


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    rows: list[str] = []
    for value in values:
        clean = str(value or "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            rows.append(clean)
    return rows


def _id(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
