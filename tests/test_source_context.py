from __future__ import annotations

from types import SimpleNamespace

from equity_research.source_context import latest_admitted_market_price


def _price(
    ticker: str,
    date: str,
    value: float,
    *,
    status: str = "validated",
) -> SimpleNamespace:
    return SimpleNamespace(
        source_id=f"{ticker}-{date}",
        ticker=ticker,
        validation_status=status,
        source_type="quantitative_historical_price",
        metric_or_claim="Adjusted Close",
        publication_date=date,
        observed_at="2026-08-10T18:40:20+00:00",
        value=value,
        currency="USD",
        origin_group="Registered price source",
        source_family="historical_quantitative",
        source_url=f"https://prices.example/{ticker}",
    )


def test_latest_admitted_price_excludes_benchmarks_and_rejected_rows() -> None:
    research = SimpleNamespace(normalized_sources=[
        _price("AAPL", "2026-08-06", 310.0),
        _price("AAPL", "2026-08-07", 313.33),
        _price("AAPL", "2026-08-08", 999.0, status="rejected"),
        _price("SPY", "2026-08-07", 700.0),
    ])

    price = latest_admitted_market_price(research, "AAPL")

    assert price is not None
    assert price.value == 313.33
    assert price.source_as_of == "2026-08-07"
    assert price.source == "Registered price source"
