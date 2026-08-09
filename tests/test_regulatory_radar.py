from __future__ import annotations

import pytest

from equity_research.models import DiscoveredSourceCandidate
from equity_research.regulatory_radar import build_regulatory_radar
from equity_research.source_admission import admit_source_candidates


def _candidate(
    candidate_id: str,
    *,
    publisher: str,
    url: str,
    text: str,
    source_type: str = "licensed_newswire",
    fingerprint: str = "",
) -> DiscoveredSourceCandidate:
    return DiscoveredSourceCandidate(
        candidate_id=candidate_id,
        ticker="AAPL",
        url=url,
        title=text,
        publisher=publisher,
        ownership_group=publisher,
        proposed_source_family="news",
        proposed_source_type=source_type,
        publication_date="2026-07-01",
        claimed_relevance="App distribution regulation",
        claim_to_test=text,
        excerpt=text,
        full_text_status="licensed_excerpt",
        evidence_ids=[f"evidence-{candidate_id}"],
        syndication_fingerprint=fingerprint,
    )


def test_single_unofficial_origin_is_regulatory_watch() -> None:
    candidates = [_candidate(
        "one",
        publisher="Publisher A",
        url="https://publisher-a.example/rule",
        text="Officials may regulate app distribution fees.",
    )]
    radar = build_regulatory_radar(
        "AAPL", candidates, admit_source_candidates(candidates)
    )
    assert radar.prospects[0].classification == "Regulatory watch"
    assert radar.prospects[0].allowed_stage == "Candidate"


def test_two_independent_origins_form_reported_prospect() -> None:
    candidates = [
        _candidate(
            "one",
            publisher="Publisher A",
            url="https://publisher-a.example/rule",
            text="Officials may regulate app distribution fees.",
        ),
        _candidate(
            "two",
            publisher="Publisher B",
            url="https://publisher-b.example/rule",
            text="A policy discussion may regulate app distribution fees.",
        ),
    ]
    radar = build_regulatory_radar(
        "AAPL", candidates, admit_source_candidates(candidates)
    )
    assert radar.prospects[0].classification == (
        "Reported regulatory prospect"
    )
    assert radar.prospects[0].allowed_stage == "Research-Ready"


def test_official_proposed_rule_is_primary_confirmed() -> None:
    candidates = [_candidate(
        "official",
        publisher="Federal Trade Commission",
        url="https://www.ftc.gov/news/proposed-rule",
        text="The FTC published a proposed rule affecting app distribution.",
        source_type="regulator_release",
    )]
    radar = build_regulatory_radar(
        "AAPL", candidates, admit_source_candidates(candidates)
    )
    prospect = radar.prospects[0]
    assert prospect.classification == "Primary-confirmed proposal"
    assert prospect.official_source_ids
    assert prospect.allowed_stage == "Research-Ready"


def test_syndicated_reports_do_not_create_independent_prospect() -> None:
    candidates = [
        _candidate(
            "one",
            publisher="Publisher A",
            url="https://publisher-a.example/rule",
            text="Officials may regulate app distribution fees.",
            fingerprint="wire-story-1",
        ),
        _candidate(
            "two",
            publisher="Publisher B",
            url="https://publisher-b.example/copy",
            text="Officials may regulate app distribution fees.",
            fingerprint="wire-story-1",
        ),
    ]
    radar = build_regulatory_radar(
        "AAPL", candidates, admit_source_candidates(candidates)
    )
    assert radar.prospects[0].classification == "Regulatory watch"
    assert radar.independent_external_origin_count == 1


@pytest.mark.parametrize(
    ("text", "stage", "classification", "allowed_stage"),
    [
        (
            "The agency issued a final rule on competition remedies.",
            "final_rule_or_law",
            "Enacted",
            "High-Conviction",
        ),
        (
            "The agency enforced the competition rule and imposed a penalty.",
            "effective_or_enforced",
            "Implemented",
            "High-Conviction",
        ),
        (
            "A court stayed the competition rule pending appeal.",
            "challenged_or_stayed",
            "Challenged or stayed",
            "Research-Ready",
        ),
        (
            "The agency repealed the competition rule.",
            "repealed",
            "Repealed",
            "Research-Ready",
        ),
    ],
)
def test_official_regulatory_lifecycle_states(
    text: str,
    stage: str,
    classification: str,
    allowed_stage: str,
) -> None:
    candidates = [_candidate(
        "official",
        publisher="Federal Trade Commission",
        url="https://www.ftc.gov/news/action",
        text=text,
        source_type="regulator_release",
    )]
    prospect = build_regulatory_radar(
        "AAPL", candidates, admit_source_candidates(candidates)
    ).prospects[0]
    assert prospect.procedural_stage == stage
    assert prospect.classification == classification
    assert prospect.allowed_stage == allowed_stage
