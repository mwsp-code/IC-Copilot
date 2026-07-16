from __future__ import annotations

from .analysis import annotate_change_importance
from .budget import build_budget_policy
from .claim_validation import validate_events
from .company_economics import attach_economic_context, build_company_economics
from .playbook_portfolio import build_playbook_portfolio
from .conviction_chain import build_conviction_chains
from .conviction_audit import build_conviction_audit
from .credit_lens import build_credit_lens
from .coverage_expansion import build_coverage_expansion_diagnostics
from .evidence_work_order import build_evidence_work_order
from .evidence_closure import execute_evidence_work_order
from .global_coverage import (
    build_canonical_metric_ontology,
    build_metric_resolution_audit,
    coverage_case_for,
    source_coverage_matrix_for,
)
from .historical_references import build_historical_references
from .ic_one_pager import build_ic_one_pager
from .manual_data import scan_manual_data_sources
from .market_capture_workflow import build_market_capture_readiness
from .market_implied import build_market_implied_expectations
from .metric_intelligence import build_metric_change_assessments
from .memo import build_dd_memo
from .models import (
    ConsensusPackage,
    CalibrationReport,
    ChangeEvent,
    Citation,
    CompanyIdentity,
    EarningsSurprise,
    EstimatePoint,
    EntityResolution,
    ExpectationsBridge,
    ExternalEvidenceBundle,
    FilingRecord,
    FinancialMetric,
    FinancialCoverage,
    GlobalPeerMetricObservation,
    LlmResearchAgentManifest,
    LlmTrendAnalysis,
    ManagementClaim,
    ManagementCrossCheck,
    ManagementDocument,
    ManagementSourcePackage,
    MeetingEvent,
    ProfilingReport,
    PeerMetricReadthrough,
    PeerMetricReadthroughSummary,
    RecommendationConsensus,
    TargetConsensus,
    TranscriptTurn,
    WisburgResearchLens,
    WatchlistStatus,
)
from .peers import peer_universe_for
from .pipeline import ResearchResult
from .providers import ConsensusAdapter, PriceReaction
from .research_store import ResearchStore
from .research_questions import build_research_questions
from .research_profiles import (
    build_historical_research_pack,
    event_identifier,
    profile_manifest_payload,
    resolve_research_profile,
)
from .research_modes import build_research_mode_suite
from .research_scout import build_research_scout_report
from .idea_engine import finalize_idea_research, generate_trade_ideas, ideas_with_changed_evidence_not_price_or_consensus
from .rigor import (
    build_data_quality_report,
    build_evidence_ledger,
    build_management_credibility,
    build_run_manifest,
)
from .thesis_synthesis import synthesize_ic_thesis
from .thesis_accountability import attach_thesis_audit_chains, build_event_workflow
from .thesis_clusters import build_thesis_clusters
from .thesis_validation import build_thesis_validation_report
from .valuation import build_valuation
from .company_model import build_company_model_workspace
from .causal_thesis_graph import build_causal_thesis_graphs
from .source_planner import build_source_plan
from .storytelling import build_story_presentation, demo_case_for


class _DemoConsensus(ConsensusAdapter):
    provider_name = "Demo"
    official_for_conviction = True

    def revision_since(self, ticker, event_date):
        return 0.0


_DEMO_COMPANIES = {
    "AAPL": ("0000320193", "Apple Inc.", "Active US reporting issuer"),
    "BABA": ("0001577552", "Alibaba Group Holding Limited", "US-listed ADR / foreign private issuer"),
    "TSLA": ("0001318605", "Tesla, Inc.", "Active US reporting issuer"),
    "GS": ("0000886982", "The Goldman Sachs Group, Inc.", "Active US reporting issuer"),
    "JPM": ("0000019617", "JPMorgan Chase & Co.", "Active US reporting issuer"),
    "NVDA": ("0001045810", "NVIDIA Corporation", "Active US reporting issuer"),
    "SPCX": ("", "Space Exploration Technologies Corp / SPX ticker ambiguity demo", "Entity ambiguity demo"),
}


