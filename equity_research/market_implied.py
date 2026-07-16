from __future__ import annotations

from .adr_profiles import adr_profile_for
from .models import (
    CompanyIdentity,
    CompanyModelWorkspace,
    FinancialMetric,
    MarketImpliedAssumption,
    MarketImpliedExpectation,
    MarketImpliedExpectations,
    ValuationResult,
)
from .valuation import classify_template


_NON_FINANCIAL_DEFAULTS = {
    "discount_rate_pct": 10.0,
    "terminal_growth_pct": 3.0,
    "forecast_years": 5.0,
    "revenue_growth_pct": 5.0,
}
_BANK_DEFAULTS = {
    "sustainable_roe_anchor_pct": 10.0,
    "pb_roe_sensitivity_pct": 4.0,
}


def _assumptions_for_template(
    template: str,
    values: dict[str, float],
) -> list[MarketImpliedAssumption]:
    if template == "Bank":
        definitions = [
            ("sustainable_roe_anchor_pct", "Sustainable ROE anchor", "%", 0.0, 30.0, 0.5,
             "Editable P/B-to-ROE heuristic assumption"),
            ("pb_roe_sensitivity_pct", "P/B-to-ROE sensitivity", "percentage points", 0.0, 20.0, 0.5,
             "ROE change associated with each turn of P/B above or below 1x"),
        ]
        defaults = _BANK_DEFAULTS
    elif template == "Non-financial":
        definitions = [
            ("discount_rate_pct", "Discount rate", "%", 1.0, 30.0, 0.5,
             "Editable reverse-DCF assumption"),
            ("terminal_growth_pct", "Terminal growth", "%", -5.0, 10.0, 0.25,
             "Editable reverse-DCF assumption; must remain below the discount rate"),
            ("forecast_years", "Explicit forecast period", "years", 1.0, 10.0, 1.0,
             "Reverse-DCF horizon"),
            ("revenue_growth_pct", "Revenue growth for implied-margin case", "%", -20.0, 40.0, 0.5,
             "Separates the growth and cash-margin assumptions embedded in price"),
        ]
        defaults = _NON_FINANCIAL_DEFAULTS
    else:
        return []
    return [
        MarketImpliedAssumption(
            name=label,
            value=values.get(key, defaults[key]),
            unit=unit,
            provenance="user_override" if key in values else "illustrative_default",
            source=source,
            editable=True,
            key=key,
            minimum=minimum,
            maximum=maximum,
            step=step,
        )
        for key, label, unit, minimum, maximum, step, source in definitions
    ]


def _validated_assumption_overrides(template: str, raw: dict[str, float]) -> dict[str, float]:
    definitions = {
        "Non-financial": {
            "discount_rate_pct": (1.0, 30.0),
            "terminal_growth_pct": (-5.0, 10.0),
            "forecast_years": (1.0, 10.0),
            "revenue_growth_pct": (-20.0, 40.0),
        },
        "Bank": {
            "sustainable_roe_anchor_pct": (0.0, 30.0),
            "pb_roe_sensitivity_pct": (0.0, 20.0),
        },
    }.get(template, {})
    values: dict[str, float] = {}
    for key, (minimum, maximum) in definitions.items():
        if key not in raw:
            continue
        try:
            value = float(raw[key])
        except (TypeError, ValueError):
            continue
        if minimum <= value <= maximum:
            values[key] = round(value) if key == "forecast_years" else value
    if template == "Non-financial":
        discount = values.get("discount_rate_pct", _NON_FINANCIAL_DEFAULTS["discount_rate_pct"])
        terminal = values.get("terminal_growth_pct", _NON_FINANCIAL_DEFAULTS["terminal_growth_pct"])
        if terminal >= discount:
            values["terminal_growth_pct"] = min(discount - 0.5, _NON_FINANCIAL_DEFAULTS["terminal_growth_pct"])
    return values


