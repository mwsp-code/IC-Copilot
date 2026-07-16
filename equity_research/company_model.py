from __future__ import annotations

import hashlib

from .models import (
    CompanyIdentity,
    CompanyModelCase,
    CompanyModelWorkspace,
    FinancialMetric,
    ModelAssumption,
    ModelLineItem,
    ModelSensitivityPoint,
    ValuationResult,
)


STATEMENT_MAP = {
    "Revenue": "Income statement",
    "Gross Profit": "Income statement",
    "Operating Income": "Income statement",
    "EBITDA": "Income statement",
    "Net Income": "Income statement",
    "EPS": "Income statement",
    "Cash": "Balance sheet",
    "Current Debt": "Balance sheet",
    "Long-term Debt": "Balance sheet",
    "Total Assets": "Balance sheet",
    "Stockholders' Equity": "Balance sheet",
    "Shares": "Balance sheet",
    "Operating Cash Flow": "Cash-flow statement",
    "Capital Expenditure": "Cash-flow statement",
    "Dividends Paid": "Cash-flow statement",
    "Share Repurchases": "Cash-flow statement",
    "Interest Expense": "Debt and financing",
}


def build_company_model_workspace(
    identity: CompanyIdentity,
    metrics: list[FinancialMetric],
    valuation: ValuationResult,
) -> CompanyModelWorkspace:
    latest = _latest_metrics(metrics)
    historicals = [
        ModelLineItem(
            statement=STATEMENT_MAP[name],
            metric=name,
            period=metric.period_end,
            value=metric.value,
            unit=metric.unit,
            source=metric.source_url or metric.source_kind or metric.form or "normalized filing fact",
            provenance="source" if metric.source_kind != "derived" else "formula",
            formula="Direct normalized source field" if metric.source_kind != "derived" else "Canonical metric derivation",
            confidence="High" if metric.source_kind in {"companyfacts", "inline_xbrl"} else "Medium",
        )
        for name, metric in latest.items()
        if name in STATEMENT_MAP
    ]
    revenue = latest.get("Revenue")
    operating_income = latest.get("Operating Income")
    net_income = latest.get("Net Income")
    operating_cash = latest.get("Operating Cash Flow")
    capex = latest.get("Capital Expenditure")
    shares = latest.get("Shares")
    base_growth = _bounded(revenue.yoy_change_pct if revenue else None, -20.0, 35.0, 5.0)
    operating_margin = _ratio(operating_income, revenue)
    net_margin = _ratio(net_income, revenue)
    fcf = None
    fcf_margin = None
    if operating_cash and capex and _same_unit(operating_cash, capex):
        fcf = operating_cash.value - abs(capex.value)
        if revenue and revenue.value:
            fcf_margin = fcf / revenue.value * 100

    assumptions = [
        _assumption("revenue_growth", "Revenue growth", "Base", base_growth, "%", "formula", "Latest comparable filing facts", "Latest reported year-over-year growth, bounded for scenario construction"),
        _assumption("operating_margin", "Operating margin", "Base", operating_margin, "%", "formula", "Revenue and operating income filing facts", "Operating income / revenue"),
        _assumption("net_margin", "Net margin", "Base", net_margin, "%", "formula", "Revenue and net income filing facts", "Net income / revenue"),
        _assumption("fcf_margin", "Free-cash-flow margin", "Base", fcf_margin, "%", "formula", "Operating cash flow and capex filing facts", "(Operating cash flow - abs(capex)) / revenue"),
    ]
    cases = _build_cases(revenue, base_growth, operating_margin, net_margin, fcf_margin, valuation)
    sensitivity = _build_sensitivity(revenue, shares, base_growth, net_margin, valuation)
    segment_rows = [row for row in historicals if "segment" in row.metric.lower()]
    debt_rows = [
        row for row in historicals
        if row.metric in {"Cash", "Current Debt", "Long-term Debt", "Interest Expense", "Operating Cash Flow"}
    ]
    required = [
        "Revenue", "Operating Income", "Net Income", "Operating Cash Flow",
        "Capital Expenditure", "Cash", "Current Debt", "Long-term Debt",
        "Interest Expense", "Shares",
    ]
    gaps = [f"{name} is unavailable or not normalized." for name in required if name not in latest]
    if not segment_rows:
        gaps.append("Segment revenue and margin rows are not yet normalized from issuer segment tables.")
    if not any("maturity" in row.metric.lower() for row in historicals):
        gaps.append("Debt maturity schedule requires tagged footnote or table extraction.")
    usable = sum(1 for row in historicals if row.value is not None)
    status = "Auditable model available" if usable >= 7 and cases else "Partial model"
    return CompanyModelWorkspace(
        ticker=identity.ticker,
        status=status,
        summary=(
            f"{usable} normalized historical line item(s), {len(cases)} scenario case(s), and "
            f"{len(sensitivity)} sensitivity point(s). Assumptions retain source/formula provenance."
        ),
        currency=_currency(revenue.unit if revenue else valuation.currency),
        historicals=historicals,
        assumptions=assumptions,
        cases=cases,
        sensitivity=sensitivity,
        segment_rows=segment_rows,
        debt_rows=debt_rows,
        data_gaps=gaps,
    )


