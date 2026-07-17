from __future__ import annotations

import csv
import re
from pathlib import Path

from .adr_profiles import adr_profile_for
from .analysis import format_number
from .config import INDUSTRY_PLAYBOOK_CSV
from .models import (
    BringYourOwnDataStatus,
    ChangeEvent,
    CompanyDriver,
    DriverCoverageCheck,
    CompanyEconomics,
    CompanyIdentity,
    FinancialMetric,
    IndustryPlaybook,
    PeerUniverse,
    PlaybookQualityCheck,
)
from .driver_templates import TEMPLATES
from .valuation import classify_template


PLAYBOOKS: dict[str, dict[str, object]] = {
    "Non-financial": {
        "industry_label": "General operating company",
        "key_kpis": ["Revenue", "Gross margin", "Operating income", "Free cash flow", "Share count"],
        "leading_indicators": ["Bookings/orders", "pricing", "inventory", "customer demand", "channel checks"],
        "valuation_methods": ["Forward P/E", "EV/revenue", "EV/EBITDA", "FCF yield"],
        "macro_sensitivities": ["Rates", "FX", "consumer/business demand", "input costs"],
        "normal_catalysts": ["Earnings", "guidance", "product cycle", "margin update", "capital return"],
    },
    "Bank": {
        "industry_label": "Bank / broker",
        "key_kpis": ["NII", "trading revenue", "IB fees", "ROTCE", "CET1", "provisions", "tangible book"],
        "leading_indicators": ["yield curve", "credit spreads", "capital markets issuance", "deposit beta"],
        "valuation_methods": ["P/TBV", "P/B", "forward P/E", "ROTCE versus COE"],
        "macro_sensitivities": ["Rates", "credit cycle", "equity markets", "volatility", "regulation"],
        "normal_catalysts": ["Earnings", "CCAR", "capital return", "credit costs", "deal activity"],
    },
    "Insurer": {
        "industry_label": "Insurer",
        "key_kpis": ["Premiums", "combined ratio", "investment income", "book value", "ROE"],
        "leading_indicators": ["catastrophe losses", "pricing cycle", "rates", "claims inflation"],
        "valuation_methods": ["P/B", "forward P/E", "ROE spread"],
        "macro_sensitivities": ["Rates", "loss costs", "claims inflation", "credit markets"],
        "normal_catalysts": ["Reserve update", "pricing renewal", "cat loss disclosure", "capital return"],
    },
    "REIT": {
        "industry_label": "REIT",
        "key_kpis": ["NOI", "occupancy", "same-store growth", "FFO/AFFO", "leverage", "dividend coverage"],
        "leading_indicators": ["cap rates", "rent growth", "leasing spreads", "financing costs"],
        "valuation_methods": ["P/FFO", "P/AFFO", "NAV premium/discount", "dividend yield"],
        "macro_sensitivities": ["Rates", "cap rates", "property demand", "credit availability"],
        "normal_catalysts": ["Leasing update", "asset sale", "financing", "dividend", "guidance"],
    },
}