_DEMO_PROFILES: dict[str, dict[str, object]] = {
    "AAPL": {
        "exchange": "NASDAQ",
        "currency": "USD",
        "period_end": "2026-03-28",
        "filed": "2026-05-01",
        "fiscal_period": "Q2",
        "current_form": "10-Q",
        "previous_form": "10-K",
        "metrics": [
            ("Revenue", 124_300_000_000, 112_900_000_000, 10.1, "USD"),
            ("Gross Profit", 58_900_000_000, 50_100_000_000, 17.6, "USD"),
            ("Operating Income", 36_200_000_000, 31_400_000_000, 15.3, "USD"),
            ("Operating Cash Flow", 41_800_000_000, 36_100_000_000, 15.8, "USD"),
            ("Capital Expenditure", 3_100_000_000, 2_900_000_000, 6.9, "USD"),
            ("Cash", 45_600_000_000, 28_200_000_000, 61.7, "USD"),
            ("Long-term Debt", 86_000_000_000, 95_000_000_000, -9.5, "USD"),
            ("Shares", 15_500_000_000, 15_900_000_000, -2.5, "shares"),
        ],
        "events": [
            ("margin", "Gross margin moved +3.0 pts", "Gross margin expanded as services mix and product economics improved.", "positive", 4, "Gross margin", "Revenue rose while gross profit grew faster, implying gross margin expansion."),
            ("capital_allocation", "Cash conversion and capital return strengthened", "Operating cash flow rose while diluted share count declined, improving capital-return capacity.", "positive", 4, "Cash flow and capital return", "Operating cash flow increased and diluted shares declined year over year."),
        ],
        "price": (200.0, 203.0),
        "targets": (236.0, 232.0, 285.0, 185.0, 34),
        "management": "Services mix, installed-base monetization, and disciplined capital return remain the principal earnings and valuation bridges.",
    },
    "NVDA": {
        "exchange": "NASDAQ",
        "currency": "USD",
        "period_end": "2026-04-26",
        "filed": "2026-05-20",
        "fiscal_period": "Q1",
        "current_form": "10-Q",
        "previous_form": "10-K",
        "metrics": [
            ("Revenue", 44_100_000_000, 26_000_000_000, 69.6, "USD"),
            ("Gross Profit", 31_000_000_000, 20_400_000_000, 52.0, "USD"),
            ("Operating Income", 25_200_000_000, 16_900_000_000, 49.1, "USD"),
            ("Net Income", 22_100_000_000, 14_900_000_000, 48.3, "USD"),
            ("Operating Cash Flow", 28_100_000_000, 15_300_000_000, 83.7, "USD"),
            ("Capital Expenditure", 1_800_000_000, 1_257_000_000, 43.2, "USD"),
            ("Goodwill", 20_900_000_000, 5_500_000_000, 280.0, "USD"),
            ("Inventory", 12_600_000_000, 10_100_000_000, 24.8, "USD"),
        ],
        "events": [
            ("financial_kpi", "Capital Expenditure changed +43.2%", "Capex accelerated; the investment may support capacity and product cadence or signal a more capital-intensive operating model.", "neutral", 5, "Capital Expenditure", "Capital expenditure increased to 1.8B USD from 1.3B USD in the comparable period."),
            ("financial_kpi", "Goodwill changed +280.0%", "Goodwill rose after acquisition activity; strategic assets may broaden the platform, while integration and impairment risk require separate testing.", "neutral", 5, "Goodwill", "Goodwill increased to 20.9B USD from 5.5B USD following acquisition accounting."),
        ],
        "price": (168.0, 171.0),
        "targets": (190.0, 188.0, 240.0, 130.0, 46),
        "management": "AI infrastructure demand remains strong, while supply commitments, acquisition integration, capex intensity, and gross-margin durability require explicit monitoring.",
    },
    "BABA": {
        "exchange": "NYSE ADR / HKEX 9988",
        "currency": "CNY",
        "period_end": "2026-03-31",
        "filed": "2026-05-20",
        "fiscal_period": "FY",
        "current_form": "20-F",
        "previous_form": "20-F",
        "metrics": [
            ("Revenue", 996_300_000_000, 941_200_000_000, 5.9, "CNY"),
            ("Gross Profit", 401_800_000_000, 363_200_000_000, 10.6, "CNY"),
            ("Operating Income", 142_600_000_000, 113_400_000_000, 25.7, "CNY"),
            ("Operating Cash Flow", 188_500_000_000, 182_700_000_000, 3.2, "CNY"),
            ("Capital Expenditure", 92_000_000_000, 54_000_000_000, 70.4, "CNY"),
            ("Cash", 354_000_000_000, 330_000_000_000, 7.3, "CNY"),
            ("Share Repurchases", 87_000_000_000, 65_000_000_000, 33.8, "CNY"),
            ("Shares", 1_880_000_000, 2_020_000_000, -6.9, "ADS"),
        ],
        "events": [
            ("financial_kpi", "Cloud and AI investment accelerated", "Higher capex supports cloud and AI capacity, but returns depend on revenue conversion and utilization.", "neutral", 5, "Cloud / AI investment", "Capital expenditure rose as the group expanded cloud and AI infrastructure."),
            ("capital_allocation", "Buybacks reduced ADS-equivalent share count", "Repurchases increased and ADS-equivalent shares declined after applying the ADR basis.", "positive", 4, "Buybacks and share normalization", "Repurchases increased while the normalized ADS-equivalent share count declined."),
        ],
        "price": (108.0, 112.0),
        "targets": (145.0, 142.0, 190.0, 95.0, 38),
        "management": "China commerce monetization, cloud and AI investment returns, international losses, buybacks, and policy risk are the central operating and valuation debates.",
    },
    "TSLA": {
        "exchange": "NASDAQ",
        "currency": "USD",
        "period_end": "2026-03-31",
        "filed": "2026-04-24",
        "fiscal_period": "Q1",
        "current_form": "10-Q",
        "previous_form": "10-K",
        "metrics": [
            ("Revenue", 25_500_000_000, 23_300_000_000, 9.4, "USD"),
            ("Gross Profit", 4_700_000_000, 3_200_000_000, 46.9, "USD"),
            ("Operating Income", 2_050_000_000, 1_170_000_000, 75.2, "USD"),
            ("Operating Cash Flow", 3_600_000_000, 2_200_000_000, 63.6, "USD"),
            ("Capital Expenditure", 2_900_000_000, 2_500_000_000, 16.0, "USD"),
            ("Deliveries", 462_000, 433_000, 6.7, "vehicles"),
        ],
        "events": [
            ("margin", "Automotive gross margin improved", "Gross profit rose faster than revenue; price, mix, incentives, credits, warranty, and input costs must explain the bridge.", "neutral", 5, "Automotive margin", "Gross profit increased faster than revenue, but durability requires the automotive margin bridge."),
            ("financial_kpi", "Deliveries changed +6.7%", "Delivery growth supports revenue but does not establish pricing or unit economics on its own.", "neutral", 4, "Deliveries and ASP", "Vehicle deliveries increased to 462,000 from 433,000."),
        ],
        "price": (320.0, 326.0),
        "targets": (355.0, 340.0, 500.0, 180.0, 41),
        "management": "Delivery growth must be reconciled with ASP, incentives, automotive gross margin, regulatory credits, and capex before direction is assigned.",
    },
    "GS": {
        "exchange": "NYSE",
        "currency": "USD",
        "period_end": "2026-03-31",
        "filed": "2026-04-15",
        "fiscal_period": "Q1",
        "current_form": "10-Q",
        "previous_form": "10-K",
        "metrics": [
            ("Revenue", 15_100_000_000, 14_200_000_000, 6.3, "USD"),
            ("Net Income", 4_500_000_000, 4_100_000_000, 9.8, "USD"),
            ("Investment Banking Fees", 2_300_000_000, 1_900_000_000, 21.1, "USD"),
            ("Trading Revenue", 7_800_000_000, 7_100_000_000, 9.9, "USD"),
            ("Provision for Credit Losses", 420_000_000, 310_000_000, 35.5, "USD"),
            ("CET1 Ratio", 14.8, 14.5, 2.1, "%"),
            ("ROTCE", 16.2, 15.1, 7.3, "%"),
        ],
        "events": [
            ("financial_kpi", "Investment-banking fee cycle improved", "Fee growth and trading resilience support ROTCE, subject to compensation, capital, and pipeline durability.", "positive", 5, "Investment banking and ROTCE", "Investment-banking fees rose 21.1% while CET1 remained above the demo policy threshold."),
            ("financial_kpi", "Credit provisions increased", "Higher provisions are a counter-signal that should be reconciled with consumer and private-credit exposure.", "negative", 4, "Credit costs", "Provision for credit losses increased 35.5% year over year."),
        ],
        "price": (720.0, 740.0),
        "targets": (790.0, 780.0, 900.0, 620.0, 26),
        "management": "The IC bridge should connect advisory and trading revenue to compensation, ROTCE, CET1, credit costs, and capital return.",
    },
}


