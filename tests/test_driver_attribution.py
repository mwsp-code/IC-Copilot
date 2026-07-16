from __future__ import annotations

import unittest

from equity_research.driver_attribution import build_driver_attribution
from equity_research.idea_engine import finalize_idea_research, generate_trade_ideas
from equity_research.models import (
    ChangeEvent,
    Citation,
    CompanyIdentity,
    EventWindowReaction,
    ExternalEvidence,
    ExternalEvidenceBundle,
    ValuationBridgeStep,
    ValuationCase,
    ValuationResult,
)
from equity_research.providers import ConsensusAdapter, PriceReaction
from equity_research.rigor import build_evidence_ledger


class _FlatConsensus(ConsensusAdapter):
    official_for_conviction = True

    def revision_since(self, ticker, event_date):
        return 0.0


class DriverAttributionTests(unittest.TestCase):
    def test_company_specific_residual_is_classified(self) -> None:
        idea = _idea()
        attribution = build_driver_attribution(
            idea,
            _reaction(raw=5.0, market=4.2, sector=3.9, beta=4.0),
            _empty_external(),
            None,
        )
        self.assertEqual(attribution.classification, "Company-specific")
        self.assertEqual(attribution.raw_return_pct, 5.0)
        self.assertTrue(any(factor.driver_type == "company_evidence" for factor in attribution.factors))
        self.assertIsNotNone(attribution.waterfall)
        self.assertAlmostEqual(
            attribution.waterfall.raw_return_pct,
            attribution.waterfall.explained_pct + attribution.waterfall.residual_pct,
            places=3,
        )
        self.assertAlmostEqual(attribution.waterfall.balance_check_pct, 0.0)
        self.assertEqual(attribution.attribution_readiness, "Price-ready / context incomplete")
        self.assertGreater(attribution.attribution_quality_score, 0)
        quality = {item.area: item for item in attribution.attribution_quality}
        self.assertEqual(quality["Price anchor"].status, "Passed")
        self.assertEqual(quality["Expectations and consensus"].status, "Passed")
        self.assertEqual(quality["Positioning, liquidity, and options"].status, "Missing")
        self.assertTrue(any("Classification: Company-specific" in item for item in attribution.attribution_summary))
        self.assertTrue(any("Residual company-specific move" in item for item in attribution.attribution_summary))
        self.assertTrue(any("Residual" in item for item in attribution.classification_evidence))
        self.assertTrue(any("Company-specific label weakens" in item for item in attribution.falsification_tests))
        self.assertTrue(any("peer operating-metric" in item for item in attribution.next_attribution_checks))

    def test_attribution_quality_flags_missing_consensus_revision(self) -> None:
        idea = _idea()
        idea.market_capture.consensus_revision_pct = None
        attribution = build_driver_attribution(
            idea,
            _reaction(raw=5.0, market=4.2, sector=3.9, beta=4.0),
            _empty_external(),
            None,
        )

        quality = {item.area: item for item in attribution.attribution_quality}
        self.assertEqual(quality["Expectations and consensus"].status, "Missing")
        self.assertIn("Missing consensus blocks", quality["Expectations and consensus"].stage_impact)

    def test_market_driven_move_is_classified(self) -> None:
        idea = _idea()
        attribution = build_driver_attribution(
            idea,
            _reaction(raw=5.0, market=0.3, sector=0.4, beta=0.2),
            _empty_external(),
            None,
        )
        self.assertEqual(attribution.classification, "Market-driven")
        self.assertTrue(any("Market-relative" in item for item in attribution.classification_evidence))
        self.assertTrue(any("Market-driven label weakens" in item for item in attribution.falsification_tests))

    def test_sector_driven_move_is_classified(self) -> None:
        idea = _idea()
        attribution = build_driver_attribution(
            idea,
            _reaction(raw=5.0, market=4.4, sector=0.2, beta=4.1),
            _empty_external(),
            None,
        )
        self.assertEqual(attribution.classification, "Sector-driven")

    def test_macro_evidence_uses_no_lookahead(self) -> None:
        idea = _idea(event_date="2026-06-01")
        external = ExternalEvidenceBundle(
            "AAPL",
            "Available",
            [
                _macro("DGS10", "2026-05-30", 0.30),
                _macro("BAA10Y", "2026-06-05", 0.50),
            ],
            [],
            [],
        )
        attribution = build_driver_attribution(
            idea,
            _reaction(raw=3.0, market=2.0, sector=2.0, beta=1.5, event_date="2026-06-01"),
            external,
            None,
        )
        self.assertEqual([item.metric_name for item in attribution.macro_context], ["DGS10"])
        self.assertEqual(attribution.classification, "Macro-sensitive")
        self.assertEqual([item.metric_name for item in attribution.macro_calendar_context], ["DGS10"])
        self.assertTrue(any("Lookahead-safe macro" in item for item in attribution.classification_evidence))
        self.assertTrue(any("Macro-sensitive label weakens" in item for item in attribution.falsification_tests))

    def test_china_adr_benchmark_context_is_added_for_seeded_adr(self) -> None:
        idea = _idea(event_date="2026-06-01")
        attribution = build_driver_attribution(
            idea,
            _reaction(raw=-5.0, market=-3.0, sector=-1.0, beta=-2.5, event_date="2026-06-01", sector_ticker="KWEB"),
            _empty_external("BABA"),
            None,
            ticker="BABA",
        )
        factor = next(item for item in attribution.factors if item.driver_type == "china_adr_context")
        self.assertIn("KWEB", factor.explanation)
        self.assertEqual(factor.source_tier, 3)

    def test_macro_evidence_must_be_marked_lookahead_safe(self) -> None:
        idea = _idea(event_date="2026-06-01")
        unsafe = _macro("DGS10", "2026-05-30", 0.30)
        unsafe.lookahead_safe = False
        external = ExternalEvidenceBundle("AAPL", "Available", [unsafe], [], [])
        attribution = build_driver_attribution(
            idea,
            _reaction(raw=3.0, market=2.0, sector=2.0, beta=1.5, event_date="2026-06-01"),
            external,
            None,
        )
        self.assertEqual(attribution.macro_context, [])
        self.assertTrue(
            any("No point-in-time macro factor" in gap for gap in attribution.data_gaps)
        )

    def test_factor_context_is_point_in_time_and_contextual(self) -> None:
        idea = _idea(event_date="2026-06-01")
        external = ExternalEvidenceBundle(
            "AAPL",
            "Available",
            [
                _factor("Momentum", "2026-05-30", 1.2),
                _factor("Value", "2026-06-05", -0.4),
            ],
            [],
            [],
        )
        attribution = build_driver_attribution(
            idea,
            _reaction(raw=4.0, market=3.0, sector=2.5, beta=2.8, event_date="2026-06-01"),
            external,
            None,
        )
        self.assertEqual([item.factor_name for item in attribution.factor_context], ["Momentum"])
        style_components = [
            item for item in attribution.waterfall.components
            if item.component_type == "style_factor"
        ]
        self.assertEqual(style_components[0].contribution_pct, None)

    def test_options_unavailable_is_explicit_not_inferred(self) -> None:
        attribution = build_driver_attribution(
            _idea(),
            _reaction(raw=2.5, market=1.5, sector=1.0, beta=1.2),
            _empty_external(),
            None,
        )
        self.assertEqual(attribution.options_context[0].status, "Unavailable")
        self.assertIsNone(attribution.options_context[0].implied_move_pct)
        quality = {item.area: item for item in attribution.attribution_quality}
        self.assertTrue(any("do not infer options" in item for item in quality["Positioning, liquidity, and options"].gaps))
        self.assertTrue(any("Options-implied move is unavailable" in gap for gap in attribution.data_gaps))
        self.assertTrue(any("contract-level options" in item for item in attribution.next_attribution_checks))

    def test_third_party_narrative_only_cannot_be_high_conviction(self) -> None:
        event = ChangeEvent(
            "narrative_saturation",
            "GDELT narrative spike",
            "Third-party narrative volume increased.",
            4,
            "positive",
            "2026-06-01",
            "GDELT",
            [Citation("GDELT", "https://example.test/gdelt", snippet="Volume spike.", source_tier=4)],
        )
        ideas = generate_trade_ideas(
            CompanyIdentity("AAPL", "0000320193", "Apple Inc."),
            [event],
            PriceReaction("AAPL", "2026-06-01", 100, 104, 4, "Fixture"),
            _FlatConsensus(),
        )
        evidence = build_evidence_ledger("AAPL", ideas, [event], [])
        gates = finalize_idea_research(ideas, _valuation(), evidence, 100)
        self.assertFalse(gates[0].high_conviction)
        self.assertEqual(ideas[0].score.score_cap, 55)