SECTOR_PLAYBOOKS: tuple[dict[str, object], ...] = (
    {
        "sic_ranges": [(7370, 7379)],
        "description_terms": ["software", "cloud", "saas", "data processing"],
        "industry_label": "Software / cloud platform",
        "key_kpis": ["ARR/RPO", "revenue growth", "net retention", "gross margin", "operating margin", "FCF margin"],
        "leading_indicators": ["cloud spend", "seat expansion", "AI product adoption", "enterprise IT budgets"],
        "macro_sensitivities": ["rates", "enterprise software budgets", "USD FX", "AI infrastructure costs"],
        "normal_catalysts": ["Earnings", "RPO/billings update", "AI product adoption", "margin guide", "large customer commentary"],
    },
    {
        "sic_ranges": [(3570, 3579), (3670, 3679)],
        "description_terms": ["semiconductor", "computer", "electronic components"],
        "industry_label": "Semiconductors / hardware technology",
        "key_kpis": ["segment revenue", "gross margin", "inventory", "backlog", "capex cycle", "customer concentration"],
        "leading_indicators": ["hyperscaler capex", "channel inventory", "lead times", "memory pricing", "export controls"],
        "macro_sensitivities": ["rates", "USD FX", "China restrictions", "electronics demand", "supply chain"],
        "normal_catalysts": ["Earnings", "product launch", "inventory correction", "customer capex update", "export-control change"],
    },
    {
        "sic_ranges": [(2830, 2839), (3840, 3851)],
        "description_terms": ["pharmaceutical", "biotechnology", "medical device", "life sciences"],
        "industry_label": "Healthcare / pharma / medtech",
        "key_kpis": ["product revenue", "pipeline milestones", "trial data", "gross margin", "R&D intensity", "LOE exposure"],
        "leading_indicators": ["trial readouts", "FDA calendar", "prescription trends", "hospital volumes", "payer coverage"],
        "macro_sensitivities": ["rates", "healthcare utilization", "policy/reimbursement", "drug pricing"],
        "normal_catalysts": ["Clinical data", "FDA decision", "earnings", "pipeline update", "pricing/reimbursement action"],
    },
    {
        "sic_ranges": [(5200, 5999)],
        "description_terms": ["retail", "stores", "e-commerce", "consumer products"],
        "industry_label": "Consumer retail / e-commerce",
        "key_kpis": ["same-store sales", "traffic", "ticket", "gross margin", "inventory", "advertising/merchant revenue"],
        "leading_indicators": ["consumer spending", "retail sales", "promotional intensity", "inventory levels", "shipping costs"],
        "macro_sensitivities": ["consumer confidence", "employment", "wages", "inflation", "credit conditions"],
        "normal_catalysts": ["Earnings", "holiday update", "inventory/margin commentary", "consumer data", "guidance"],
    },
    {
        "sic_ranges": [(1310, 1389), (2910, 2911)],
        "description_terms": ["oil", "gas", "energy", "petroleum", "refining"],
        "industry_label": "Energy / upstream and refining",
        "key_kpis": ["production", "realized price", "lifting cost", "capex", "reserve life", "FCF"],
        "leading_indicators": ["Brent/WTI", "natural gas", "crack spreads", "rig counts", "OPEC supply"],
        "macro_sensitivities": ["oil price", "gas price", "USD FX", "rates", "geopolitical supply risk"],
        "normal_catalysts": ["Earnings", "production guide", "reserve update", "capital-return update", "commodity shock"],
    },
    {
        "sic_ranges": [(3710, 3719)],
        "description_terms": ["automotive", "motor vehicles", "ev", "vehicle"],
        "industry_label": "Autos / mobility",
        "key_kpis": ["deliveries", "ASP", "gross margin", "inventory days", "incentives", "capex"],
        "leading_indicators": ["auto sales", "EV subsidies", "battery costs", "used-car prices", "dealer inventory"],
        "macro_sensitivities": ["rates", "consumer credit", "FX", "commodity inputs", "policy/subsidies"],
        "normal_catalysts": ["Delivery report", "earnings", "pricing action", "product launch", "regulatory/safety event"],
    },
    {
        "sic_ranges": [(4810, 4899)],
        "description_terms": ["telecommunications", "wireless", "broadband"],
        "industry_label": "Telecom / connectivity",
        "key_kpis": ["net adds", "ARPU", "churn", "capex intensity", "EBITDA margin", "leverage"],
        "leading_indicators": ["wireless pricing", "fiber penetration", "spectrum costs", "device upgrade cycle"],
        "macro_sensitivities": ["rates", "consumer credit", "competition", "regulation", "spectrum policy"],
        "normal_catalysts": ["Earnings", "subscriber update", "pricing plan", "spectrum auction", "deleveraging update"],
    },
    {
        "sic_ranges": [(4910, 4939)],
        "description_terms": ["electric", "utility", "utilities", "power"],
        "industry_label": "Utilities / power infrastructure",
        "key_kpis": ["rate base", "allowed ROE", "load growth", "capex plan", "debt", "dividend coverage"],
        "leading_indicators": ["rate-case calendar", "power demand", "fuel costs", "renewable interconnection", "AI/data-center load"],
        "macro_sensitivities": ["rates", "fuel costs", "regulation", "power demand", "credit spreads"],
        "normal_catalysts": ["Rate case", "earnings", "capex update", "storm cost filing", "load-growth update"],
    },
    {
        "sic_ranges": [(4510, 4581)],
        "description_terms": ["airline", "air transportation"],
        "industry_label": "Airlines / travel capacity",
        "key_kpis": ["unit revenue", "load factor", "capacity", "fuel cost", "CASM", "free cash flow"],
        "leading_indicators": ["TSA throughput", "jet fuel", "booking curves", "business travel", "capacity growth"],
        "macro_sensitivities": ["fuel", "consumer demand", "FX", "labor costs", "rates"],
        "normal_catalysts": ["Traffic update", "earnings", "fuel shock", "capacity guide", "labor agreement"],
    },
    {
        "sic_ranges": [(5810, 5819)],
        "description_terms": ["restaurant", "food services"],
        "industry_label": "Restaurants / food service",
        "key_kpis": ["same-store sales", "traffic", "ticket", "restaurant margin", "unit growth", "labor/commodity costs"],
        "leading_indicators": ["food inflation", "wage inflation", "consumer spending", "menu pricing", "store openings"],
        "macro_sensitivities": ["employment", "wages", "food costs", "consumer confidence", "gasoline prices"],
        "normal_catalysts": ["Earnings", "monthly sales", "menu pricing", "unit growth update", "margin guide"],
    },
    {
        "sic_ranges": [(4830, 4841), (7810, 7999)],
        "description_terms": ["media", "entertainment", "streaming", "advertising"],
        "industry_label": "Media / entertainment / ads",
        "key_kpis": ["ad revenue", "subscribers", "ARPU", "content spend", "engagement", "segment margin"],
        "leading_indicators": ["ad market", "subscriber churn", "sports rights", "box office", "consumer attention"],
        "macro_sensitivities": ["ad budgets", "consumer spending", "FX", "rates", "content inflation"],
        "normal_catalysts": ["Earnings", "subscriber update", "ad-market commentary", "content slate", "sports-rights deal"],
    },
)


