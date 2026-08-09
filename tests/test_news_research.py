from __future__ import annotations

from equity_research import config
from equity_research.models import ChangeEvent, CompanyIdentity, ExternalEvidenceBundle
from equity_research.news_research import (
    build_news_intelligence_report,
    build_news_research_plan,
    news_claims_from_report,
)


def _identity(ticker: str = "ORCL", name: str = "Oracle Corporation") -> CompanyIdentity:
    return CompanyIdentity(ticker=ticker, cik="0000000001", name=name, exchange="NASDAQ")


def _event() -> ChangeEvent:
    return ChangeEvent(
        category="financial_kpi",
        title="Capital expenditure increased",
        summary="Capital expenditure increased while free cash flow declined.",
        severity=5,
        direction="neutral",
        event_date="2026-06-22",
        source="10-K",
        metrics={"economic_driver": "capital_expenditure"},
    )


def _clear_live_keys(monkeypatch) -> None:
    for name in (
        "EODHD_API_KEY",
        "FMP_API_KEY",
        "ALPHAVANTAGE_API_KEY",
        "FINNHUB_API_KEY",
        "NEWSAPI_API_KEY",
        "EVENT_REGISTRY_API_KEY",
        "WISBURG_API_KEY",
    ):
        monkeypatch.setattr(config, name, "")


def test_syndicated_reuters_copies_count_once(monkeypatch) -> None:
    _clear_live_keys(monkeypatch)
    monkeypatch.setattr(config, "EODHD_API_KEY", "fixture-key")

    def fetcher(_url: str, _timeout: int):
        return [
            {
                "source": "Reuters",
                "title": "Oracle expands data-center capacity as cloud backlog grows",
                "link": "https://publisher-a.example/oracle-capacity",
                "date": "2026-06-23T10:00:00Z",
                "content": "Oracle is expanding capacity against contracted cloud demand.",
            },
            {
                "source": "Reuters",
                "title": "Oracle expands data-center capacity as cloud backlog grows",
                "link": "https://publisher-b.example/reuters-oracle-capacity",
                "date": "2026-06-23T10:00:00Z",
                "content": "Syndicated copy of the same report.",
            },
        ]

    report = build_news_intelligence_report(
        _identity(),
        [_event()],
        ExternalEvidenceBundle("ORCL", "Unavailable"),
        fetch_json=fetcher,
    )

    assert len(report.articles) == 1
    assert len(report.syndication) == 1
    assert len(report.syndication[0].duplicate_observation_ids) == 1
    assert report.clusters[0].source_origin_groups == ["reuters"]


def test_news_claims_remain_candidate_context(monkeypatch) -> None:
    _clear_live_keys(monkeypatch)
    monkeypatch.setattr(config, "EODHD_API_KEY", "fixture-key")

    report = build_news_intelligence_report(
        _identity(),
        [_event()],
        ExternalEvidenceBundle("ORCL", "Unavailable"),
        fetch_json=lambda _url, _timeout: [{
            "source": "Industry Journal",
            "title": "Oracle cloud capacity spending accelerates",
            "link": "https://example.com/oracle-cloud",
            "date": "2026-06-23",
            "content": "The report links backlog growth with higher capacity spending.",
        }],
    )
    claims = news_claims_from_report(report, "Oracle Corporation")

    assert claims
    assert {claim.allowed_stage for claim in claims} == {"Candidate"}
    assert all(claim.source_tier >= 3 for claim in claims)
    assert all(claim.required_corroboration for claim in claims)


def test_zero_results_have_exact_coverage_diagnosis(monkeypatch) -> None:
    _clear_live_keys(monkeypatch)

    report = build_news_intelligence_report(
        _identity(),
        [_event()],
        ExternalEvidenceBundle("ORCL", "Unavailable"),
        fetch_json=lambda _url, _timeout: {},
    )

    assert report.status == "Unavailable"
    assert report.coverage_status
    assert "Missing entitlement or key" in report.coverage_status
    assert "0" not in report.summary.split("Coverage diagnosis:", 1)[-1].strip()


def test_llm_plan_rejects_urls_and_preserves_registered_boundary(monkeypatch) -> None:
    _clear_live_keys(monkeypatch)

    class Provider:
        provider_name = "fixture-llm"

        def complete_json(self, _prompt):
            return {
                "aliases": ["Oracle Cloud", "https://unregistered.example/source"],
                "event_queries": ["Oracle capacity financing", "https://unregistered.example/search"],
                "driver_queries": ["Oracle backlog conversion"],
            }

    plan = build_news_research_plan(
        _identity(),
        [_event()],
        llm_provider=Provider(),
        use_llm=True,
    )

    all_terms = plan.aliases + plan.event_queries + plan.driver_queries
    assert "Oracle Cloud" in plan.aliases
    assert not any("http://" in item or "https://" in item for item in all_terms)
    assert set(plan.allowed_source_ids) <= {
        item.source_id
        for item in build_news_intelligence_report(
            _identity(),
            [_event()],
            ExternalEvidenceBundle("ORCL", "Unavailable"),
            fetch_json=lambda _url, _timeout: {},
        ).registered_sources
        if item.enabled
    }


def test_china_adr_plan_has_multilingual_aliases(monkeypatch) -> None:
    _clear_live_keys(monkeypatch)
    plan = build_news_research_plan(
        _identity("BABA", "Alibaba Group Holding Limited"),
        [_event()],
    )

    assert plan.languages == ["en", "zh-Hans", "zh-Hant"]
    assert "9988 HK" in plan.aliases
    assert "\u963f\u91cc\u5df4\u5df4" in plan.aliases