def _idea(event_date: str = "2026-06-01"):
    event = ChangeEvent(
        "margin",
        "Gross margin moved +3 pts",
        "Gross margin expanded.",
        4,
        "positive",
        event_date,
        "SEC Companyfacts",
        [Citation("SEC XBRL", "https://www.sec.gov/facts", section="Gross margin", snippet="Gross margin expanded.", source_tier=1)],
    )
    return generate_trade_ideas(
        CompanyIdentity("AAPL", "0000320193", "Apple Inc."),
        [event],
        PriceReaction("AAPL", event_date, 100, 105, 5, "Fixture"),
        _FlatConsensus(),
    )[0]


def _reaction(
    raw: float,
    market: float,
    sector: float,
    beta: float,
    event_date: str = "2026-06-01",
    sector_ticker: str = "XLK",
) -> EventWindowReaction:
    return EventWindowReaction(
        ticker="AAPL",
        event_id=f"margin:{event_date}:0",
        event_date=event_date,
        event_timestamp=None,
        anchor_date=event_date,
        prior_close=100,
        source="Fixture prices",
        status="available",
        benchmark_ticker="SPY",
        sector_benchmark_ticker=sector_ticker,
        raw_returns={"5d": raw},
        market_relative_returns={"5d": market},
        sector_relative_returns={"5d": sector},
        beta_adjusted_returns={"5d": beta},
    )