def _demo_profile(ticker: str) -> dict[str, object]:
    return _DEMO_PROFILES.get(ticker, _DEMO_PROFILES["AAPL"])


def _demo_metrics(profile: dict[str, object]) -> list[FinancialMetric]:
    period_end = str(profile["period_end"])
    filed = str(profile["filed"])
    fiscal_period = str(profile["fiscal_period"])
    form = str(profile["current_form"])
    return [
        FinancialMetric(
            name=name,
            value=value,
            unit=unit,
            period_end=period_end,
            fiscal_period=fiscal_period,
            fiscal_year=2026,
            form=form,
            filed=filed,
            previous_value=previous,
            yoy_change_pct=change,
        )
        for name, value, previous, change, unit in profile["metrics"]
    ]


def _demo_events(profile: dict[str, object], filing_url: str) -> list[ChangeEvent]:
    filed = str(profile["filed"])
    period_end = str(profile["period_end"])
    form = str(profile["current_form"])
    events = []
    for category, title, summary, direction, severity, section, excerpt in profile["events"]:
        events.append(ChangeEvent(
            category=category,
            title=title,
            summary=summary,
            severity=severity,
            direction=direction,
            event_date=filed,
            source=f"Demo {form}",
            citations=[Citation(
                source=f"Demo {form}",
                url=filing_url,
                filed=filed,
                form=form,
                section=section,
                snippet=excerpt,
                source_tier=1,
            )],
            metrics={
                "event_period": period_end,
                "metric_family": section,
                "interpretation": summary,
                "research_work_order": f"Validate {section} against the historical series, peer operating metrics, management discussion, and valuation bridge.",
            },
        ))
    return events


_DEMO_PEER_METRICS: dict[str, tuple[str, list[tuple[str, list[tuple[str, float, float, float, str]]]]]] = {
    "AAPL": ("financial_kpi", [
        ("MSFT", [("Revenue", 82_900_000_000, 70_100_000_000, 18.3, "USD"), ("Gross Profit", 56_100_000_000, 48_200_000_000, 16.4, "USD"), ("Operating Cash Flow", 46_700_000_000, 37_100_000_000, 25.9, "USD")]),
        ("GOOGL", [("Revenue", 90_200_000_000, 80_500_000_000, 12.0, "USD"), ("Operating Income", 39_700_000_000, 30_600_000_000, 29.7, "USD"), ("Capital Expenditure", 35_700_000_000, 17_200_000_000, 107.6, "USD")]),
    ]),
    "NVDA": ("semiconductor_demand", [
        ("TSM", [("Revenue", 839_000_000_000, 673_500_000_000, 24.6, "TWD"), ("Gross Margin", 58.8, 53.1, 10.7, "%"), ("Capital Expenditure", 317_000_000_000, 252_000_000_000, 25.8, "TWD")]),
        ("AMD", [("Revenue", 9_200_000_000, 6_800_000_000, 35.3, "USD"), ("Gross Profit", 4_900_000_000, 3_600_000_000, 36.1, "USD"), ("Inventory", 7_100_000_000, 5_000_000_000, 42.0, "USD")]),
    ]),
    "BABA": ("china_consumer_cloud", [
        ("JD", [("Revenue", 301_100_000_000, 270_300_000_000, 11.4, "CNY"), ("Operating Income", 12_800_000_000, 10_500_000_000, 21.9, "CNY")]),
        ("PDD", [("Revenue", 112_600_000_000, 86_800_000_000, 29.7, "CNY"), ("Operating Income", 31_900_000_000, 25_400_000_000, 25.6, "CNY")]),
    ]),
    "TSLA": ("automotive_margin", [
        ("GM", [("Revenue", 47_500_000_000, 43_000_000_000, 10.5, "USD"), ("Automotive Margin", 9.4, 8.1, 16.0, "%"), ("Deliveries", 690_000, 650_000, 6.2, "vehicles")]),
        ("BYDDF", [("Revenue", 223_000_000_000, 171_000_000_000, 30.4, "CNY"), ("Gross Margin", 21.3, 20.1, 6.0, "%"), ("Deliveries", 1_000_000, 790_000, 26.6, "vehicles")]),
    ]),
    "GS": ("broker_bank", [
        ("MS", [("Investment Banking Fees", 2_000_000_000, 1_650_000_000, 21.2, "USD"), ("Trading Revenue", 6_900_000_000, 6_300_000_000, 9.5, "USD"), ("ROTCE", 17.1, 15.8, 8.2, "%")]),
        ("JPM", [("Investment Banking Fees", 2_400_000_000, 2_000_000_000, 20.0, "USD"), ("Trading Revenue", 8_700_000_000, 7_900_000_000, 10.1, "USD"), ("CET1 Ratio", 15.0, 14.7, 2.0, "%")]),
    ]),
}