TICKER_OVERRIDES: dict[str, dict[str, object]] = {
    "AAPL": {
        "industry_label": "Large-cap consumer technology / devices and services",
        "business_model": "Integrated device, software, services, and ecosystem monetization business.",
        "key_kpis": ["iPhone revenue", "Services revenue", "gross margin", "installed base", "buybacks"],
        "leading_indicators": ["device replacement cycle", "China demand", "Services attach rate", "FX", "AI feature adoption"],
        "normal_catalysts": ["Earnings", "product cycle", "capital return", "regulatory action", "Services margin"],
    },
    "BABA": {
        "industry_label": "China internet / commerce / cloud ADR",
        "business_model": "China commerce, cloud, local services, logistics, and international digital commerce platform.",
        "key_kpis": ["GMV", "customer-management revenue", "cloud revenue", "take rate", "buybacks", "RMB/USD"],
        "leading_indicators": ["China retail sales", "cloud demand", "merchant activity", "RMB", "ADR/HK liquidity"],
        "macro_sensitivities": ["China consumption", "RMB/USD", "policy/regulation", "US-China risk", "rates"],
        "normal_catalysts": ["Earnings", "6-K results", "buyback update", "segment margin", "regulatory change"],
    },
    "NVDA": {
        "industry_label": "Semiconductors / accelerated computing",
        "business_model": "GPU, networking, systems, and software stack tied to data-center and AI capex.",
        "key_kpis": ["Data-center revenue", "gross margin", "backlog/supply", "customer concentration", "inventory"],
        "leading_indicators": ["hyperscaler capex", "AI server demand", "export controls", "memory supply", "lead times"],
    },
    "GS": {
        "industry_label": "Investment bank / broker-dealer",
        "business_model": "Global banking, markets, asset management, and wealth platform sensitive to capital markets activity.",
        "key_kpis": ["IB fees", "trading revenue", "ROTCE", "CET1", "compensation ratio", "tangible book"],
        "leading_indicators": ["IPO/debt issuance", "M&A activity", "market volatility", "credit spreads", "rate curve"],
    },
}


def build_company_economics(
    identity: CompanyIdentity,
    metrics: list[FinancialMetric],
    events: list[ChangeEvent],
    peer_universe: PeerUniverse | None,
    manual_data: BringYourOwnDataStatus | None = None,
) -> CompanyEconomics:
    template = classify_template(identity)
    adr_profile = adr_profile_for(identity.ticker)
    playbook = _playbook_for(identity, template, peer_universe)
    drivers = _drivers_from_metrics(metrics, events, playbook)
    if adr_profile and adr_profile.segment_drivers:
        drivers.extend(_drivers_from_adr_profile(adr_profile, playbook, {driver.name for driver in drivers}))
    if manual_data and any(source.rows_loaded for source in manual_data.sources):
        drivers.append(CompanyDriver(
            "Manual / paid data overlay",
            "manual_data",
            "Medium",
            "User-provided CSV/manual data is available and should be reviewed before final thesis synthesis.",
            trend="User supplied",
            why_it_matters="Manual imports can fill open-source gaps for segments, consensus, transcripts, or paid-report excerpts.",
            source="Bring-your-own-data import",
        ))
    driver_coverage = _driver_coverage_checks(drivers)
    playbook_quality = _playbook_quality_checks(identity, playbook, drivers, driver_coverage)
    material_count = sum(1 for driver in drivers if driver.materiality in {"High", "Medium"})
    data_gaps = []
    if not metrics:
        data_gaps.append("Structured company facts were unavailable, so the driver map is based on filings and playbook defaults.")
    if not drivers:
        data_gaps.append("No material operating driver could be mapped from current metrics or detected events.")
    if manual_data and manual_data.status == "Unavailable":
        data_gaps.append("No manual segment, industry, consensus, or paid-report CSV imports were found.")
    return CompanyEconomics(
        ticker=identity.ticker,
        status="Available" if material_count else "Partial",
        business_model=_business_model(identity, playbook),
        industry_playbook=playbook,
        drivers=drivers[:12],
        driver_coverage=driver_coverage[:12],
        playbook_quality=playbook_quality,
        playbook_quality_score=_playbook_quality_score(playbook_quality),
        material_driver_count=material_count,
        data_gaps=data_gaps,
    )


def attach_economic_context(events: list[ChangeEvent], economics: CompanyEconomics) -> None:
    for event in events:
        driver = _driver_for_event(event, economics)
        if not driver:
            event.metrics["economic_driver"] = "Unmapped"
            event.metrics["driver_materiality"] = "Low"
            event.metrics["industry_playbook"] = economics.industry_playbook.industry_label
            if not event.why_this_matters:
                event.why_this_matters = "This signal is not yet mapped to a material economic driver."
            continue
        event.metrics["economic_driver"] = driver.name
        event.metrics["driver_materiality"] = driver.materiality
        event.metrics["industry_playbook"] = economics.industry_playbook.industry_label
        event.metrics["driver_why_it_matters"] = driver.why_it_matters
        driver_note = f"Economic driver: {driver.name} ({driver.materiality}). {driver.why_it_matters}"
        if event.why_this_matters:
            if driver.name not in event.why_this_matters:
                event.why_this_matters = f"{event.why_this_matters} {driver_note}"
        else:
            event.why_this_matters = driver_note