def build_market_implied_expectations(
    identity: CompanyIdentity,
    metrics: list[FinancialMetric],
    current_price: float | None,
    valuation: ValuationResult,
    company_model: CompanyModelWorkspace,
    price_source: str = "",
    price_as_of: str | None = None,
    assumption_overrides: dict[str, float] | None = None,
) -> MarketImpliedExpectations:
    template = classify_template(identity)
    latest = _latest(metrics)
    financial_basis, financial_period, annual_flow_basis = _financial_basis(latest)
    currency = valuation.currency or company_model.currency or "USD"
    normalized_overrides = _validated_assumption_overrides(template, assumption_overrides or {})
    assumptions = _assumptions_for_template(template, normalized_overrides)
    if not current_price or current_price <= 0:
        return MarketImpliedExpectations(
            ticker=identity.ticker,
            template=template,
            status="Insufficient data",
            summary="Current listed-security price is required to infer market expectations.",
            currency=currency,
            assumptions=assumptions,
            data_gaps=["Current price is unavailable."],
            price_source=price_source,
            price_as_of=price_as_of,
            financial_basis=financial_basis,
            financial_period=financial_period,
        )

    shares = latest.get("Shares")
    adr_profile = adr_profile_for(identity.ticker)
    adr_ratio = adr_profile.ordinary_share_ratio if adr_profile else 1.0
    market_cap = current_price * shares.value / adr_ratio if shares and shares.value > 0 else None
    expectations: list[MarketImpliedExpectation] = []
    if template == "Bank":
        expectations.extend(_bank_expectations(latest, market_cap, current_price, normalized_overrides))
    elif template == "Non-financial":
        expectations.extend(_non_financial_expectations(
            latest,
            market_cap,
            normalized_overrides,
            annual_flow_basis=annual_flow_basis,
        ))
        expectations.append(_commodity_expectation(identity, latest, market_cap))
    else:
        expectations.extend(_financial_multiple_expectations(template, latest, market_cap, current_price))

    available = [row for row in expectations if row.implied_value is not None]
    status = "Available" if available else "Insufficient data"
    gaps = [gap for row in expectations for gap in row.missing_inputs]
    if not annual_flow_basis and any(row.implied_value is not None for row in expectations):
        gaps.append(
            "Reverse-model flow inputs use an explicit annualized screening run-rate from the latest interim filing, not verified trailing-twelve-month cash flow; treat growth and margin outputs as low-confidence screening estimates."
        )
        for row in expectations:
            if row.status == "Derived" and row.confidence in {"High", "Medium"}:
                row.confidence = "Low"
                row.interpretation += (
                    " The normalized flow basis is not verified as annual or trailing twelve months, so this is a screening estimate."
                )
    return MarketImpliedExpectations(
        ticker=identity.ticker,
        template=template,
        status=status,
        summary=(
            f"{len(available)} market-implied expectation(s) derived from price and normalized filing facts. "
            "These are reverse-engineered assumptions, not analyst consensus."
            if available else
            "Price is available, but normalized operating or balance-sheet inputs are insufficient for a defensible reverse model."
        ),
        current_price=current_price,
        currency=currency,
        assumptions=assumptions,
        expectations=expectations,
        data_gaps=_dedupe(gaps),
        price_source=price_source,
        price_as_of=price_as_of,
        financial_basis=financial_basis,
        financial_period=financial_period,
    )