def _attach_demo_peer_metrics(
    ticker: str,
    ideas: list,
    *,
    period_end: str,
    filed: str,
) -> dict[str, list[PeerMetricReadthrough]]:
    fixture = _DEMO_PEER_METRICS.get(ticker)
    if not fixture:
        return {}
    family, peer_rows = fixture
    readthroughs: list[PeerMetricReadthrough] = []
    for peer_ticker, metric_rows in peer_rows:
        observations = []
        for metric, value, previous, change, unit in metric_rows:
            citation = Citation(
                source=f"Demo official filing fixture: {peer_ticker}",
                url="https://www.sec.gov/edgar/search/",
                filed=filed,
                form="official filing / issuer results",
                section=metric,
                snippet=f"{peer_ticker} reported {metric} of {value:g} {unit} for the aligned demo period.",
                source_tier=1,
            )
            observations.append(GlobalPeerMetricObservation(
                observation_id=f"demo-{ticker}-{peer_ticker}-{metric.lower().replace(' ', '-')}",
                peer_ticker=peer_ticker,
                metric=metric,
                value=value,
                unit=unit,
                currency=unit if unit in {"USD", "CNY", "TWD"} else "",
                period_end=period_end,
                fiscal_period="Aligned demo period",
                source_document_id=f"demo-peer-{peer_ticker}",
                source_url=citation.url,
                source_type="official_filing_fixture",
                observed_at="2026-07-15T00:00:00+00:00",
                confidence="High",
                validation_status="validated_fixture",
                previous_value=previous,
                yoy_change_pct=change,
                citation=citation,
            ))
        present = [item.metric for item in observations]
        summary_parts = [
            f"{item.metric} {item.value:g} {item.unit} ({item.yoy_change_pct:+.1f}%)"
            for item in observations
        ]
        readthroughs.append(PeerMetricReadthrough(
            peer_ticker=peer_ticker,
            metric_family=family,
            status="available",
            relation="Relevant operating evidence",
            summary=f"{peer_ticker} aligned read-through: " + "; ".join(summary_parts),
            fiscal_alignment="Aligned demo period",
            observations=observations,
            source_tier=1,
            required_metrics=present,
            present_metrics=present,
            missing_metrics=[],
            acceptance_criteria=["Same driver family", "Aligned period", "Source-linked operating metric"],
            falsification_tests=["Peer move reflects a different business mix", "Metric reverses in the next comparable period"],
        ))
    summary = PeerMetricReadthroughSummary(
        status="Available",
        score=86,
        summary=f"{len(readthroughs)} peer(s) provide aligned operating evidence for {family}; stock-price sympathy remains a separate test.",
        total_peers=len(readthroughs),
        operating_metric_peers=len(readthroughs),
        metric_families=[family],
        confirmations=[row.summary for row in readthroughs],
        data_gaps=["Demo peer metrics are illustrative, sanitized fixtures; run live research for current values."],
        next_actions=["Refresh the same metric family from current official filings before acting."],
        stage_impact="Supports Research-Ready when the company causal bridge is independently validated.",
    )
    for idea in ideas:
        idea.peer_metric_readthrough = readthroughs
        idea.peer_metric_summary = summary
    return {idea.idea_id: readthroughs for idea in ideas}