def _playbook_for(
    identity: CompanyIdentity,
    template: str,
    peer_universe: PeerUniverse | None,
) -> IndustryPlaybook:
    base = dict(PLAYBOOKS.get(template, PLAYBOOKS["Non-financial"]))
    playbook_source = f"built_in:{template}"
    sector_overlay = _sector_playbook_for(identity)
    if sector_overlay:
        base.update(sector_overlay)
        playbook_source = f"sector:{sector_overlay.get('industry_label', template)}"
    adr_profile = adr_profile_for(identity.ticker)
    override = TICKER_OVERRIDES.get(identity.ticker.upper(), {})
    base.update({key: value for key, value in override.items() if key != "business_model"})
    if override:
        playbook_source = f"ticker:{identity.ticker.upper()}"
    if adr_profile and adr_profile.segment_drivers:
        base["key_kpis"] = list(dict.fromkeys(list(base.get("key_kpis", [])) + list(adr_profile.segment_drivers)))
        base["leading_indicators"] = list(dict.fromkeys(list(base.get("leading_indicators", [])) + [
            "Home-market demand", "FX", "ADR/HK liquidity", "policy/regulation"
        ] + list(adr_profile.benchmark_tickers)))
        base["macro_sensitivities"] = list(dict.fromkeys(list(base.get("macro_sensitivities", [])) + [
            f"{adr_profile.reporting_currency}/USD", "home-market consumption", "local policy risk"
        ]))
        playbook_source = f"{playbook_source}+adr_profile"
    csv_override = _csv_playbook_override(identity, INDUSTRY_PLAYBOOK_CSV)
    csv_business_model = ""
    csv_peers: list[str] = []
    if csv_override:
        values, csv_business_model, csv_peers, csv_source = csv_override
        base.update(values)
        if csv_business_model:
            base["business_model"] = csv_business_model
        playbook_source = csv_source
    if adr_profile and adr_profile.segment_drivers:
        base["key_kpis"] = list(dict.fromkeys(list(base.get("key_kpis", [])) + list(adr_profile.segment_drivers)))
        base["leading_indicators"] = list(dict.fromkeys(list(base.get("leading_indicators", [])) + [
            "Home-market demand", "FX", "ADR/HK liquidity", "policy/regulation"
        ] + list(adr_profile.benchmark_tickers)))
        base["macro_sensitivities"] = list(dict.fromkeys(list(base.get("macro_sensitivities", [])) + [
            f"{adr_profile.reporting_currency}/USD", "home-market consumption", "local policy risk"
        ]))
        if "+adr_profile" not in playbook_source:
            playbook_source = f"{playbook_source}+adr_profile"
    peer_tickers = [peer.ticker for peer in peer_universe.peers] if peer_universe else []
    if csv_peers:
        peer_tickers = list(dict.fromkeys(csv_peers + peer_tickers))
    data_gaps = []
    if not peer_tickers:
        data_gaps.append("Curated peer universe is not configured.")
    return IndustryPlaybook(
        industry_label=str(base["industry_label"]),
        sector_template=template,
        key_kpis=list(base.get("key_kpis", [])),
        leading_indicators=list(base.get("leading_indicators", [])),
        valuation_methods=list(base.get("valuation_methods", PLAYBOOKS.get(template, {}).get("valuation_methods", []))),
        macro_sensitivities=list(base.get("macro_sensitivities", PLAYBOOKS.get(template, {}).get("macro_sensitivities", []))),
        normal_catalysts=list(base.get("normal_catalysts", [])),
        peer_tickers=peer_tickers,
        data_gaps=data_gaps,
        playbook_source=playbook_source,
    )


