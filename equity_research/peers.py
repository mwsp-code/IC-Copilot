from __future__ import annotations

import csv
from datetime import date

from . import config
from .models import PeerDefinition, PeerUniverse


FINANCIAL_METRICS = [
    "Investment banking fees",
    "Trading revenue",
    "Net interest income",
    "ROTCE",
    "CET1 ratio",
    "Compensation ratio",
    "Credit loss provision",
    "Tangible book value",
]

TECH_METRICS = ["Revenue", "Gross margin", "Operating margin", "Free cash flow", "R&D expense"]
CONSUMER_INTERNET_METRICS = [
    "Revenue", "Operating income", "Customer growth", "Take rate", "Free cash flow",
]


_CURATED: dict[str, tuple[str, list[str], list[tuple[str, str]]]] = {
    "AAPL": ("Technology", TECH_METRICS, [("MSFT", "Large-cap platform"), ("GOOGL", "Platform ecosystem"), ("AMZN", "Services and devices"), ("META", "Consumer platform"), ("NVDA", "Technology spending read-through")]),
    "MSFT": ("Technology", TECH_METRICS, [("AAPL", "Large-cap platform"), ("GOOGL", "Cloud and software"), ("AMZN", "Cloud"), ("ORCL", "Enterprise software"), ("ADBE", "Application software")]),
    "GOOGL": ("Technology", TECH_METRICS, [("META", "Digital advertising"), ("AMZN", "Advertising and cloud"), ("MSFT", "AI and cloud"), ("NFLX", "Consumer engagement")]),
    "AMZN": ("Consumer / technology", TECH_METRICS, [("WMT", "Retail"), ("COST", "Retail"), ("MSFT", "Cloud"), ("GOOGL", "Advertising and cloud")]),
    "NVDA": ("Semiconductors", TECH_METRICS, [("AMD", "Accelerators"), ("AVGO", "Semiconductors"), ("TSM", "Foundry demand"), ("ASML", "Equipment demand"), ("MSFT", "AI infrastructure customer")]),
    "META": ("Consumer internet", CONSUMER_INTERNET_METRICS, [("GOOGL", "Digital advertising"), ("SNAP", "Social advertising"), ("PINS", "Social advertising"), ("AMZN", "Advertising")]),
    "TSLA": ("Automotive", ["Revenue", "Automotive margin", "Deliveries", "Free cash flow"], [("GM", "Auto manufacturer"), ("F", "Auto manufacturer"), ("RIVN", "EV manufacturer"), ("LCID", "EV manufacturer"), ("BYDDF", "EV manufacturer")]),
    "JPM": ("Diversified bank", FINANCIAL_METRICS, [("BAC", "Money-center bank"), ("WFC", "Money-center bank"), ("C", "Money-center bank"), ("GS", "Capital markets"), ("MS", "Capital markets")]),
    "BAC": ("Diversified bank", FINANCIAL_METRICS, [("JPM", "Money-center bank"), ("WFC", "Money-center bank"), ("C", "Money-center bank"), ("USB", "Large regional bank")]),
    "GS": ("Broker / investment bank", FINANCIAL_METRICS, [("MS", "Investment bank and wealth manager"), ("JPM", "Capital-markets bank"), ("BAC", "Capital-markets bank"), ("C", "Capital-markets bank"), ("JEF", "Investment bank")]),
    "MS": ("Broker / investment bank", FINANCIAL_METRICS, [("GS", "Investment bank"), ("JPM", "Capital-markets bank"), ("BAC", "Capital-markets bank"), ("C", "Capital-markets bank"), ("JEF", "Investment bank")]),
    "XOM": ("Integrated energy", ["Revenue", "Operating cash flow", "Capital expenditure", "Debt"], [("CVX", "Integrated oil"), ("COP", "Exploration and production"), ("SHEL", "Integrated oil"), ("BP", "Integrated oil")]),
    "LLY": ("Pharmaceuticals", ["Revenue", "R&D expense", "Operating margin", "Guidance"], [("NVO", "Metabolic disease"), ("MRK", "Large-cap pharma"), ("PFE", "Large-cap pharma"), ("JNJ", "Large-cap pharma")]),
}

for ticker, peers in {
    "BABA": ["JD", "PDD", "BIDU", "NTES", "TCOM"],
    "JD": ["BABA", "PDD", "BIDU", "TCOM", "NTES"],
    "PDD": ["BABA", "JD", "BIDU", "TCOM", "NTES"],
    "BIDU": ["BABA", "JD", "PDD", "NTES", "TCOM"],
    "NTES": ["BABA", "BIDU", "PDD", "JD", "TCOM"],
    "TCOM": ["BABA", "JD", "PDD", "BIDU", "NTES"],
}.items():
    _CURATED[ticker] = (
        "China consumer internet",
        CONSUMER_INTERNET_METRICS,
        [(peer, "US-listed China internet peer") for peer in peers],
    )


def peer_universe_for(ticker: str) -> PeerUniverse:
    normalized = ticker.upper()
    csv_universe = _csv_peer_universe_for(normalized)
    if csv_universe:
        return csv_universe
    definition = _CURATED.get(normalized)
    if not definition:
        return PeerUniverse(
            ticker=normalized,
            status="Unconfigured",
            sector_template="Unconfigured",
            provenance="Curated peer registry",
            effective_date=date.today().isoformat(),
            reason="No curated peer universe is configured; peers were not inferred automatically.",
        )
    sector, metrics, peers = definition
    return PeerUniverse(
        ticker=normalized,
        status="Configured",
        sector_template=sector,
        provenance="Curated peer registry v1",
        effective_date="2026-06-25",
        peers=[PeerDefinition(peer, rationale) for peer, rationale in peers],
        key_metrics=list(metrics),
        reason="Peers are explicitly selected for operating and capital-markets comparability.",
    )


def _csv_peer_universe_for(ticker: str) -> PeerUniverse | None:
    path = config.PEER_UNIVERSE_CSV
    if not path.exists():
        return None
    try:
        with path.open("r", newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                row_ticker = str(row.get("ticker") or "").strip().upper()
                if row_ticker != ticker:
                    continue
                peers = _parse_peer_rows(row.get("peers") or "")
                if not peers:
                    continue
                metrics = _split_list(row.get("key_metrics") or "")
                return PeerUniverse(
                    ticker=ticker,
                    status="Configured",
                    sector_template=(row.get("sector_template") or "Configured").strip(),
                    provenance=(row.get("provenance") or "CSV peer universe").strip(),
                    effective_date=(row.get("effective_date") or date.today().isoformat()).strip(),
                    peers=peers,
                    key_metrics=metrics,
                    reason=(row.get("reason") or "Peers are explicitly selected through data/peer_universes.csv.").strip(),
                )
    except OSError:
        return None
    return None


def _parse_peer_rows(value: str) -> list[PeerDefinition]:
    rows: list[PeerDefinition] = []
    for item in value.split("|"):
        clean = item.strip()
        if not clean:
            continue
        if ":" in clean:
            ticker, rationale = clean.split(":", 1)
        else:
            ticker, rationale = clean, "Configured peer"
        ticker = ticker.strip().upper()
        if ticker:
            rows.append(PeerDefinition(ticker, rationale.strip() or "Configured peer"))
    return rows


def _split_list(value: str) -> list[str]:
    return [item.strip() for item in value.replace(";", "|").split("|") if item.strip()]


PEER_MAP = {
    ticker: [definition.ticker for definition in peer_universe_for(ticker).peers]
    for ticker in _CURATED
}
