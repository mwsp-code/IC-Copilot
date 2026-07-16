from __future__ import annotations

import csv
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .models import (
    CanonicalMetricDefinition,
    CanonicalMetricOntology,
    Citation,
    CompanyIdentity,
    CoverageCase,
    EntityResolution,
    FilingRecord,
    FinancialMetric,
    GlobalCoverageUniverse,
    MetricResolutionAudit,
    MetricResolutionItem,
    SecurityTypeProfile,
    SourceCoverageEntry,
    SourceCoverageMatrix,
)


LIST_SEPARATOR = "|"


BUILT_IN_COVERAGE_CASES: tuple[CoverageCase, ...] = (
    CoverageCase("AAPL", "Apple Inc.", "United States", "North America", "US", "NASDAQ", "common_stock", "Information Technology", "Consumer electronics", "SEC domestic issuer", "US GAAP", "USD", "09-30", primary_sources=["sec_edgar", "issuer_ir"], representative_dimensions=["US", "common_stock", "large_cap", "technology"]),
    CoverageCase("MSFT", "Microsoft Corporation", "United States", "North America", "US", "NASDAQ", "common_stock", "Information Technology", "Software / cloud", "SEC domestic issuer", "US GAAP", "USD", "06-30", primary_sources=["sec_edgar", "issuer_ir"], representative_dimensions=["US", "common_stock", "large_cap", "software"]),
    CoverageCase("NVDA", "NVIDIA Corporation", "United States", "North America", "US", "NASDAQ", "common_stock", "Information Technology", "Semiconductors", "SEC domestic issuer", "US GAAP", "USD", "01-31", primary_sources=["sec_edgar", "issuer_ir"], representative_dimensions=["US", "semiconductor", "inventory_cycle"]),
    CoverageCase("JPM", "JPMorgan Chase & Co.", "United States", "North America", "US", "NYSE", "common_stock", "Financials", "Bank", "SEC domestic issuer", "US GAAP", "USD", "12-31", primary_sources=["sec_edgar", "issuer_ir", "regulator_bank"], representative_dimensions=["bank", "credit_cycle"]),
    CoverageCase("XOM", "Exxon Mobil Corporation", "United States", "North America", "US", "NYSE", "common_stock", "Energy", "Integrated oil and gas", "SEC domestic issuer", "US GAAP", "USD", "12-31", primary_sources=["sec_edgar", "issuer_ir", "commodity_data"], representative_dimensions=["energy", "commodity_sensitive"]),
    CoverageCase("AMZN", "Amazon.com, Inc.", "United States", "North America", "US", "NASDAQ", "common_stock", "Consumer Discretionary", "Ecommerce / cloud", "SEC domestic issuer", "US GAAP", "USD", "12-31", primary_sources=["sec_edgar", "issuer_ir"], representative_dimensions=["US", "ecommerce", "cloud"]),
    CoverageCase("TSLA", "Tesla, Inc.", "United States", "North America", "US", "NASDAQ", "common_stock", "Consumer Discretionary", "Automobiles", "SEC domestic issuer", "US GAAP", "USD", "12-31", primary_sources=["sec_edgar", "issuer_ir", "vehicle_delivery_data"], representative_dimensions=["auto", "gross_margin_mix"]),
    CoverageCase("PLD", "Prologis, Inc.", "United States", "North America", "US", "NYSE", "reit", "Real Estate", "Industrial REIT", "SEC domestic REIT", "US GAAP", "USD", "12-31", primary_sources=["sec_edgar", "issuer_ir"], representative_dimensions=["REIT", "FFO", "occupancy"]),
    CoverageCase("BABA", "Alibaba Group Holding Limited", "China / Hong Kong", "Asia", "Cayman Islands / HK", "NYSE", "ADR", "Consumer Discretionary", "China internet / commerce / cloud", "SEC FPI plus HKEX/issuer 6-K", "IFRS / US SEC furnished reports", "CNY", "03-31", "9988", "HKEX", ["sec_edgar", "hkex_document", "issuer_ir", "cninfo_document"], ["ADR uses ordinary-share ratio and home-market filings."], ["ADR", "FPI", "China internet", "HK dual listing"]),
    CoverageCase("BYDDF", "BYD Company Limited", "China / Hong Kong", "Asia", "China / HK", "OTC", "OTC ADR / foreign ordinary", "Consumer Discretionary", "Automobiles / batteries", "HKEX/CNInfo official reports", "PRC GAAP / IFRS-style issuer report", "CNY", "12-31", "1211", "HKEX", ["hkex_document", "cninfo_document", "issuer_ir"], ["SEC companyfacts is not expected for this OTC security."], ["global_peer", "auto", "HKEX", "CNInfo"]),
    CoverageCase("ASML", "ASML Holding N.V.", "Netherlands", "Europe", "NL", "NASDAQ", "ADR/FPI", "Information Technology", "Semiconductor equipment", "SEC FPI plus ESEF", "IFRS", "EUR", "12-31", "ASML", "Euronext Amsterdam", ["sec_edgar", "esef_repository", "issuer_ir"], representative_dimensions=["Europe", "FPI", "semiconductor"]),
    CoverageCase("SAP", "SAP SE", "Germany", "Europe", "DE", "NYSE", "ADR/FPI", "Information Technology", "Enterprise software", "SEC FPI plus ESEF", "IFRS", "EUR", "12-31", "SAP", "Xetra", ["sec_edgar", "esef_repository", "issuer_ir"], representative_dimensions=["Europe", "software", "FPI"]),
    CoverageCase("NVO", "Novo Nordisk A/S", "Denmark", "Europe", "DK", "NYSE", "ADR/FPI", "Healthcare", "Pharmaceuticals", "SEC FPI plus ESEF", "IFRS", "DKK", "12-31", "NOVO B", "Nasdaq Copenhagen", ["sec_edgar", "esef_repository", "issuer_ir", "product_regulator"], representative_dimensions=["Europe", "healthcare", "product_approval"]),
    CoverageCase("TM", "Toyota Motor Corporation", "Japan", "Asia", "JP", "NYSE", "ADR/FPI", "Consumer Discretionary", "Automobiles", "SEC FPI plus EDINET", "IFRS / Japanese disclosure", "JPY", "03-31", "7203", "TSE", ["sec_edgar", "edinet_document", "issuer_ir"], representative_dimensions=["Japan", "auto", "ADR"]),
    CoverageCase("SONY", "Sony Group Corporation", "Japan", "Asia", "JP", "NYSE", "ADR/FPI", "Communication Services", "Consumer electronics / media", "SEC FPI plus EDINET", "IFRS", "JPY", "03-31", "6758", "TSE", ["sec_edgar", "edinet_document", "issuer_ir"], representative_dimensions=["Japan", "conglomerate"]),
    CoverageCase("INFY", "Infosys Limited", "India", "Asia", "IN", "NYSE", "ADR/FPI", "Information Technology", "IT services", "SEC FPI plus NSE/BSE", "IFRS / Ind AS", "INR", "03-31", "INFY", "NSE", ["sec_edgar", "nse_bse_document", "issuer_ir"], representative_dimensions=["India", "ADR", "services"]),
    CoverageCase("TSM", "Taiwan Semiconductor Manufacturing Company Limited", "Taiwan", "Asia", "TW", "NYSE", "ADR/FPI", "Information Technology", "Semiconductors / foundry", "SEC FPI plus TWSE/issuer", "IFRS", "TWD", "12-31", "2330", "TWSE", ["sec_edgar", "issuer_ir", "exchange_announcement"], representative_dimensions=["Taiwan", "ADR", "semiconductor"]),
    CoverageCase("BHP", "BHP Group Limited", "Australia", "APAC", "AU", "NYSE", "ADR/FPI", "Materials", "Diversified mining", "SEC FPI plus ASX", "IFRS", "USD", "06-30", "BHP", "ASX", ["sec_edgar", "asx_announcement", "issuer_ir", "commodity_data"], representative_dimensions=["Australia", "mining", "commodity_sensitive"]),
    CoverageCase("MELI", "MercadoLibre, Inc.", "Argentina / Uruguay", "LatAm", "US", "NASDAQ", "common_stock", "Consumer Discretionary", "LatAm ecommerce / fintech", "SEC domestic issuer", "US GAAP", "USD", "12-31", primary_sources=["sec_edgar", "issuer_ir", "fx_macro"], representative_dimensions=["LatAm", "ecommerce", "FX"]),
    CoverageCase("PBR", "Petroleo Brasileiro S.A. - Petrobras", "Brazil", "LatAm", "BR", "NYSE", "ADR/FPI", "Energy", "Integrated oil and gas", "SEC FPI plus CVM/B3", "IFRS", "BRL", "12-31", "PETR4", "B3", ["sec_edgar", "b3_cvm_document", "issuer_ir", "commodity_data"], representative_dimensions=["LatAm", "energy", "ADR"]),
    CoverageCase("2222.SE", "Saudi Arabian Oil Company", "Saudi Arabia", "MENA", "SA", "Tadawul", "common_stock", "Energy", "Integrated oil and gas", "Tadawul / issuer reports", "IFRS", "SAR", "12-31", "2222", "Tadawul", ["tadawul_announcement", "issuer_ir", "commodity_data"], representative_dimensions=["MENA", "energy", "local_common"]),
)