def demo_result(ticker: str = "AAPL") -> ResearchResult:
    ticker = ticker.upper()
    if ticker == "SPXC":
        ticker = "SPCX"
    profile = _demo_profile(ticker)
    is_baba = ticker == "BABA"
    demo_case = demo_case_for(ticker)
    cik, company_name, listing_status = _DEMO_COMPANIES.get(
        ticker,
        ("0000320193", f"{ticker} Demo Company", "Demo US-listed reporting issuer"),
    )
    identity = CompanyIdentity(
        ticker=ticker,
        cik=cik,
        name=company_name,
        exchange=str(profile["exchange"]),
    )
    filing_url = f"https://www.sec.gov/Archives/edgar/data/{cik.lstrip('0') or '0000000'}/"
    annual_form = str(profile["current_form"])
    previous_form = str(profile["previous_form"])
    current_report_form = "6-K" if is_baba else "8-K"
    currency = str(profile["currency"])
    filed = str(profile["filed"])
    period_end = str(profile["period_end"])
    accession_prefix = cik or "0000000000"
    primary_prefix = ticker.lower()
    filings = [
        FilingRecord(
            form=annual_form,
            accession=f"{accession_prefix}-26-000001",
            filing_date=filed,
            report_date=period_end,
            primary_doc=f"{primary_prefix}-2026-current.htm",
            description="Annual report" if is_baba else "Quarterly report",
            url=filing_url,
        ),
        FilingRecord(
            form=previous_form,
            accession=f"{accession_prefix}-25-000001",
            filing_date="2025-11-01",
            report_date="2025-09-30",
            primary_doc=f"{primary_prefix}-2025-annual.htm",
            description="Annual report",
            url=filing_url,
        ),
        FilingRecord(
            form=current_report_form,
            accession=f"{accession_prefix}-26-000002",
            filing_date=filed,
            report_date=filed,
            primary_doc=f"{primary_prefix}-{current_report_form.lower()}.htm",
            description="Interim results furnished report" if is_baba else "Results and capital return update",
            url=filing_url,
        ),
    ]
    metrics = _demo_metrics(profile)
    events = _demo_events(profile, filing_url)
    events = annotate_change_importance(events)
    demo_peer_universe = peer_universe_for(ticker)
    manual_data_status = scan_manual_data_sources(ticker)
    company_economics = build_company_economics(
        identity,
        metrics,
        events,
        demo_peer_universe,
        manual_data_status,
    )
    credit_lens = build_credit_lens(identity, metrics)
    attach_economic_context(events, company_economics)
    validated_claims, llm_extraction_manifest = validate_events(identity, events)
    start_price, latest_price = profile["price"]
    price = PriceReaction(
        ticker=ticker.upper(),
        event_date=filed,
        start_price=start_price,
        latest_price=latest_price,
        reaction_pct=round((latest_price / start_price - 1.0) * 100.0, 2),
        source="Demo price series",
    )
    target_mean, target_median, target_high, target_low, analyst_count = profile["targets"]
    target = TargetConsensus(
        ticker=ticker,
        as_of="2026-07-15",
        target_mean=target_mean,
        target_median=target_median,
        target_high=target_high,
        target_low=target_low,
        analyst_count=analyst_count,
        current_price=latest_price,
        implied_upside_pct=round((target_mean / latest_price - 1.0) * 100.0, 1),
        dispersion_pct=round((target_high - target_low) / target_mean * 100.0, 1),
        provider_timestamp="2026-07-15",
        freshness_days=0,
        source="Demo",
    )
    consensus = ConsensusPackage(
        ticker=ticker,
        provider="Demo",
        status="Available",
        target=target,
        recommendations=RecommendationConsensus(
            ticker=ticker, as_of="2026-07-15", strong_buy=12, buy=14,
            hold=7, sell=1, strong_sell=0, consensus_label="Buy", source="Demo",
        ),
        estimates=[
            EstimatePoint(
                ticker, "2026-07-15", "EPS", "2027-03-31", "annual",
                8.4, 9.2, 7.5, 28, "USD", "Demo",
            ),
            EstimatePoint(
                ticker, "2026-07-15", "Revenue", "2027-03-31", "annual",
                next(metric.value for metric in metrics if metric.name == "Revenue")
                * (1.1 if str(profile["fiscal_period"]) == "FY" else 4.4),
                next(metric.value for metric in metrics if metric.name == "Revenue")
                * (1.2 if str(profile["fiscal_period"]) == "FY" else 4.8),
                next(metric.value for metric in metrics if metric.name == "Revenue")
                * (1.0 if str(profile["fiscal_period"]) == "FY" else 4.0),
                25, currency, "Demo",
            ),
        ],
        surprises=[EarningsSurprise(ticker, period_end, 2.05, 1.92, 6.8, "Demo")],
    )
    expectations = ExpectationsBridge(
        status="Available",
        headline="EPS beat expectations by 6.8%.",
        point_in_time_note="Demo uses a point-in-time pre-event estimate.",
    )
    valuation = build_valuation(identity, metrics, consensus, price.latest_price)
    ideas = generate_trade_ideas(
        identity, events, price, _DemoConsensus(), metrics=metrics,
        price_reactions={filed: price},
    )
    demo_peer_metric_readthrough = _attach_demo_peer_metrics(
        ticker,
        ideas,
        period_end=period_end,
        filed=filed,
    )
    evidence = build_evidence_ledger(ticker, ideas, events)
    gate_results = finalize_idea_research(ideas, valuation, evidence, price.latest_price)
    attach_thesis_audit_chains(ideas, valuation)
    build_conviction_chains(ideas, company_economics, valuation, consensus)
    thesis_clusters = build_thesis_clusters(ideas, company_economics, valuation, consensus)
    data_quality = build_data_quality_report(events, ideas, consensus)
    historical_references = build_historical_references(ideas, ResearchStore())
    management = build_management_credibility(expectations, metrics, [])
    observed_at = "2026-07-15T00:00:00+00:00"
    management_doc = ManagementDocument(
        document_id=f"demo-mgmt-{ticker}",
        ticker=ticker,
        source_type="earnings_call_transcript",
        provider="Demo transcript",
        title=f"{ticker} demo earnings call",
        url=filing_url,
        event_date=filed,
        fiscal_period=f"2026{profile['fiscal_period']}",
        source_tier=2,
        observed_at=observed_at,
        excerpt=str(profile["management"]),
    )
    management_turn = TranscriptTurn(
        turn_id=f"demo-turn-{ticker}",
        document_id=management_doc.document_id,
        speaker="CFO",
        role="Chief Financial Officer",
        section="prepared_remarks",
        text=str(profile["management"]),
        turn_index=0,
    )
    claim_citation = Citation(
        source="Demo transcript",
        url=filing_url,
        filed=filed,
        form="earnings_call_transcript",
        section="prepared_remarks",
        snippet=management_turn.text,
        source_tier=2,
    )
    management_claim = ManagementClaim(
        claim_id=f"demo-claim-{ticker}",
        ticker=ticker,
        document_id=management_doc.document_id,
        claim_type="guidance_shift",
        statement=management_turn.text,
        source_type="earnings_call_transcript",
        source_tier=2,
        event_date=filed,
        citation=claim_citation,
        speaker="CFO",
        metric="margin",
        direction="positive",
        machine_readable=True,
        status="Confirmed",
    )
    management_check = ManagementCrossCheck(
        check_id=f"demo-cross-{ticker}",
        claim_id=management_claim.claim_id,
        ticker=ticker,
        status="Confirmed",
        check_type="financial_fact",
        summary="Demo companyfacts show operating margin and cash flow moved in the same direction.",
        source_type="SEC companyfacts",
        source_tier=1,
        materiality=3,
        citation=claim_citation,
    )
    management_sources = ManagementSourcePackage(
        ticker=ticker,
        status="Available",
        documents=[management_doc],
        transcript_turns=[management_turn],
        claims=[management_claim],
        meeting_events=[
            MeetingEvent(
                event_id=f"demo-meeting-{ticker}",
                ticker=ticker,
                document_id=management_doc.document_id,
                event_type="shareholder_vote_signal",
                description="Demo annual meeting materials show routine governance votes.",
                event_date=filed,
                citation=claim_citation,
                source_tier=1,
            )
        ],
        cross_checks=[management_check],
    )
    calibration = CalibrationReport(
        status="Uncalibrated",
        sample_size=0,
        minimum_sample_size=30,
        hit_rate_pct=None,
        brier_score=None,
        mean_absolute_error_pct=None,
        data_gaps=["Demo contains no resolved historical outcomes."],
    )
    manifest = build_run_manifest(ticker, filings, metrics, events, consensus, [])
    demo_resolution = EntityResolution(
        ticker=ticker,
        name=identity.name,
        cik=identity.cik,
        exchange=str(profile["exchange"]),
        sic=None,
        sic_description=None,
        listing_status=listing_status,
        reporting_forms=sorted({filing.form for filing in filings}),
        adr_ratio=8.0 if is_baba else 1.0,
        similar_tickers=["SPXC"] if ticker == "SPCX" else [],
        warning=(
            "Demo warning: SPCX can be confused with SPXC. Resolve entity identity before relying on financial coverage."
            if ticker == "SPCX" else None
        ),
    )
    demo_coverage = FinancialCoverage(
        status="registration_only" if ticker == "SPCX" else "available",
        reason=(
            "Demo shows entity ambiguity and limited periodic XBRL coverage; verify SPCX versus SPXC before research."
            if ticker == "SPCX" else
            "Demo structured financial metrics are available."
        ),
        source="Demo SEC companyfacts",
        periodic_forms=sorted({annual_form}),
        metrics_count=len(metrics),
    )
    external_evidence = ExternalEvidenceBundle(
        ticker=ticker,
        status="Unavailable",
        evidence=[],
        provider_statuses=[],
        data_gaps=["Demo workflow does not fetch external evidence providers."],
    )
    wisburg_lens = WisburgResearchLens(
        ticker=ticker,
        status="Unavailable",
        observed_at=manifest.generated_at,
        caveats=["Demo workflow does not fetch Wisburg external research."],
        provider_status="Unavailable",
    )
    thesis_validation = build_thesis_validation_report(
        ideas,
        evidence,
        consensus,
        valuation,
        management_sources,
        external_evidence,
        historical_references,
    )
    source_plan = build_source_plan(identity, events, validated_claims, management_sources)
    research_questions = build_research_questions(ideas, thesis_clusters, company_economics, source_plan)
    market_capture_readiness = build_market_capture_readiness(
        ticker,
        ideas,
        consensus,
        manual_data_status,
        source_plan,
    )
    coverage_expansion = build_coverage_expansion_diagnostics(
        identity,
        demo_resolution,
        demo_coverage,
        company_economics,
        consensus,
        valuation,
        ideas,
        thesis_clusters,
        source_plan,
    )
    coverage_case = coverage_case_for(ticker, identity, demo_resolution, filings)
    source_coverage_matrix = source_coverage_matrix_for(coverage_case, filings)
    metric_resolution_audit = build_metric_resolution_audit(
        ticker,
        metrics,
        coverage_case,
        build_canonical_metric_ontology(),
    )
    evidence_work_order = build_evidence_work_order(
        thesis_validation,
        market_capture_readiness,
        research_questions,
        source_plan,
        coverage_expansion,
        coverage_case,
        source_coverage_matrix,
        metric_resolution_audit,
    )
    evidence_closure = execute_evidence_work_order(
        ticker,
        evidence_work_order,
        filings=filings,
        metrics=metrics,
        validated_claims=validated_claims,
        management_sources=management_sources,
        external_evidence=external_evidence,
        consensus=consensus,
        ideas=ideas,
        peer_metric_readthrough=demo_peer_metric_readthrough,
        primary_observations=[],
        corroboration_results=[],
    )
    company_model = build_company_model_workspace(identity, metrics, valuation)
    market_implied_expectations = build_market_implied_expectations(
        identity, metrics, price.latest_price, valuation, company_model,
    )
    causal_thesis_graphs = build_causal_thesis_graphs(
        ticker, ideas, validated_claims, company_model, valuation,
        market_implied_expectations, evidence_closure,
    )
    research_modes = build_research_mode_suite(
        ticker, metrics, events, ideas, management_sources, evidence_closure,
    )
    selected_profile = resolve_research_profile("adaptive_ic")
    for event in events:
        event.metrics["event_id"] = event_identifier(event)
    historical_research = build_historical_research_pack(
        ticker,
        selected_profile,
        filings,
        management_sources,
        events,
        parsed_filing_accessions={
            citation.accession
            for event in events for citation in event.citations
            if citation.accession
        },
    )
    metric_assessments = build_metric_change_assessments(
        events, metrics, historical_research.selected_event_ids,
    )
    playbook_portfolio = build_playbook_portfolio(
        identity, company_economics, events, management_sources,
    )
    profile_summary, history_summary = profile_manifest_payload(selected_profile, historical_research)
    manifest.research_profile_summary = profile_summary
    manifest.effective_history_summary = history_summary
    research_scout = build_research_scout_report(
        identity,
        ideas,
        company_economics,
        demo_peer_universe,
        source_plan,
        evidence_work_order,
    )
    event_workflow = build_event_workflow(ticker, filings, ideas, source_plan, consensus)
    llm_research_manifest = LlmResearchAgentManifest(
        provider="deterministic",
        model="none",
        prompt_version="global-peer-research-agent-v1",
        generated_at=manifest.generated_at,
        status="Not required",
        messages=["Demo run did not require global peer LLM-assistant lanes."],
        redacted_config={"llm_used": "false"},
        allowed_roles=[
            "source_planning_from_registered_source_types",
            "official_document_triage",
            "provisional_metric_extraction_drafts",
            "validated_trend_summarization",
            "IC_narrative_from_validated_claims_only",
        ],
        prohibited_actions=[
            "free_browsing_or_arbitrary_trusted_URL_creation",
            "inventing_facts_citations_targets_or_probabilities",
            "promoting_candidates_or_overriding_deterministic_gates",
            "treating_external_research_or_news_as_standalone_high_conviction_proof",
            "persisting_raw_vendor_payloads_or_API_keys",
        ],
        source_registry_version="source-registry-v1",
        deterministic_executor="registered_source_adapters_and_validators",
        validation_gates=[
            "allowed_source_type_filter",
            "source_url_and_citation_required",
            "metric_period_unit_currency_validation",
            "primary_source_or_explicit_gap_for_promotion",
            "no_high_conviction_without_evidence_gates",
        ],
    )
    llm_trend_analysis = LlmTrendAnalysis(
        status="Available" if demo_peer_metric_readthrough else "Unavailable",
        summary=(
            "Demo operating peer metrics are period-aligned and source-linked; the narrative remains deterministic."
            if demo_peer_metric_readthrough
            else "Demo run has no validated global peer metric observations."
        ),
        data_gaps=(
            ["Refresh sanitized demo observations with live official-source data before acting."]
            if demo_peer_metric_readthrough
            else ["Run live research to populate global peer trend analysis."]
        ),
    )
    for idea in ideas:
        idea.llm_contribution = {
            "source_planning": llm_research_manifest.status,
            "document_triage": "not_required",
            "metric_extraction": "not_required",
            "trend_analysis": llm_trend_analysis.status,
            "final_synthesis": "guardrailed_by_evidence_sufficiency",
        }
    budget_policy = build_budget_policy("Lean", consensus, external_evidence, llm_enabled=False)
    thesis_synthesis = synthesize_ic_thesis(
        identity,
        ideas,
        evidence,
        valuation,
        data_quality,
        management,
        expectations,
        management_sources,
        external_evidence,
        calibration,
        historical_references=historical_references,
        thesis_validation=thesis_validation,
        provider=None,
        budget_policy=budget_policy,
        manual_data_status=manual_data_status,
        company_economics=company_economics,
        credit_lens=credit_lens,
        thesis_clusters=thesis_clusters,
        research_questions=research_questions,
        research_scout=research_scout,
        validated_claims=validated_claims,
        source_plan=source_plan,
        llm_extraction_manifest=llm_extraction_manifest,
        wisburg_lens=wisburg_lens,
        evidence_work_order=evidence_work_order,
    )
    ic_one_pager = build_ic_one_pager(
        identity,
        thesis_synthesis.thesis_brief,
        thesis_synthesis.thesis_critique,
        thesis_synthesis.evidence_sufficiency,
        ideas,
        valuation,
        thesis_validation,
        evidence_work_order,
        company_economics,
        credit_lens,
        thesis_clusters,
        thesis_synthesis.action_plan,
    )
    conviction_audit = build_conviction_audit(
        ideas,
        evidence,
        data_quality,
        consensus,
        valuation,
        management_sources,
        external_evidence,
        historical_references,
        calibration,
        thesis_synthesis.llm_manifest,
        thesis_synthesis.llm_reviews,
        thesis_synthesis.llm_comparison,
        company_economics=company_economics,
        market_capture_readiness=market_capture_readiness,
        thesis_brief=thesis_synthesis.thesis_brief,
        thesis_clusters=thesis_clusters,
        research_questions=research_questions,
        credit_lens=credit_lens,
        llm_research_manifest=llm_research_manifest,
    )
    memo = build_dd_memo(
        identity, filings, metrics, events, ideas, consensus, expectations, valuation,
        evidence, data_quality, management, calibration, manifest,
        demo_resolution, demo_coverage, demo_peer_universe, management_sources,
        external_evidence,
        thesis_synthesis.thesis_brief,
        ic_one_pager,
        thesis_synthesis.thesis_critique,
        thesis_synthesis.evidence_sufficiency,
        thesis_synthesis.action_plan,
        thesis_synthesis.llm_manifest,
        thesis_synthesis.llm_reviews,
        thesis_synthesis.llm_comparison,
        thesis_synthesis.language_audit,
        historical_references,
        thesis_validation,
        conviction_audit,
        budget_policy,
        manual_data_status,
        company_economics,
        credit_lens,
        thesis_clusters,
        research_questions,
        research_scout,
        market_capture_readiness,
        validated_claims,
        source_plan,
        llm_extraction_manifest,
        llm_research_manifest=llm_research_manifest,
        event_workflow=event_workflow,
        wisburg_lens=wisburg_lens,
        coverage_expansion=coverage_expansion,
        evidence_work_order=evidence_work_order,
        coverage_case=coverage_case,
        source_coverage_matrix=source_coverage_matrix,
        metric_resolution_audit=metric_resolution_audit,
        evidence_closure=evidence_closure,
        causal_thesis_graphs=causal_thesis_graphs,
        market_implied_expectations=market_implied_expectations,
        company_model=company_model,
        research_modes=research_modes,
    )
    return ResearchResult(
        identity=identity,
        filings=filings,
        metrics=metrics,
        events=events,
        ideas=ideas,
        wow_ideas=ideas_with_changed_evidence_not_price_or_consensus(ideas),
        memo_markdown=memo,
        price_reaction=price,
        transcript_count=len(management_sources.transcript_turns),
        coverage_notes=(
            [
                "Foreign private issuer / ADR coverage: demo uses 20-F annual reports and 6-K furnished reports.",
                "6-K reports are less standardized than 10-Q/8-K filings, so extraction quality varies by issuer exhibit.",
            ]
            if is_baba
            else [
                "Entity-resolution demo: SPCX/SPXC ambiguity must be resolved before financial facts are trusted."
            ]
            if ticker == "SPCX"
            else []
        ),
        consensus=consensus,
        expectations_bridge=expectations,
        valuation=valuation,
        watchlist_status=WatchlistStatus("default", ticker, False),
        active_alerts=[],
        data_quality=data_quality,
        evidence_ledger=evidence,
        management_credibility=management,
        run_manifest=manifest,
        calibration=calibration,
        price_reactions_by_event={filed: price},
        entity_resolution=demo_resolution,
        financial_coverage=demo_coverage,
        peer_universe=demo_peer_universe,
        peer_reactions={},
        idea_gate_results=gate_results,
        event_window_reactions={},
        management_sources=management_sources,
        external_evidence=external_evidence,
        thesis_brief=thesis_synthesis.thesis_brief,
        ic_one_pager=ic_one_pager,
        thesis_critique=thesis_synthesis.thesis_critique,
        evidence_sufficiency=thesis_synthesis.evidence_sufficiency,
        action_plan=thesis_synthesis.action_plan,
        llm_run_manifest=thesis_synthesis.llm_manifest,
        llm_reviews=thesis_synthesis.llm_reviews,
        llm_comparison=thesis_synthesis.llm_comparison,
        language_audit=thesis_synthesis.language_audit,
        historical_references=historical_references,
        thesis_validation=thesis_validation,
        conviction_audit=conviction_audit,
        budget_policy=budget_policy,
        manual_data_status=manual_data_status,
        company_economics=company_economics,
        credit_lens=credit_lens,
        thesis_clusters=thesis_clusters,
        research_questions=research_questions,
        research_scout=research_scout,
        market_capture_readiness=market_capture_readiness,
        validated_claims=validated_claims,
        source_plan=source_plan,
        llm_extraction_manifest=llm_extraction_manifest,
        event_workflow=event_workflow,
        wisburg_lens=wisburg_lens,
        profiling=ProfilingReport(
            "Demo",
            notes=["Demo payload is static and was not stage-profiled."],
        ),
        coverage_expansion=coverage_expansion,
        news_claims=[],
        primary_source_observations=[],
        source_corroboration_results=[],
        causal_bridges=[],
        global_peer_coverage={},
        peer_metric_readthrough=demo_peer_metric_readthrough,
        llm_research_manifest=llm_research_manifest,
        llm_trend_analysis=llm_trend_analysis,
        evidence_work_order=evidence_work_order,
        coverage_case=coverage_case,
        source_coverage_matrix=source_coverage_matrix,
        metric_resolution_audit=metric_resolution_audit,
        evidence_closure=evidence_closure,
        causal_thesis_graphs=causal_thesis_graphs,
        market_implied_expectations=market_implied_expectations,
        company_model=company_model,
        research_modes=research_modes,
        research_profile=selected_profile,
        historical_research=historical_research,
        metric_assessments=metric_assessments,
        playbook_portfolio=playbook_portfolio,
        **build_story_presentation(
            identity=identity,
            ideas=ideas,
            one_pager=ic_one_pager,
            thesis_brief=thesis_synthesis.thesis_brief,
            thesis_critique=thesis_synthesis.thesis_critique,
            validation=thesis_validation,
            validated_claims=validated_claims,
            economics=company_economics,
            credit_lens=credit_lens,
            valuation=valuation,
            market_capture=market_capture_readiness,
            work_order=evidence_work_order,
            metric_audit=metric_resolution_audit,
            entity_resolution=demo_resolution,
            financial_coverage=demo_coverage,
            demo_case=demo_case,
        ),
    )