def _non_financial_expectations(metrics, market_cap, assumptions, *, annual_flow_basis=False):
    discount = assumptions.get("discount_rate_pct", _NON_FINANCIAL_DEFAULTS["discount_rate_pct"]) / 100
    terminal_growth = assumptions.get("terminal_growth_pct", _NON_FINANCIAL_DEFAULTS["terminal_growth_pct"]) / 100
    years = int(round(assumptions.get("forecast_years", _NON_FINANCIAL_DEFAULTS["forecast_years"])))
    horizon_label = "five-year" if years == 5 else f"{years}-year"
    revenue_growth = assumptions.get("revenue_growth_pct", _NON_FINANCIAL_DEFAULTS["revenue_growth_pct"]) / 100
    revenue = metrics.get("Revenue")
    operating_income = metrics.get("Operating Income")
    operating_cash = metrics.get("Operating Cash Flow")
    capex = metrics.get("Capital Expenditure")
    debt = _sum_values(metrics, ("Current Debt", "Long-term Debt"))
    cash = metrics.get("Cash").value if metrics.get("Cash") else 0.0
    enterprise_value = market_cap + debt - cash if market_cap is not None else None
    revenue_value, revenue_factor = _annualized_flow_value(
        revenue, cumulative_interim=False, annual_flow_basis=annual_flow_basis,
    )
    operating_income_value, _ = _annualized_flow_value(
        operating_income, cumulative_interim=False, annual_flow_basis=annual_flow_basis,
    )
    operating_cash_value, operating_cash_factor = _annualized_flow_value(
        operating_cash, cumulative_interim=True, annual_flow_basis=annual_flow_basis,
    )
    capex_value, capex_factor = _annualized_flow_value(
        capex, cumulative_interim=True, annual_flow_basis=annual_flow_basis,
    )
    fcf = None
    if operating_cash and capex and _same_currency(operating_cash, capex):
        fcf = operating_cash_value - abs(capex_value)
    rows = []
    flow_basis_label = "reported annual/TTM" if annual_flow_basis else "annualized interim screening"
    if enterprise_value and revenue and revenue_value > 0:
        rows.append(MarketImpliedExpectation(
            metric="Current enterprise value / revenue",
            status="Derived",
            implied_value=enterprise_value / revenue_value,
            unit="x",
            formula=f"(Price x normalized shares + debt - cash) / {flow_basis_label} revenue",
            interpretation="A transparent valuation anchor for comparing the market's revenue multiple with history and peers.",
            confidence="Medium",
            required_inputs=["Price", "Shares", "Debt", "Cash", "Revenue"],
        ))
    if market_cap and fcf and fcf > 0:
        rows.append(MarketImpliedExpectation(
            metric="Reverse DCF base FCF",
            status="Observed" if annual_flow_basis else "Annualized screening estimate",
            implied_value=fcf,
            unit=_currency(operating_cash.unit) if operating_cash else "Unknown",
            formula=(
                "Operating cash flow - capital expenditure"
                if annual_flow_basis else
                f"Operating cash flow x {operating_cash_factor:g} - capital expenditure x {capex_factor:g}"
            ),
            interpretation=(
                "Reported annual cash flow used as the starting FCF base."
                if annual_flow_basis else
                "Interim cash flow is annualized only to create a transparent screening base; seasonality and working-capital timing can make it materially different from TTM FCF."
            ),
            confidence="High" if annual_flow_basis else "Low",
            required_inputs=["Operating Cash Flow", "Capital Expenditure"],
        ))
        rows.append(MarketImpliedExpectation(
            metric="Current free-cash-flow yield",
            status="Derived",
            implied_value=fcf / market_cap * 100,
            unit="%",
            formula=f"{flow_basis_label} FCF / market capitalization",
            interpretation="Shows the cash yield supported by normalized reported inputs before imposing a growth forecast.",
            confidence="Medium",
            required_inputs=["Price", "Shares", "Operating Cash Flow", "Capital Expenditure"],
        ))
    if enterprise_value and fcf and fcf > 0:
        implied_growth = _solve_fcf_growth(enterprise_value, fcf, discount, terminal_growth, years)
        rows.append(MarketImpliedExpectation(
            metric=f"Reverse DCF: implied {horizon_label} FCF growth",
            status="Derived",
            implied_value=implied_growth * 100 if implied_growth is not None else None,
            unit="% CAGR",
            formula=f"Solve EV = PV(FCF growing for {years} years) + PV(terminal value)",
            interpretation=(
                f"At a {discount * 100:.1f}% discount rate and {terminal_growth * 100:.1f}% terminal growth, "
                f"the price implies about {implied_growth * 100:.1f}% annual FCF growth over {years} years."
                if implied_growth is not None else "No stable reverse-DCF solution was found within the bounded range."
            ),
            confidence="Medium" if implied_growth is not None else "Low",
            required_inputs=["Price", "Shares", "Cash", "Debt", "Operating Cash Flow", "Capital Expenditure"],
            missing_inputs=[],
        ))
        if revenue and revenue_value > 0:
            current_fcf_margin = fcf / revenue_value
            implied_revenue_growth = implied_growth if current_fcf_margin > 0 else None
            rows.append(MarketImpliedExpectation(
                metric="Implied revenue growth at constant FCF margin",
                status="Derived" if implied_revenue_growth is not None else "Insufficient data",
                implied_value=implied_revenue_growth * 100 if implied_revenue_growth is not None else None,
                unit="% CAGR",
                formula="Implied FCF growth, holding current FCF/revenue margin constant",
                interpretation="Shows the revenue path embedded in price if cash conversion does not change.",
                confidence="Low",
                required_inputs=["Revenue", "Free cash flow"],
                missing_inputs=[] if implied_revenue_growth is not None else ["Positive, comparable FCF margin"],
            ))
            implied_margin = _solve_terminal_margin(
                enterprise_value, revenue_value, revenue_growth, discount, terminal_growth, years,
            )
            rows.append(MarketImpliedExpectation(
                metric=f"Implied terminal FCF margin at {revenue_growth * 100:.1f}% revenue growth",
                status="Derived" if implied_margin is not None else "Insufficient data",
                implied_value=implied_margin * 100 if implied_margin is not None else None,
                unit="%",
                formula=(
                    f"Solve EV from {years}-year {revenue_growth * 100:.1f}% revenue growth "
                    "and a constant terminal FCF margin"
                ),
                interpretation="Separates the margin assumption embedded in price from the growth assumption.",
                confidence="Low",
                required_inputs=["Revenue", "Enterprise value"],
                missing_inputs=[] if implied_margin is not None else ["Stable positive enterprise value and revenue"],
            ))
        terminal_fcf = fcf * (1 + (implied_growth or 0.0)) ** years
        terminal_multiple = enterprise_value / terminal_fcf if terminal_fcf > 0 else None
        rows.append(MarketImpliedExpectation(
            metric=f"Current EV / year-{years} implied FCF",
            status="Derived" if terminal_multiple else "Insufficient data",
            implied_value=terminal_multiple,
            unit="x",
            formula=f"Current enterprise value / reverse-DCF year-{years} FCF",
            interpretation="A transparent terminal-value reasonableness check; it is not a provider target multiple.",
            confidence="Low",
            required_inputs=["Enterprise value", f"Year-{years} implied FCF"],
            missing_inputs=[] if terminal_multiple else [f"Year-{years} implied FCF"],
        ))
    else:
        missing = []
        if market_cap is None:
            missing.extend(["Shares", "Market capitalization"])
        if not fcf or fcf <= 0:
            missing.extend(["Positive Operating Cash Flow less Capital Expenditure"])
        rows.append(_unavailable("Reverse DCF", ["Price", "Shares", "Cash", "Debt", "Operating Cash Flow", "Capital Expenditure"], missing))
    if revenue and operating_income and _same_currency(revenue, operating_income):
        margin = operating_income_value / revenue_value * 100 if revenue_value else None
        rows.append(MarketImpliedExpectation(
            metric="Current operating margin reference",
            status="Observed",
            implied_value=margin,
            unit="%",
            formula="Operating income / revenue",
            interpretation="Reference anchor for comparing the reverse-implied margin path with reported economics.",
            confidence="High",
            required_inputs=["Revenue", "Operating Income"],
        ))
    return rows