def _build_cases(revenue, growth, op_margin, net_margin, fcf_margin, valuation):
    valuation_cases = {case.name.lower(): case for case in valuation.cases}
    rows = []
    for name, growth_delta, margin_delta in (
        ("Bear", -5.0, -3.0),
        ("Base", 0.0, 0.0),
        ("Bull", 5.0, 3.0),
    ):
        case_growth = growth + growth_delta
        forecast_revenue = revenue.value * (1 + case_growth / 100) if revenue else None
        case_op_margin = op_margin + margin_delta if op_margin is not None else None
        case_net_margin = net_margin + margin_delta * 0.7 if net_margin is not None else None
        case_fcf_margin = fcf_margin + margin_delta * 0.8 if fcf_margin is not None else None
        valuation_case = valuation_cases.get(name.lower())
        rows.append(CompanyModelCase(
            name=name,
            revenue=forecast_revenue,
            operating_margin_pct=case_op_margin,
            net_income=(forecast_revenue * case_net_margin / 100) if forecast_revenue is not None and case_net_margin is not None else None,
            free_cash_flow=(forecast_revenue * case_fcf_margin / 100) if forecast_revenue is not None and case_fcf_margin is not None else None,
            fair_value=valuation_case.fair_value if valuation_case else None,
            assumptions=[
                f"Revenue growth {case_growth:.1f}%.",
                f"Operating margin {case_op_margin:.1f}%." if case_op_margin is not None else "Operating margin unavailable.",
                "Fair value comes only from the existing internal valuation case.",
            ],
        ))
    return rows


def _build_sensitivity(revenue, shares, growth, net_margin, valuation):
    if not revenue or not shares or shares.value <= 0 or net_margin is None:
        return []
    base_case = next((case for case in valuation.cases if case.name.lower() == "base" and case.fair_value), None)
    current_eps = revenue.value * net_margin / 100 / shares.value
    base_multiple = base_case.fair_value / current_eps if base_case and current_eps > 0 else None
    if not base_multiple or base_multiple <= 0:
        return []
    rows = []
    for growth_delta in (-5.0, 0.0, 5.0):
        for margin_delta in (-2.0, 0.0, 2.0):
            forecast_revenue = revenue.value * (1 + (growth + growth_delta) / 100)
            forecast_eps = forecast_revenue * (net_margin + margin_delta) / 100 / shares.value
            rows.append(ModelSensitivityPoint(
                row_label=f"Growth {growth + growth_delta:.1f}%",
                column_label=f"Net margin {net_margin + margin_delta:.1f}%",
                value=forecast_eps * base_multiple,
            ))
    return rows


def _assumption(key, name, case, value, unit, provenance, source, formula):
    raw = f"{key}|{case}|{source}"
    return ModelAssumption(
        assumption_id=hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12],
        name=name,
        case=case,
        value=value,
        unit=unit,
        provenance=provenance,
        source=source,
        formula=formula,
        editable=True,
    )


def _latest_metrics(metrics):
    result = {}
    for metric in sorted(metrics, key=lambda row: (row.period_end or "", row.filed or ""), reverse=True):
        result.setdefault(metric.name, metric)
    return result


def _ratio(numerator, denominator):
    if not numerator or not denominator or not denominator.value or not _same_unit(numerator, denominator):
        return None
    return numerator.value / denominator.value * 100


def _same_unit(left, right):
    return _currency(left.unit) == _currency(right.unit)


def _currency(unit):
    value = (unit or "USD").upper()
    for code in ("USD", "CNY", "HKD", "EUR", "GBP", "JPY", "KRW", "TWD", "INR", "CAD", "AUD"):
        if code in value:
            return code
    return value


def _bounded(value, low, high, default):
    if value is None:
        return default
    return max(low, min(high, value))
