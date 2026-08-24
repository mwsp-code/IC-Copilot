from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Tuple


FINANCIAL_FACT_SERIES_SCHEMA_VERSION = "financial-fact-series-v1"
CASH_CONVERSION_ANALYSIS_VERSION = "cash-conversion-recurrence-v1"


@dataclass(frozen=True)
class FinancialFactSeriesQuery:
    ticker: str
    security_key: str
    normalized_metrics: Tuple[str, ...]
    data_cutoff: str = ""
    comparison_periods: int = 2
    period_basis: str = "comparable_ttm"
    forms: Tuple[str, ...] = ("10-K", "10-Q", "20-F", "40-F")
    schema_version: str = FINANCIAL_FACT_SERIES_SCHEMA_VERSION


@dataclass(frozen=True)
class FinancialFactObservation:
    source_id: str
    ticker: str
    normalized_metric: str
    taxonomy: str
    concept: str
    value: float
    unit: str
    currency: str
    period_start: str
    period_end: str
    duration_days: int | None
    fiscal_period: str
    fiscal_year: int | None
    form: str
    filed: str
    accession: str
    source_url: str
    source_snapshot_hash: str
    no_lookahead_safe: bool
    evidence_id: str = ""
    schema_version: str = FINANCIAL_FACT_SERIES_SCHEMA_VERSION


@dataclass(frozen=True)
class PeriodAlignedFinancialSeries:
    ticker: str
    data_cutoff: str
    observations: Tuple[FinancialFactObservation, ...]
    source_snapshot_hash: str
    status: str = "available"
    gaps: Tuple[str, ...] = ()
    schema_version: str = FINANCIAL_FACT_SERIES_SCHEMA_VERSION


class FinancialFactSeriesPort(Protocol):
    def query(self, query: FinancialFactSeriesQuery) -> PeriodAlignedFinancialSeries: ...


@dataclass(frozen=True)
class CashConversionComponent:
    key: str
    label: str
    value: float | None
    unit: str = "USD"
    status: str = "available"
    evidence_ids: Tuple[str, ...] = ()
    formula: str = ""


@dataclass(frozen=True)
class CashConversionPeriodBridge:
    period_start: str
    period_end: str
    duration_days: int | None
    net_income: float | None
    operating_cash_flow: float | None
    operating_cash_flow_to_net_income: float | None
    working_capital_change: float | None
    cash_taxes: float | None
    capital_expenditure: float | None
    free_cash_flow: float | None
    free_cash_flow_to_net_income: float | None
    debt_issued: float | None
    debt_repaid: float | None
    dividends: float | None
    repurchases: float | None
    cash_after_dividends_and_repurchases: float | None
    components: Tuple[CashConversionComponent, ...] = ()
    component_coverage: Tuple[str, ...] = ()
    missing_components: Tuple[str, ...] = ()
    evidence_ids: Tuple[str, ...] = ()
    formula: str = ""


@dataclass(frozen=True)
class CashConversionAssessment:
    assessment: str
    confidence: str
    direct_answer: str
    strongest_support: str
    strongest_limitation: str
    comparison_periods: int
    coverage_ratio: float
    material_gaps: Tuple[str, ...] = ()


@dataclass(frozen=True)
class CashConversionRecurrenceBridge:
    ticker: str
    status: str
    periods: Tuple[CashConversionPeriodBridge, ...]
    assessment: CashConversionAssessment
    data_cutoff: str
    source_snapshot_hash: str
    evidence_hash: str
    intent_id: str = ""
    qa_freshness: str = "not_reviewed"
    answer_basis: str = "deterministic_financial_bridge"
    analysis_version: str = CASH_CONVERSION_ANALYSIS_VERSION
    schema_version: str = FINANCIAL_FACT_SERIES_SCHEMA_VERSION