def _bank_expectations(metrics, market_cap, current_price, assumptions):
    equity = metrics.get("Stockholders' Equity")
    net_income = metrics.get("Net Income")
    provision = metrics.get("Credit Loss Provision")
    rows = []
    if market_cap and equity and equity.value > 0:
        pb = market_cap / equity.value
        roe_anchor = assumptions.get("sustainable_roe_anchor_pct", _BANK_DEFAULTS["sustainable_roe_anchor_pct"]) / 100
        pb_sensitivity = assumptions.get("pb_roe_sensitivity_pct", _BANK_DEFAULTS["pb_roe_sensitivity_pct"]) / 100
        implied_roe = roe_anchor + (pb - 1.0) * pb_sensitivity
        rows.append(MarketImpliedExpectation(
            metric="Implied sustainable ROE",
            status="Derived",
            implied_value=implied_roe * 100,
            unit="%",
            formula=(
                f"{roe_anchor * 100:.1f}% anchor + (P/B - 1) x "
                f"{pb_sensitivity * 100:.1f} percentage points"
            ),
            interpretation="A transparent P/B-to-ROE heuristic for scenario framing, not a calibrated bank valuation model.",
            confidence="Low",
            required_inputs=["Price", "Shares", "Stockholders' Equity"],
        ))
        if net_income and provision and _same_currency(net_income, provision):
            target_income = equity.value * implied_roe
            implied_provision = max(0.0, net_income.value + provision.value - target_income)
            rows.append(MarketImpliedExpectation(
                metric="Implied credit-loss provision",
                status="Derived",
                implied_value=implied_provision,
                unit=provision.unit,
                formula="Reported net income + reported provision - income implied by sustainable ROE",
                interpretation="Approximates the credit-cost burden consistent with the price-implied ROE, holding other earnings drivers constant.",
                confidence="Low",
                required_inputs=["Equity", "Net Income", "Credit Loss Provision"],
            ))
        else:
            rows.append(_unavailable(
                "Implied credit-loss provision",
                ["Stockholders' Equity", "Net Income", "Credit Loss Provision"],
                ["Net Income or Credit Loss Provision"],
            ))
    else:
        rows.append(_unavailable("Implied sustainable ROE", ["Price", "Shares", "Stockholders' Equity"], ["Shares or Stockholders' Equity"]))
    return rows