BUILT_IN_SOURCE_ENTRIES: tuple[SourceCoverageEntry, ...] = (
    SourceCoverageEntry("sec_edgar", "SEC EDGAR submissions and companyfacts", "US", "official_filings", "High", True, 1, "keyless_api", "public_metadata_and_filings", "https://www.sec.gov/search-filings/edgar-application-programming-interfaces", True, True, False, True, notes="Gold-standard source for US-listed domestic issuers and FPIs."),
    SourceCoverageEntry("issuer_ir", "Issuer investor relations documents", "global", "issuer_documents", "High", True, 1, "registered_seed_or_manual", "metadata_excerpt_or_user_supplied", supports_pdf=True, supports_html=True, notes="Use official issuer pages, releases, decks, and transcripts when registered or user supplied."),
    SourceCoverageEntry("hkex_document", "HKEXnews announcements and financial reports", "HK", "official_filings", "High", True, 1, "registered_adapter_or_manual", "metadata_excerpt_only", "https://www.hkexnews.hk/index.htm", supports_pdf=True, supports_html=True),
    SourceCoverageEntry("cninfo_document", "CNInfo official China filings", "CN", "official_filings", "High", True, 1, "registered_adapter_or_manual", "metadata_excerpt_only", "https://www.cninfo.com.cn/new/index", supports_pdf=True, supports_html=True),
    SourceCoverageEntry("companies_house", "Companies House company data", "UK", "official_filings", "Medium", True, 1, "api", "public_api", "https://developer.company-information.service.gov.uk/", supports_ixbrl=True, supports_pdf=True, supports_html=True),
    SourceCoverageEntry("edinet_document", "Japan EDINET/JFSA filings", "JP", "official_filings", "High", True, 1, "api_or_manual", "official_filings_metadata_excerpt", supports_xbrl=True, supports_ixbrl=True, supports_pdf=True),
    SourceCoverageEntry("esef_repository", "ESEF iXBRL repository / national OAM", "EU", "official_filings", "High", True, 1, "jurisdiction_adapter_or_manual", "official_filings_metadata_excerpt", supports_ixbrl=True, supports_html=True),
    SourceCoverageEntry("asx_announcement", "ASX announcements", "AU", "exchange_announcement", "Medium", True, 1, "registered_adapter_or_manual", "metadata_excerpt_only", supports_pdf=True, supports_html=True),
    SourceCoverageEntry("nse_bse_document", "NSE/BSE company announcements", "IN", "exchange_announcement", "Medium", True, 1, "registered_adapter_or_manual", "metadata_excerpt_only", supports_pdf=True, supports_html=True),
    SourceCoverageEntry("b3_cvm_document", "Brazil B3/CVM filings", "BR", "official_filings", "Medium", True, 1, "registered_adapter_or_manual", "metadata_excerpt_only", supports_pdf=True, supports_html=True),
    SourceCoverageEntry("tadawul_announcement", "Tadawul announcements", "SA", "exchange_announcement", "Medium", True, 1, "registered_adapter_or_manual", "metadata_excerpt_only", supports_pdf=True, supports_html=True),
    SourceCoverageEntry("openfigi", "OpenFIGI security identifier mapping", "global", "entity_mapping", "Medium", True, 2, "api", "identifier_mapping", "https://www.openfigi.com/api", notes="Security mapping context only; not thesis evidence."),
    SourceCoverageEntry("gleif_lei", "GLEIF LEI legal entity lookup", "global", "entity_mapping", "Medium", True, 2, "api", "identifier_mapping", "https://www.gleif.org/en/lei-data/gleif-api", notes="Legal entity identity context only."),
    SourceCoverageEntry("manual_csv", "Bring-your-own-data CSV import", "global", "manual_import", "Medium", True, 2, "local_file", "user_supplied", notes="Use for licensed reports, segment KPIs, consensus snapshots, and local official-source extracts."),
)