def _csv_playbook_override(
    identity: CompanyIdentity,
    path: Path,
) -> tuple[dict[str, object], str, list[str], str] | None:
    if not path.exists():
        return None
    best: tuple[int, dict[str, str]] | None = None
    try:
        with path.open("r", newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                score = _playbook_row_match_score(identity, row)
                if score <= 0:
                    continue
                if best is None or score > best[0]:
                    best = (score, row)
    except OSError:
        return None
    if best is None:
        return None
    row = best[1]
    values: dict[str, object] = {}
    for key in (
        "industry_label",
        "key_kpis",
        "leading_indicators",
        "valuation_methods",
        "macro_sensitivities",
        "normal_catalysts",
    ):
        value = _clean_row_value(row.get(key))
        if not value:
            continue
        if key == "industry_label":
            values[key] = value
        else:
            values[key] = _split_csv_list(value)
    business_model = _clean_row_value(row.get("business_model"))
    peer_tickers = _split_csv_list(_clean_row_value(row.get("peer_tickers")))
    source = _clean_row_value(row.get("source")) or path.name
    label = _clean_row_value(row.get("industry_label")) or _clean_row_value(row.get("ticker")) or "matched row"
    return values, business_model, peer_tickers, f"csv:{source}:{label}"


def _playbook_row_match_score(identity: CompanyIdentity, row: dict[str, str]) -> int:
    ticker = identity.ticker.upper()
    row_ticker = _clean_row_value(row.get("ticker")).upper()
    if row_ticker:
        tickers = {item.upper() for item in _split_csv_list(row_ticker)}
        return 100 if ticker in tickers else 0
    sic = _parse_int(identity.sic)
    row_sic = _parse_int(_clean_row_value(row.get("sic") or row.get("sic_code")))
    if row_sic and sic == row_sic:
        return 80
    sic_min = _parse_int(_clean_row_value(row.get("sic_min")))
    sic_max = _parse_int(_clean_row_value(row.get("sic_max")))
    if sic and sic_min and sic_max and sic_min <= sic <= sic_max:
        return 70
    terms = _split_csv_list(_clean_row_value(row.get("description_contains")))
    if terms:
        description = f"{identity.sic_description or ''} {identity.name or ''}".lower()
        return 50 if any(term.lower() in description for term in terms) else 0
    return 0


def _split_csv_list(value: str) -> list[str]:
    if not value:
        return []
    raw_items = re.split(r"[|;]", value)
    return [item.strip() for item in raw_items if item.strip()]


def _clean_row_value(value: object) -> str:
    return str(value or "").strip()


def _parse_int(value: object) -> int:
    try:
        return int(str(value or "").strip())
    except ValueError:
        return 0


def _sector_playbook_for(identity: CompanyIdentity) -> dict[str, object]:
    try:
        sic = int(identity.sic or 0)
    except ValueError:
        sic = 0
    description = f"{identity.sic_description or ''} {identity.name or ''}".lower()
    for candidate in SECTOR_PLAYBOOKS:
        ranges = candidate.get("sic_ranges", [])
        terms = candidate.get("description_terms", [])
        sic_match = any(start <= sic <= end for start, end in ranges)
        term_match = any(str(term).lower() in description for term in terms)
        if sic_match or term_match:
            return {
                key: value for key, value in candidate.items()
                if key not in {"sic_ranges", "description_terms"}
            }
    return {}


def _business_model(identity: CompanyIdentity, playbook: IndustryPlaybook) -> str:
    if getattr(playbook, "playbook_source", "").startswith("csv:"):
        csv_business_model = _csv_playbook_business_model(identity, INDUSTRY_PLAYBOOK_CSV)
        if csv_business_model:
            return csv_business_model
    override = TICKER_OVERRIDES.get(identity.ticker.upper(), {}).get("business_model")
    if override:
        return str(override)
    return (
        f"{identity.name.title()} is treated as a {playbook.industry_label.lower()} for MVP thesis generation. "
        "The app should validate segment economics with filings, transcripts, and manual industry data."
    )


def _csv_playbook_business_model(identity: CompanyIdentity, path: Path) -> str:
    override = _csv_playbook_override(identity, path)
    if not override:
        return ""
    return override[1]


def _drivers_from_metrics(
    metrics: list[FinancialMetric],
    events: list[ChangeEvent],
    playbook: IndustryPlaybook,
) -> list[CompanyDriver]:
    drivers: list[CompanyDriver] = []
    used: set[str] = set()
    for metric in sorted(metrics, key=lambda item: abs(item.yoy_change_pct or 0), reverse=True):
        name = _driver_name_for_metric(metric.name)
        if name in used:
            continue
        used.add(name)
        materiality = _materiality(metric.yoy_change_pct)
        drivers.append(CompanyDriver(
            name=name,
            category=_driver_category(metric.name),
            materiality=materiality,
            current_evidence=(
                f"{metric.name} was {format_number(metric.value)} {metric.unit} for {metric.period_end}"
                + (f", {metric.yoy_change_pct:+.1f}% versus comparable period." if metric.yoy_change_pct is not None else ".")
            ),
            latest_value=f"{format_number(metric.value)} {metric.unit}",
            trend=_trend(metric.yoy_change_pct),
            why_it_matters=_why_metric_matters(metric.name, playbook),
            source=metric.source_kind or metric.form or "structured facts",
        ))
    for event in events:
        name = _driver_name_for_event(event)
        if name in used:
            continue
        used.add(name)
        drivers.append(CompanyDriver(
            name=name,
            category=event.category,
            materiality="High" if event.severity >= 4 else "Medium" if event.severity >= 3 else "Low",
            current_evidence=event.summary,
            trend=event.direction.title(),
            why_it_matters=_why_event_matters(event, playbook),
            source=event.source,
        ))
    return drivers


def _drivers_from_adr_profile(
    adr_profile,
    playbook: IndustryPlaybook,
    existing_names: set[str],
) -> list[CompanyDriver]:
    rows: list[CompanyDriver] = []
    for name in adr_profile.segment_drivers:
        if name in existing_names:
            continue
        existing_names.add(name)
        rows.append(CompanyDriver(
            name=name,
            category="adr_segment",
            materiality="Medium",
            current_evidence=(
                f"{name} is part of the ADR/FPI segment playbook and should be tied to issuer filings, "
                "6-K results, presentations, or manual segment KPI imports before becoming thesis-grade."
            ),
            trend="Needs source validation",
            why_it_matters=_why_adr_segment_matters(name, adr_profile, playbook),
            source=f"ADR profile ({adr_profile.source})",
        ))
    return rows


def _driver_coverage_checks(drivers: list[CompanyDriver]) -> list[DriverCoverageCheck]:
    rows: list[DriverCoverageCheck] = []
    for driver in drivers:
        template = _template_for_driver(driver)
        missing = _missing_evidence_for_driver(driver, template)
        status = _coverage_status(driver, missing)
        rows.append(DriverCoverageCheck(
            driver_name=driver.name,
            materiality=driver.materiality,
            status=status,
            current_evidence=driver.current_evidence,
            required_evidence=[
                template.confirm_evidence,
                template.falsify_evidence,
            ],
            missing_evidence=missing,
            next_source=template.next_source,
            falsification_test=template.falsify_evidence,
            stage_impact=_stage_impact(status),
        ))
    return rows


def _playbook_quality_checks(
    identity: CompanyIdentity,
    playbook: IndustryPlaybook,
    drivers: list[CompanyDriver],
    driver_coverage: list[DriverCoverageCheck],
) -> list[PlaybookQualityCheck]:
    return [
        _business_model_quality(identity, playbook),
        _kpi_indicator_quality(playbook),
        _valuation_macro_quality(playbook),
        _peer_universe_quality(playbook),
        _driver_source_quality(drivers, driver_coverage),
    ]


def _business_model_quality(identity: CompanyIdentity, playbook: IndustryPlaybook) -> PlaybookQualityCheck:
    source = playbook.playbook_source or "built_in"
    is_specific = source.startswith(("ticker:", "csv:")) or "+adr_profile" in source
    is_sector = source.startswith("sector:")
    if is_specific:
        status, score = "Specific", 100
        gaps: list[str] = []
    elif is_sector:
        status, score = "Sector-level", 75
        gaps = ["Add ticker-specific segment economics, KPIs, and peer rationale if this is a frequent research name."]
    else:
        status, score = "Generic", 45
        gaps = ["Replace generic playbook with sector, ticker, ADR/FPI, or CSV override before relying on thesis ranking."]
    return PlaybookQualityCheck(
        "Business model specificity",
        status,
        score,
        f"{identity.ticker} uses playbook source {source}.",
        [f"Industry label: {playbook.industry_label}.", f"Template: {playbook.sector_template}."],
        gaps,
        "Add data/industry_playbooks.csv override with business_model, key_kpis, peer_tickers, and leading_indicators.",
        "Generic playbooks keep ideas at Candidate/Research Question unless source evidence validates the exact driver.",
    )


def _kpi_indicator_quality(playbook: IndustryPlaybook) -> PlaybookQualityCheck:
    evidence = [
        f"KPIs: {', '.join(playbook.key_kpis[:6]) or 'none'}.",
        f"Leading indicators: {', '.join(playbook.leading_indicators[:6]) or 'none'}.",
    ]
    gaps: list[str] = []
    if len(playbook.key_kpis) < 4:
        gaps.append("Add at least four industry KPIs that explain revenue, margin, cash flow, or risk premium.")
    if len(playbook.leading_indicators) < 3:
        gaps.append("Add leading indicators that can be monitored before the next filing.")
    if not gaps:
        status, score = "Covered", 100
    elif playbook.key_kpis or playbook.leading_indicators:
        status, score = "Partial", 65
    else:
        status, score = "Missing", 0
    return PlaybookQualityCheck(
        "KPI and leading-indicator coverage",
        status,
        score,
        "Checks whether the playbook can translate a filing signal into operating KPIs and monitorable indicators.",
        evidence,
        gaps,
        "Add KPI and leading-indicator columns in the playbook CSV or ticker override.",
        "Weak KPI coverage blocks convincing causal bridges.",
    )


def _valuation_macro_quality(playbook: IndustryPlaybook) -> PlaybookQualityCheck:
    evidence = [
        f"Valuation methods: {', '.join(playbook.valuation_methods[:5]) or 'none'}.",
        f"Macro sensitivities: {', '.join(playbook.macro_sensitivities[:5]) or 'none'}.",
        f"Normal catalysts: {', '.join(playbook.normal_catalysts[:5]) or 'none'}.",
    ]
    gaps: list[str] = []
    if len(playbook.valuation_methods) < 2:
        gaps.append("Add sector-appropriate valuation methods before connecting driver changes to payoff.")
    if len(playbook.macro_sensitivities) < 2:
        gaps.append("Add macro sensitivities for attribution and scenario falsification.")
    if len(playbook.normal_catalysts) < 2:
        gaps.append("Add normal catalysts so thesis timing is not generic.")
    if not gaps:
        status, score = "Covered", 100
    elif playbook.valuation_methods or playbook.macro_sensitivities or playbook.normal_catalysts:
        status, score = "Partial", 65
    else:
        status, score = "Missing", 0
    return PlaybookQualityCheck(
        "Valuation, macro, and catalyst coverage",
        status,
        score,
        "Checks whether the playbook can connect drivers to valuation, macro attribution, and timing.",
        evidence,
        gaps,
        "Add valuation_methods, macro_sensitivities, and normal_catalysts to the playbook source.",
        "Missing valuation/macro/catalyst context keeps ideas from becoming convincing IC pitches.",
    )


def _peer_universe_quality(playbook: IndustryPlaybook) -> PlaybookQualityCheck:
    gaps = list(playbook.data_gaps)
    if playbook.peer_tickers:
        status, score = "Configured", 100
        evidence = [f"Peers: {', '.join(playbook.peer_tickers[:8])}."]
    else:
        status, score = "Missing", 0
        evidence = []
        if not gaps:
            gaps.append("Curated peer universe is not configured.")
    return PlaybookQualityCheck(
        "Peer universe coverage",
        status,
        score,
        "Checks whether peer read-through can compare operating metrics against a known universe.",
        evidence,
        gaps,
        "Configure curated peers through the peer map or industry_playbooks.csv peer_tickers column.",
        "Peer metric read-through is weak until a curated peer universe exists.",
    )


def _driver_source_quality(
    drivers: list[CompanyDriver],
    coverage: list[DriverCoverageCheck],
) -> PlaybookQualityCheck:
    playbook_only = [item for item in coverage if item.status.startswith("Playbook-only")]
    ready = [item for item in coverage if item.status == "Mapped / ready for thesis testing"]
    mapped = [item for item in coverage if item.status.startswith("Mapped")]
    evidence = [
        f"Drivers: {len(drivers)} total.",
        f"Mapped drivers: {len(mapped)}.",
        f"Playbook-only drivers: {len(playbook_only)}.",
    ]
    gaps: list[str] = []
    if not drivers:
        gaps.append("No company drivers are mapped from metrics, events, manual data, or ADR/FPI profile.")
        status, score = "Missing", 0
    elif playbook_only and not mapped:
        gaps.append("All material drivers are playbook-only and need issuer, filing, transcript, metric, or imported-data validation.")
        status, score = "Playbook-only", 35
    elif playbook_only:
        gaps.append("Some drivers are playbook-only and cannot support Research-Ready ideas until source-validated.")
        status, score = "Partial", 65
    elif ready:
        status, score = "Source-backed", 100
    else:
        status, score = "Mapped / needs corroboration", 75
    return PlaybookQualityCheck(
        "Driver source validation",
        status,
        score,
        "Checks whether playbook drivers are backed by current source evidence instead of only template defaults.",
        evidence,
        gaps,
        "Use source-plan requests, issuer filings, transcripts, segment KPIs, or manual imports to validate playbook-only drivers.",
        "Playbook-only drivers block Research-Ready and High-Conviction promotion.",
    )


def _playbook_quality_score(checks: list[PlaybookQualityCheck]) -> int:
    if not checks:
        return 0
    return round(sum(item.score for item in checks) / len(checks))


def _template_for_driver(driver: CompanyDriver):
    text = f"{driver.name} {driver.category} {driver.current_evidence}".lower()
    if any(token in text for token in ("share", "buyback", "dilution", "capital return")):
        return TEMPLATES["share_count"]
    if any(token in text for token in ("guidance", "outlook", "expectation")):
        return TEMPLATES["guidance"]
    if any(token in text for token in ("gross", "margin", "mix", "cost of revenue")):
        return TEMPLATES["margin"]
    if any(token in text for token in ("expense", "opex", "sga", "r&d", "marketing", "operating leverage")):
        return TEMPLATES["opex"]
    if any(token in text for token in ("debt", "liquidity", "cash", "borrow", "leverage", "refinancing")):
        return TEMPLATES["debt"]
    if any(token in text for token in ("risk", "litigation", "regulation", "policy", "legal")):
        return TEMPLATES["regulation"]
    if any(token in text for token in ("management", "tone", "credibility", "evasion", "governance", "incentive")):
        return TEMPLATES["management"]
    return TEMPLATES["revenue"]


def _missing_evidence_for_driver(driver: CompanyDriver, template) -> list[str]:
    missing: list[str] = []
    source = (driver.source or "").lower()
    evidence = (driver.current_evidence or "").lower()
    if "adr profile" in source or "playbook" in evidence or "should be tied" in evidence:
        missing.append("Source-linked company evidence for this playbook driver.")
    if driver.materiality in {"High", "Medium"}:
        missing.append(template.confirm_evidence)
        missing.append(f"Falsification check: {template.falsify_evidence}")
    if not driver.source:
        missing.append("Source provenance for the observed driver.")
    return _dedupe(missing)


def _coverage_status(driver: CompanyDriver, missing: list[str]) -> str:
    if driver.materiality == "Low":
        return "Low materiality"
    source = (driver.source or "").lower()
    evidence = (driver.current_evidence or "").lower()
    if "adr profile" in source or "playbook" in evidence or "should be tied" in evidence:
        return "Playbook-only / needs source validation"
    if missing:
        return "Mapped / needs corroboration"
    return "Mapped / ready for thesis testing"


def _stage_impact(status: str) -> str:
    if status == "Low materiality":
        return "Usually Watch unless corroborated by a more material source signal."
    if status.startswith("Playbook-only"):
        return "Blocks Research-Ready until an issuer, filing, transcript, metric, or imported dataset validates the driver."
    if status.startswith("Mapped / needs"):
        return "Can support Candidate; Research-Ready requires confirmation and falsification evidence."
    return "Can support Research-Ready if the idea also passes price, valuation/payoff, counter-thesis, and monitor gates."


def _dedupe(items: list[str]) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for item in items:
        clean = item.strip()
        if not clean or clean in seen:
            continue
        rows.append(clean)
        seen.add(clean)
    return rows


def _why_adr_segment_matters(name: str, adr_profile, playbook: IndustryPlaybook) -> str:
    lower = name.lower()
    if "commerce" in lower or "retail" in lower or "travel" in lower:
        return "Home-market demand and monetization are core bridges from operating evidence to revenue and margin."
    if "cloud" in lower or "ai" in lower:
        return "Cloud and AI mix can change growth durability, margin structure, and valuation multiple."
    if "logistics" in lower or "local services" in lower:
        return "Service/logistics losses or scale benefits can materially change operating leverage."
    if "buyback" in lower:
        return "Capital return only supports per-share value after share-basis reconciliation confirms the count is comparable."
    if "rmb" in lower or "fx" in lower:
        return f"FX can alter ADR returns and translated financials because reporting currency is {adr_profile.reporting_currency}."
    if "policy" in lower:
        return "Policy and regulation can change risk premium, growth assumptions, and disclosure quality."
    return f"This ADR/FPI segment should be tested against the {playbook.industry_label} playbook."


def _driver_for_event(event: ChangeEvent, economics: CompanyEconomics) -> CompanyDriver | None:
    event_driver = _driver_name_for_event(event)
    event_tokens = set(_tokens(event_driver + " " + event.category + " " + str(event.metrics.get("metric_name", ""))))
    best: tuple[int, CompanyDriver] | None = None
    for driver in economics.drivers:
        driver_tokens = set(_tokens(driver.name + " " + driver.category))
        score = len(event_tokens & driver_tokens)
        if event.metrics.get("metric_name") and str(event.metrics["metric_name"]).lower() in driver.current_evidence.lower():
            score += 3
        if best is None or score > best[0]:
            best = (score, driver)
    if best and best[0] > 0:
        return best[1]
    return None


def _driver_name_for_metric(metric_name: str) -> str:
    lower = metric_name.lower()
    if "revenue" in lower or "sales" in lower:
        return "Revenue growth / demand"
    if "gross" in lower or "margin" in lower:
        return "Gross margin / mix"
    if "cash" in lower or "free cash" in lower:
        return "Cash generation / liquidity"
    if "operating" in lower or "income" in lower or "ebit" in lower:
        return "Operating leverage / profitability"
    if "debt" in lower or "borrow" in lower or "liabil" in lower:
        return "Balance sheet / liquidity"
    if "share" in lower:
        return "Share count / dilution"
    if "equity" in lower or "book" in lower:
        return "Book value / capital"
    return metric_name


def _driver_name_for_event(event: ChangeEvent) -> str:
    if event.category == "margin":
        return "Gross margin / mix"
    if event.category == "financial_kpi":
        return _driver_name_for_metric(str(event.metrics.get("metric_name") or event.title))
    if event.category in {"risk_factors", "litigation"}:
        return "Regulatory / legal risk"
    if event.category in {"debt_liquidity"}:
        return "Balance sheet / liquidity"
    if event.category in {"dilution"}:
        return "Share count / dilution"
    if event.category in {"guidance", "guidance_shift", "guidance_specificity_change"}:
        return "Guidance / expectations"
    if event.category in {"tone_shift", "qa_evasion", "management_credibility", "strategic_priority_change"}:
        return "Management credibility / execution"
    if event.category in {"governance_change", "incentive_alignment", "shareholder_vote_signal"}:
        return "Governance / incentive alignment"
    return event.category.replace("_", " ").title()


def _driver_category(metric_name: str) -> str:
    return _driver_name_for_metric(metric_name).split(" / ")[0].lower().replace(" ", "_")


def _materiality(change_pct: float | None) -> str:
    if change_pct is None:
        return "Medium"
    magnitude = abs(change_pct)
    if magnitude >= 10:
        return "High"
    if magnitude >= 3:
        return "Medium"
    return "Low"


def _trend(change_pct: float | None) -> str:
    if change_pct is None:
        return "Unknown"
    if change_pct > 0:
        return "Improving"
    if change_pct < 0:
        return "Deteriorating"
    return "Stable"


def _why_metric_matters(metric_name: str, playbook: IndustryPlaybook) -> str:
    driver = _driver_name_for_metric(metric_name)
    if driver == "Revenue growth / demand":
        return "Revenue is the first bridge from evidence to EPS, FCF, and multiple durability."
    if driver == "Gross margin / mix":
        return "Margin changes can reprice operating leverage when they are durable rather than one-time."
    if driver == "Cash generation":
        return "Cash flow controls buybacks, debt capacity, and valuation support."
    if driver == "Balance sheet / liquidity":
        return "Leverage and liquidity can change downside risk, refinancing risk, and equity optionality."
    if driver == "Share count / dilution":
        return "Share count changes directly affect per-share value and capital-allocation quality."
    return f"This KPI should be tested against the {playbook.industry_label} playbook before becoming a thesis."


def _why_event_matters(event: ChangeEvent, playbook: IndustryPlaybook) -> str:
    if event.category in {"risk_factors", "litigation"}:
        return "Risk language matters only if it changes probability, severity, timing, or valuation multiple."
    if event.category in {"guidance", "guidance_shift", "guidance_specificity_change"}:
        return "Guidance is thesis-relevant when it changes consensus expectations or narrows the operating range."
    if event.category.startswith("management") or event.category in {"tone_shift", "qa_evasion"}:
        return "Management language is supporting evidence until corroborated by facts, revisions, or price reaction."
    return f"The signal should be tied to {', '.join(playbook.key_kpis[:3]) or 'industry KPIs'} before it is treated as material."


def _tokens(value: str) -> list[str]:
    return [token for token in re.split(r"[^a-z0-9]+", value.lower()) if token]