def _financial_multiple_expectations(template, metrics, market_cap, current_price):
    equity = metrics.get("Stockholders' Equity")
    if market_cap and equity and equity.value > 0:
        return [MarketImpliedExpectation(
            metric="Implied price-to-book reference",
            status="Derived",
            implied_value=market_cap / equity.value,
            unit="x",
            formula="Market capitalization / stockholders' equity",
            interpretation=f"Market-implied book-value multiple for the {template} valuation workspace.",
            confidence="Medium",
            required_inputs=["Price", "Shares", "Stockholders' Equity"],
        )]
    return [_unavailable("Implied price-to-book reference", ["Price", "Shares", "Stockholders' Equity"], ["Shares or Stockholders' Equity"])]


def _commodity_expectation(identity, metrics, market_cap):
    description = (identity.sic_description or "").lower()
    is_producer = any(word in description for word in ("oil", "gas", "mining", "metal", "coal", "petroleum"))
    if not is_producer:
        return MarketImpliedExpectation(
            metric="Implied commodity price",
            status="Not applicable",
            implied_value=None,
            unit="Unknown",
            formula="Producer-specific operating sensitivity",
            interpretation="Not applied because the issuer is not classified as a commodity producer.",
            confidence="High",
        )
    production = next((row for name, row in metrics.items() if "production" in name.lower()), None)
    revenue = metrics.get("Revenue")
    operating_income = metrics.get("Operating Income")
    if production and production.value > 0 and revenue and operating_income:
        implied_price = (revenue.value - operating_income.value) / production.value
        return MarketImpliedExpectation(
            metric="Implied commodity break-even proxy",
            status="Derived",
            implied_value=implied_price,
            unit=f"{revenue.unit}/{production.unit}",
            formula="(Revenue - operating income) / production volume",
            interpretation="A coarse operating break-even proxy; product mix and non-commodity revenue must be checked.",
            confidence="Low",
            required_inputs=["Revenue", "Operating Income", "Production volume"],
        )
    return _unavailable(
        "Implied commodity price",
        ["Revenue", "Operating Income", "Production volume", "Commodity mix"],
        ["Normalized production volume or commodity mix"],
    )