BUILT_IN_METRIC_DEFINITIONS: tuple[CanonicalMetricDefinition, ...] = (
    CanonicalMetricDefinition("Revenue", "core_financials", "currency", ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet", "Revenue"], required_for_drivers=["revenue_demand", "gross_margin_mix"], preferred_sources=["xbrl", "issuer_report"]),
    CanonicalMetricDefinition("Cost of Revenue", "core_financials", "currency", ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfSales"], required_for_drivers=["gross_margin_mix"], preferred_sources=["xbrl", "issuer_report"]),
    CanonicalMetricDefinition("Gross Profit", "core_financials", "currency", ["GrossProfit", "GrossIncome"], "Revenue - Cost of Revenue", ["gross_margin_mix"], preferred_sources=["xbrl", "issuer_report"]),
    CanonicalMetricDefinition("Gross Margin", "core_financials", "percent", ["GrossMargin"], "Gross Profit / Revenue", ["gross_margin_mix"], preferred_sources=["derived"]),
    CanonicalMetricDefinition("Operating Income", "core_financials", "currency", ["OperatingIncomeLoss", "OperatingProfitLoss"], required_for_drivers=["operating_expense", "margin"], preferred_sources=["xbrl", "issuer_report"]),
    CanonicalMetricDefinition("Net Income", "core_financials", "currency", ["NetIncomeLoss", "ProfitLoss"], required_for_drivers=["eps"], preferred_sources=["xbrl", "issuer_report"]),
    CanonicalMetricDefinition("Free Cash Flow", "core_financials", "currency", ["FreeCashFlow"], "Operating Cash Flow - Capital Expenditure", ["cash_generation", "valuation"], preferred_sources=["derived"]),
    CanonicalMetricDefinition("Net Debt", "core_financials", "currency", ["NetDebt"], "Long-term Debt + Current Debt - Cash", ["leverage_refinancing"], preferred_sources=["derived"]),
    CanonicalMetricDefinition("CET1 Ratio", "bank_kpi", "percent", ["CommonEquityTier1CapitalRatio"], required_for_drivers=["bank_credit_cycle"], sector_templates=["Bank"]),
    CanonicalMetricDefinition("Combined Ratio", "insurance_kpi", "percent", ["CombinedRatio"], required_for_drivers=["insurance_reserve_shock"], sector_templates=["Insurer"]),
    CanonicalMetricDefinition("FFO", "reit_kpi", "currency", ["FundsFromOperations"], required_for_drivers=["reit_occupancy_rate"], sector_templates=["REIT"]),
    CanonicalMetricDefinition("Deliveries", "auto_kpi", "units", ["VehicleDeliveries", "Deliveries"], required_for_drivers=["gross_margin_mix", "revenue_demand"], sector_templates=["Automobiles"]),
)


BUILT_IN_SECURITY_PROFILES: tuple[SecurityTypeProfile, ...] = (
    SecurityTypeProfile("common_stock", "Ordinary listed equity.", ["ticker", "exchange", "fiscal year-end"], ["Use local reporting currency unless ADR/FPI overlay applies."]),
    SecurityTypeProfile("ADR", "US-listed American depositary receipt.", ["ADR ratio", "home ticker", "ordinary-share basis", "reporting currency"], ["Normalize ordinary shares vs ADS.", "Translate home reporting currency separately from ADR price currency."]),
    SecurityTypeProfile("ADR/FPI", "Foreign private issuer with US listing.", ["20-F/40-F/6-K availability", "home exchange", "reporting standard"], ["Use SEC FPI filings plus home-market official documents where needed."]),
    SecurityTypeProfile("OTC ADR / foreign ordinary", "OTC-traded foreign security.", ["home listing", "issuer official reports", "security basis"], ["Do not expect SEC companyfacts.", "Use official home-market filings and manual imports."]),
    SecurityTypeProfile("reit", "Real estate investment trust.", ["REIT status", "property type", "FFO/AFFO basis"], ["Require FFO/AFFO and occupancy metrics before valuation."]),
    SecurityTypeProfile("bank", "Bank equity or ADR.", ["bank regulator", "capital ratio basis", "loan/provision definitions"], ["Use CET1, NIM, provisions, deposits, tangible book, and ROTCE."]),
)


def build_global_coverage_universe(path: Path | None = None) -> GlobalCoverageUniverse:
    cases = _load_coverage_cases(path or config.COVERAGE_UNIVERSE_CSV)
    gaps = [] if cases else ["No coverage cases were available from CSV or built-ins."]
    return GlobalCoverageUniverse(
        status="Available" if cases else "Unavailable",
        generated_at=_now(),
        cases=cases,
        representative_geographies=_unique(case.geography for case in cases),
        representative_security_types=_unique(case.security_type for case in cases),
        representative_sectors=_unique(case.sector for case in cases),
        data_gaps=gaps,
    )


def coverage_case_for(
    ticker: str,
    identity: CompanyIdentity | None = None,
    entity_resolution: EntityResolution | None = None,
    filings: list[FilingRecord] | None = None,
    path: Path | None = None,
) -> CoverageCase:
    normalized = ticker.upper()
    for case in build_global_coverage_universe(path).cases:
        aliases = {case.ticker.upper(), case.home_ticker.upper()}
        if normalized in aliases:
            return case
    forms = sorted({filing.form for filing in filings or []})
    is_fpi = any(form in {"20-F", "40-F", "6-K"} for form in forms)
    name = identity.name if identity else normalized
    exchange = (entity_resolution.exchange if entity_resolution else identity.exchange if identity else "Unknown")
    security_type = "ADR/FPI" if is_fpi else "common_stock"
    filing_regime = "SEC FPI" if is_fpi else "SEC domestic issuer" if forms else "Unclassified / needs source mapping"
    sources = ["sec_edgar", "issuer_ir"] if forms else ["issuer_ir", "manual_csv", "openfigi", "gleif_lei"]
    gaps = [] if forms else ["No known coverage fixture; add the ticker to coverage_universe.csv for richer jurisdiction and security-type checks."]
    return CoverageCase(
        normalized,
        name,
        "United States" if exchange in {"US", "NYSE", "NASDAQ"} else "Unknown",
        "North America" if exchange in {"US", "NYSE", "NASDAQ"} else "Unknown",
        "US" if exchange in {"US", "NYSE", "NASDAQ"} else "Unknown",
        exchange or "Unknown",
        security_type,
        "Unknown",
        "Unknown",
        filing_regime,
        "US GAAP / IFRS" if is_fpi else "US GAAP",
        "USD",
        primary_sources=sources,
        representative_dimensions=[security_type, filing_regime],
        data_gaps=gaps,
        profile_source="inferred",
    )


def source_coverage_matrix_for(case: CoverageCase, filings: list[FilingRecord] | None = None) -> SourceCoverageMatrix:
    configured = _load_source_entries(config.JURISDICTION_SOURCES_CSV)
    by_type = {entry.source_type: entry for entry in configured}
    entries: list[SourceCoverageEntry] = []
    forms = {filing.form for filing in filings or []}
    for source_type in _unique(list(case.primary_sources) + ["openfigi", "gleif_lei", "manual_csv"]):
        template = by_type.get(source_type)
        if not template:
            entries.append(SourceCoverageEntry(
                source_type,
                source_type.replace("_", " ").title(),
                case.jurisdiction,
                "unregistered_source",
                "Low",
                False,
                4,
                "not_implemented",
                "unknown",
                status="source unavailable",
                blocker="Source type is not registered in the coverage matrix.",
            ))
            continue
        status = _entry_status(template, forms)
        blocker = "" if status in {"validated", "document found", "source not attempted"} else "No matching filing/document is attached to this run."
        entries.append(replace(template, status=status, blocker=blocker))
    missing = [entry.source_type for entry in entries if entry.status in {"source unavailable", "source not attempted"} and entry.priority == "High"]
    return SourceCoverageMatrix(
        ticker=case.ticker,
        status="Coverage gaps" if missing else "Available",
        summary=(
            f"{len(entries)} registered source(s) mapped for {case.jurisdiction}/{case.security_type}; "
            f"{len(missing)} high-priority source(s) still need evidence."
        ),
        entries=entries,
        source_families=_unique(entry.source_family for entry in entries),
        data_gaps=[f"High-priority source not validated: {item}" for item in missing],
    )


def build_canonical_metric_ontology(path: Path | None = None) -> CanonicalMetricOntology:
    definitions = _load_metric_definitions(path or config.METRIC_ONTOLOGY_CSV)
    return CanonicalMetricOntology(
        status="Available" if definitions else "Unavailable",
        definitions=definitions,
        data_gaps=[] if definitions else ["No metric ontology definitions were available."],
    )


def build_metric_resolution_audit(
    ticker: str,
    metrics: list[FinancialMetric],
    coverage_case: CoverageCase | None = None,
    ontology: CanonicalMetricOntology | None = None,
) -> MetricResolutionAudit:
    ontology = ontology or build_canonical_metric_ontology()
    by_name = {metric.name: metric for metric in metrics}
    items: list[MetricResolutionItem] = []
    for definition in ontology.definitions:
        direct = by_name.get(definition.metric)
        if direct:
            items.append(_direct_item(definition.metric, direct))
            continue
        derived = _derive_metric(definition.metric, by_name)
        if derived:
            items.append(derived)
            continue
        if _metric_relevant(definition, coverage_case):
            items.append(MetricResolutionItem(
                metric=definition.metric,
                status="metric missing",
                resolution_method="unresolved",
                formula=definition.formula,
                blocker=_missing_blocker(definition),
            ))
    missing = [item.metric for item in items if item.status == "metric missing"]
    derived = [item.metric for item in items if item.status == "metric derived"]
    validated = [item for item in items if item.status in {"validated", "metric derived"}]
    status = "Available" if validated and not missing else "Partial" if validated else "Unavailable"
    return MetricResolutionAudit(
        ticker=ticker.upper(),
        status=status,
        summary=f"{len(validated)} canonical metric(s) resolved; {len(derived)} derived; {len(missing)} missing.",
        items=items,
        missing_core_metrics=missing,
        derived_metrics=derived,
        data_gaps=[f"Canonical metric unresolved: {name}" for name in missing],
    )


def security_type_profile_for(security_type: str) -> SecurityTypeProfile:
    normalized = security_type.lower()
    for profile in _load_security_profiles(config.SECURITY_TYPE_PROFILES_CSV):
        if profile.security_type.lower() == normalized:
            return profile
    return SecurityTypeProfile(
        security_type or "unknown",
        "Unregistered security type.",
        data_gaps=["Add this security type to security_type_profiles.csv with required identity checks and normalization rules."],
    )


def global_coverage_work_order_items(
    case: CoverageCase,
    matrix: SourceCoverageMatrix,
    audit: MetricResolutionAudit,
) -> list[tuple[str, str, str, str, str, str, list[str], list[str]]]:
    rows: list[tuple[str, str, str, str, str, str, list[str], list[str]]] = []
    for entry in matrix.entries:
        if entry.status in {"source unavailable", "parse failed"} or (entry.priority == "High" and entry.status == "source not attempted"):
            rows.append((
                entry.priority if entry.priority in {"High", "Medium", "Low"} else "Medium",
                f"Global coverage: {entry.label}",
                f"Validate {entry.label} for {case.company_name}.",
                entry.source_type,
                f"{entry.source_family} evidence with source URL, observed time, licensing policy, period, and citation.",
                entry.blocker or f"{entry.source_type} has not produced validated evidence for this run.",
                [
                    "Source type is registered and allowed for the ticker jurisdiction.",
                    "Citation, publication/source date, observed time, source tier, and licensing policy are retained.",
                ],
                [
                    "Source is unofficial but treated as primary evidence.",
                    "LLM suggests an arbitrary source URL outside the registry.",
                ],
            ))
    for metric in audit.items:
        if metric.status == "metric missing":
            rows.append((
                "High" if metric.metric in {"Revenue", "Gross Profit", "Operating Income", "Net Income"} else "Medium",
                f"Metric resolution: {metric.metric}",
                f"Resolve canonical metric {metric.metric} for {case.company_name}.",
                "canonical_metric_ontology",
                "Direct tagged metric, valid alias, accepted derivation, or explicit Unknown.",
                metric.blocker or "Metric is missing after direct, alias, and derivation checks.",
                [
                    "Metric is period-aligned to the focal event or marked stale.",
                    "Unit, currency, formula, and source metric provenance are preserved.",
                ],
                [
                    "Missing values are converted to zero.",
                    "Derived metric lacks source inputs or compatible units.",
                ],
            ))
    return rows


def _direct_item(metric_name: str, metric: FinancialMetric) -> MetricResolutionItem:
    return MetricResolutionItem(
        metric=metric_name,
        status="validated",
        resolution_method="direct",
        value=metric.value,
        unit=metric.unit,
        currency=metric.unit if metric.unit.upper() in {"USD", "CNY", "EUR", "JPY", "INR", "TWD", "AUD", "BRL", "SAR", "DKK"} else "",
        period_end=metric.period_end,
        source_metric=metric.name,
        source_type=metric.source_kind,
        citation=Citation(
            source=metric.source_kind or "financial metric",
            url=metric.source_url or "",
            filed=metric.filed,
            form=metric.form,
            period_end=metric.period_end,
            source_tier=1,
        ) if metric.source_url or metric.filed else None,
    )


def _derive_metric(metric_name: str, by_name: dict[str, FinancialMetric]) -> MetricResolutionItem | None:
    if metric_name == "Gross Profit":
        revenue, cost = by_name.get("Revenue"), by_name.get("Cost of Revenue")
        if revenue and cost and _compatible(revenue, cost):
            return _derived_item(metric_name, revenue.value - abs(cost.value), revenue.unit, revenue.period_end, "Revenue - Cost of Revenue", "Revenue; Cost of Revenue")
    if metric_name == "Gross Margin":
        gross, revenue = by_name.get("Gross Profit"), by_name.get("Revenue")
        if not gross:
            derived_gross = _derive_metric("Gross Profit", by_name)
            if derived_gross and derived_gross.value is not None:
                gross = FinancialMetric("Gross Profit", derived_gross.value, derived_gross.unit, derived_gross.period_end or "")
        if gross and revenue and revenue.value:
            return _derived_item(metric_name, gross.value / revenue.value * 100, "%", revenue.period_end, "Gross Profit / Revenue", "Gross Profit; Revenue")
    if metric_name == "Free Cash Flow":
        ocf, capex = by_name.get("Operating Cash Flow"), by_name.get("Capital Expenditure")
        if ocf and capex and _compatible(ocf, capex):
            return _derived_item(metric_name, ocf.value - abs(capex.value), ocf.unit, ocf.period_end, "Operating Cash Flow - Capital Expenditure", "Operating Cash Flow; Capital Expenditure")
    if metric_name == "Net Debt":
        cash = by_name.get("Cash")
        long_debt = by_name.get("Long-term Debt")
        current_debt = by_name.get("Current Debt")
        if cash and (long_debt or current_debt):
            unit = (long_debt or current_debt or cash).unit
            debt_value = (long_debt.value if long_debt and long_debt.unit == unit else 0) + (current_debt.value if current_debt and current_debt.unit == unit else 0)
            if cash.unit == unit:
                return _derived_item(metric_name, debt_value - cash.value, unit, cash.period_end, "Long-term Debt + Current Debt - Cash", "Long-term Debt; Current Debt; Cash")
    return None


def _derived_item(metric_name: str, value: float, unit: str, period_end: str, formula: str, source_metric: str) -> MetricResolutionItem:
    return MetricResolutionItem(
        metric=metric_name,
        status="metric derived",
        resolution_method="derived_formula",
        value=value,
        unit=unit,
        currency=unit if unit.upper() in {"USD", "CNY", "EUR", "JPY", "INR", "TWD", "AUD", "BRL", "SAR", "DKK"} else "",
        period_end=period_end,
        source_metric=source_metric,
        formula=formula,
        source_type="canonical_metric_ontology",
    )


def _compatible(left: FinancialMetric, right: FinancialMetric) -> bool:
    return bool(left.unit == right.unit and left.period_end == right.period_end)


def _metric_relevant(definition: CanonicalMetricDefinition, case: CoverageCase | None) -> bool:
    if not case:
        return definition.family == "core_financials"
    sector_text = f"{case.sector} {case.industry} {case.security_type}".lower()
    if definition.family == "core_financials":
        return True
    return any(template.lower() in sector_text for template in definition.sector_templates)


def _missing_blocker(definition: CanonicalMetricDefinition) -> str:
    if definition.formula:
        return f"Missing direct tag and cannot derive because formula inputs are incomplete: {definition.formula}."
    return "Missing direct tagged metric or accepted alias in the current source set."


def _entry_status(entry: SourceCoverageEntry, forms: set[str]) -> str:
    if entry.source_type == "sec_edgar":
        return "validated" if forms else "source not attempted"
    if entry.source_type == "manual_csv":
        return "source not attempted"
    return "source not attempted"


def _load_coverage_cases(path: Path) -> list[CoverageCase]:
    rows = _read_csv_rows(path)
    if not rows:
        return list(BUILT_IN_COVERAGE_CASES)
    cases: list[CoverageCase] = []
    for row in rows:
        cases.append(CoverageCase(
            ticker=row.get("ticker", "").upper(),
            company_name=row.get("company_name", ""),
            geography=row.get("geography", ""),
            region=row.get("region", ""),
            jurisdiction=row.get("jurisdiction", ""),
            exchange=row.get("exchange", ""),
            security_type=row.get("security_type", ""),
            sector=row.get("sector", ""),
            industry=row.get("industry", ""),
            filing_regime=row.get("filing_regime", ""),
            reporting_standard=row.get("reporting_standard", ""),
            currency=row.get("currency", ""),
            fiscal_year_end=row.get("fiscal_year_end", ""),
            home_ticker=row.get("home_ticker", ""),
            home_exchange=row.get("home_exchange", ""),
            primary_sources=_split(row.get("primary_sources", "")),
            source_notes=_split(row.get("source_notes", "")),
            representative_dimensions=_split(row.get("representative_dimensions", "")),
            data_gaps=_split(row.get("data_gaps", "")),
            profile_source="csv",
        ))
    return [case for case in cases if case.ticker]


def _load_source_entries(path: Path) -> list[SourceCoverageEntry]:
    rows = _read_csv_rows(path)
    if not rows:
        return list(BUILT_IN_SOURCE_ENTRIES)
    entries: list[SourceCoverageEntry] = []
    for row in rows:
        entries.append(SourceCoverageEntry(
            source_type=row.get("source_type", ""),
            label=row.get("label", ""),
            jurisdiction=row.get("jurisdiction", "global"),
            source_family=row.get("source_family", "core"),
            priority=row.get("priority", "Medium"),
            official=_truthy(row.get("official", "true")),
            source_tier=_safe_int(row.get("source_tier"), 3),
            access_mode=row.get("access_mode", ""),
            licensing_policy=row.get("licensing_policy", "metadata_excerpt_only"),
            url=row.get("url", ""),
            supports_xbrl=_truthy(row.get("supports_xbrl", "")),
            supports_ixbrl=_truthy(row.get("supports_ixbrl", "")),
            supports_pdf=_truthy(row.get("supports_pdf", "")),
            supports_html=_truthy(row.get("supports_html", "")),
            notes=row.get("notes", ""),
        ))
    return [entry for entry in entries if entry.source_type]


def _load_metric_definitions(path: Path) -> list[CanonicalMetricDefinition]:
    rows = _read_csv_rows(path)
    if not rows:
        return list(BUILT_IN_METRIC_DEFINITIONS)
    definitions: list[CanonicalMetricDefinition] = []
    for row in rows:
        definitions.append(CanonicalMetricDefinition(
            metric=row.get("metric", ""),
            family=row.get("family", "core_financials"),
            unit_type=row.get("unit_type", ""),
            aliases=_split(row.get("aliases", "")),
            formula=row.get("formula", ""),
            required_for_drivers=_split(row.get("required_for_drivers", "")),
            sector_templates=_split(row.get("sector_templates", "")),
            preferred_sources=_split(row.get("preferred_sources", "")),
        ))
    return [definition for definition in definitions if definition.metric]


def _load_security_profiles(path: Path) -> list[SecurityTypeProfile]:
    rows = _read_csv_rows(path)
    if not rows:
        return list(BUILT_IN_SECURITY_PROFILES)
    profiles: list[SecurityTypeProfile] = []
    for row in rows:
        profiles.append(SecurityTypeProfile(
            security_type=row.get("security_type", ""),
            description=row.get("description", ""),
            required_identity_checks=_split(row.get("required_identity_checks", "")),
            normalization_rules=_split(row.get("normalization_rules", "")),
            data_gaps=_split(row.get("data_gaps", "")),
        ))
    return [profile for profile in profiles if profile.security_type]


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    except OSError:
        return []


def _split(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split(LIST_SEPARATOR) if item.strip()]


def _truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _safe_int(value: object, default: int) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def _unique(values) -> list[str]:
    seen: set[str] = set()
    rows: list[str] = []
    for value in values:
        if not value:
            continue
        if value not in seen:
            rows.append(value)
            seen.add(value)
    return rows


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