def _macro(series_id: str, as_of: str, change: float) -> ExternalEvidence:
    return ExternalEvidence(
        provider="FRED macro",
        source_type="macro_factor",
        title=series_id,
        summary=f"{series_id} changed {change:+.2f}.",
        observed_at="2026-06-02T00:00:00+00:00",
        source_as_of=as_of,
        source_tier=2,
        official=True,
        confidence="Medium",
        metric_name=series_id,
        metric_value=change,
        direction="positive" if change > 0 else "negative",
        tags=["macro"],
        disqualifies_high_conviction=False,
    )


def _factor(name: str, as_of: str, value: float) -> ExternalEvidence:
    return ExternalEvidence(
        provider="Ken French factors",
        source_type="factor_return",
        title=f"{name} daily factor return",
        summary=f"{name} was {value:+.2f}%.",
        observed_at="2026-06-02T00:00:00+00:00",
        source_as_of=as_of,
        source_tier=3,
        official=False,
        confidence="Low",
        metric_name=name,
        metric_value=value,
        direction="positive" if value > 0 else "negative",
        tags=["factor"],
    )


def _empty_external(ticker: str = "AAPL") -> ExternalEvidenceBundle:
    return ExternalEvidenceBundle(ticker, "Unavailable", [], [], [])


def _valuation() -> ValuationResult:
    cases = [
        ValuationCase("Bear", 0.25, 90.0, "Fixture", ["Bear"]),
        ValuationCase("Base", 0.50, 110.0, "Fixture", ["Base"]),
        ValuationCase("Bull", 0.25, 130.0, "Fixture", ["Bull"]),
    ]
    return ValuationResult(
        template="Non-financial",
        status="Available",
        cases=cases,
        currency="USD",
        bridge=[ValuationBridgeStep(case.name, "Fair value", case.fair_value, "USD", "Fixture", "Fixture") for case in cases],
    )


if __name__ == "__main__":
    unittest.main()