def _solve_fcf_growth(ev, current_fcf, discount, terminal_growth, years):
    low, high = -0.50, 0.80
    for _ in range(100):
        mid = (low + high) / 2
        value = _dcf_value(current_fcf, mid, discount, terminal_growth, years)
        if value < ev:
            low = mid
        else:
            high = mid
    result = (low + high) / 2
    return result if abs(_dcf_value(current_fcf, result, discount, terminal_growth, years) / ev - 1) < 0.01 else None


def _dcf_value(fcf, growth, discount, terminal_growth, years):
    total = 0.0
    projected = fcf
    for year in range(1, years + 1):
        projected *= 1 + growth
        total += projected / ((1 + discount) ** year)
    terminal = projected * (1 + terminal_growth) / max(0.001, discount - terminal_growth)
    return total + terminal / ((1 + discount) ** years)


def _solve_terminal_margin(ev, revenue, revenue_growth, discount, terminal_growth, years):
    value_per_margin = _dcf_value(revenue, revenue_growth, discount, terminal_growth, years)
    return ev / value_per_margin if value_per_margin > 0 else None


def _unavailable(metric, required, missing):
    return MarketImpliedExpectation(
        metric=metric,
        status="Insufficient data",
        implied_value=None,
        unit="Unknown",
        formula="Not calculated",
        interpretation="The app did not infer a value because required normalized inputs are missing.",
        confidence="Unknown",
        required_inputs=required,
        missing_inputs=missing,
    )


def _latest(metrics):
    result = {}
    for metric in sorted(metrics, key=lambda row: (row.period_end or "", row.filed or ""), reverse=True):
        result.setdefault(metric.name, metric)
    return result


def _financial_basis(metrics):
    anchor = (
        metrics.get("Revenue")
        or metrics.get("Operating Cash Flow")
        or metrics.get("Net Income")
        or next(iter(metrics.values()), None)
    )
    if not anchor:
        return "No normalized financial basis", None, False
    fiscal_period = (anchor.fiscal_period or "").upper()
    form = (anchor.form or "").upper()
    annual = fiscal_period == "FY" or (form in {"10-K", "20-F", "40-F"} and not fiscal_period.startswith("Q"))
    if annual:
        label = "Annual normalized filing facts"
    else:
        label = f"Annualized {fiscal_period or form or 'interim'} screening run-rate; not verified as TTM"
    return label, anchor.period_end or None, annual


def _annualized_flow_value(metric, *, cumulative_interim, annual_flow_basis):
    if not metric:
        return 0.0, 1.0
    if annual_flow_basis:
        return metric.value, 1.0
    fiscal_period = (metric.fiscal_period or "").upper()
    quarter = None
    if fiscal_period.startswith("Q"):
        try:
            quarter = int(fiscal_period[1:2])
        except (TypeError, ValueError):
            quarter = None
    if not quarter or quarter not in {1, 2, 3, 4}:
        return metric.value, 1.0
    factor = (4.0 / quarter) if cumulative_interim else 4.0
    return metric.value * factor, factor


def _sum_values(metrics, names):
    rows = [metrics.get(name) for name in names if metrics.get(name)]
    if not rows:
        return 0.0
    currency = _currency(rows[0].unit)
    return sum(row.value for row in rows if _currency(row.unit) == currency)


def _same_currency(left, right):
    return _currency(left.unit) == _currency(right.unit)


def _currency(unit):
    value = (unit or "").upper()
    for code in ("USD", "CNY", "HKD", "EUR", "GBP", "JPY", "KRW", "TWD", "INR", "CAD", "AUD"):
        if code in value:
            return code
    return value


def _dedupe(values):
    return list(dict.fromkeys(value for value in values if value))
