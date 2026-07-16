from __future__ import annotations

from datetime import date

from .adr_profiles import adr_profile_for
from .models import (
    CompanyIdentity,
    ConsensusPackage,
    EstimatePoint,
    FinancialMetric,
    ProviderObservation,
    SensitivityPoint,
    ValuationBridgeStep,
    ValuationCase,
    ValuationResult,
)


TEMPLATE_OVERRIDES = {
    "JPM": "Bank", "BAC": "Bank", "WFC": "Bank", "C": "Bank", "GS": "Bank", "MS": "Bank",
    "AIG": "Insurer", "MET": "Insurer", "PRU": "Insurer",
    "SPG": "REIT", "PLD": "REIT", "O": "REIT", "AMT": "REIT",
}

def build_valuation(
    identity: CompanyIdentity,
    metrics: list[FinancialMetric],
    consensus: ConsensusPackage,
    current_price: float | None,
) -> ValuationResult:
    template = classify_template(identity)
    currency = consensus.target.currency if consensus.target else "USD"
    if not current_price or current_price <= 0:
        return _insufficient(template, "Current share price is unavailable.", currency)

    by_name = {metric.name: metric for metric in metrics}
    estimates = _future_estimates(consensus.estimates)
    target = _consensus_target_value(consensus.target)
    adr_profile = adr_profile_for(identity.ticker)
    adr_ratio = adr_profile.ordinary_share_ratio if adr_profile else 1.0
    normalization = [f"US-listed security currency: {currency}."]
    if adr_ratio != 1:
        home = f" on {adr_profile.home_exchange}" if adr_profile and adr_profile.home_exchange != "Unknown" else ""
        normalization.append(f"ADR ratio normalized at {adr_ratio:g} ordinary shares per ADR{home}.")

    if template == "Non-financial":
        result = _non_financial_valuation(
            estimates, by_name, current_price, currency, adr_ratio,
            consensus.observations,
        )
    elif template == "Bank":
        result = _financial_valuation(
            "Bank", estimates, by_name, current_price, currency, adr_ratio,
            consensus.observations,
        )
    elif template == "Insurer":
        result = _financial_valuation(
            "Insurer", estimates, by_name, current_price, currency, adr_ratio,
            consensus.observations,
        )
    else:
        result = _reit_valuation(
            estimates, by_name, current_price, currency, adr_ratio,
            consensus.observations,
        )

    result.currency = currency
    result.normalization_notes = normalization + result.normalization_notes
    result.consensus_target = target
    if result.probability_weighted_value is not None and target not in (None, 0):
        result.disagreement_pct = (result.probability_weighted_value / target - 1) * 100
    return result


def classify_template(identity: CompanyIdentity) -> str:
    ticker = identity.ticker.upper()
    if ticker in TEMPLATE_OVERRIDES:
        return TEMPLATE_OVERRIDES[ticker]
    try:
        sic = int(identity.sic or 0)
    except ValueError:
        sic = 0
    description = (identity.sic_description or "").lower()
    if sic == 6798 or "real estate investment trust" in description:
        return "REIT"
    if 6000 <= sic <= 6199 or "bank" in description:
        return "Bank"
    if 6300 <= sic <= 6499 or "insurance" in description:
        return "Insurer"
    return "Non-financial"


def _non_financial_valuation(
    estimates: list[EstimatePoint],
    metrics: dict[str, FinancialMetric],
    current_price: float,
    currency: str,
    adr_ratio: float,
    observations: list[ProviderObservation],
) -> ValuationResult:
    values: list[list[float]] = [[], [], []]
    methods: list[str] = []
    assumptions: list[str] = []
    gaps: list[str] = []
    bridge: list[ValuationBridgeStep] = []
    references: list[str] = []
    eps = _estimate(estimates, "EPS")
    if _usable_per_share_estimate(eps, currency):
        observed_pe = _observation_value(observations, "forward_pe")
        base_pe = observed_pe if observed_pe and observed_pe > 0 else current_price / eps.average
        eps_factors = (0.9, 1.0, 1.1)
        multiple_factors = (0.8, 1.0, 1.2)
        for index, (eps_factor, multiple_factor) in enumerate(zip(eps_factors, multiple_factors)):
            values[index].append(eps.average * eps_factor * base_pe * multiple_factor)
            bridge.append(ValuationBridgeStep(
                ("Bear", "Base", "Bull")[index], "Forward EPS",
                eps.average * eps_factor, currency,
                f"EPS x {base_pe * multiple_factor:.1f}x forward P/E",
                eps.source,
            ))
        methods.append("forward P/E")
        assumptions.append(
            f"Forward EPS: -10% / base / +10%; P/E anchored at {base_pe:.1f}x "
            "and flexed -20% / base / +20%."
        )
        references.append(
            "Provider forward P/E" if observed_pe else "Current price divided by forward EPS"
        )
    else:
        gaps.append("Forward EPS in the listed security currency is unavailable.")

    shares = _usable_shares(metrics.get("Shares"))
    net_debt = _net_debt(metrics, currency)
    market_cap = _normalized_market_cap(
        observations, current_price, shares, adr_ratio, currency,
    )
    for metric_name, method in (("Revenue", "EV/revenue"), ("EBITDA", "EV/EBITDA")):
        estimate = _estimate(estimates, metric_name)
        if estimate and estimate.average and estimate.average > 0 and _currency(estimate.currency) == _currency(currency) and shares and net_debt is not None and market_cap:
            base_multiple = (market_cap + net_debt) / estimate.average
            for index, factor in enumerate((0.8, 1.0, 1.2)):
                operating_value = estimate.average * (0.9, 1.0, 1.1)[index]
                multiple = base_multiple * factor
                equity_value = operating_value * multiple - net_debt
                if equity_value > 0:
                    per_security_value = equity_value / shares * adr_ratio
                    values[index].append(per_security_value)
                    bridge.append(ValuationBridgeStep(
                        ("Bear", "Base", "Bull")[index],
                        metric_name,
                        operating_value,
                        estimate.currency or currency,
                        (
                            f"{format_label(method)}: {operating_value:,.0f} x "
                            f"{multiple:.1f}x less net debt = {per_security_value:.2f} per listed security"
                        ),
                        estimate.source,
                    ))
            methods.append(method)
            assumptions.append(
                f"{method} anchored at current implied {base_multiple:.1f}x; "
                "operating estimate -10% / base / +10%; multiple -20% / base / +20%."
            )
            references.append(f"Current market-implied {method}")
        else:
            gaps.append(f"{metric_name}, net debt, shares, or currency normalization is insufficient for {method}.")

    operating_cash = metrics.get("Operating Cash Flow")
    capex = metrics.get("Capital Expenditure")
    if _same_metric_currency((operating_cash, capex), currency) and shares:
        free_cash_flow = (operating_cash.value if operating_cash else 0) - abs(capex.value if capex else 0)
        if free_cash_flow > 0:
            base_yield = free_cash_flow / market_cap if market_cap else None
            if base_yield and base_yield > 0:
                for index, factor in enumerate((1.2, 1.0, 0.8)):
                    scenario_fcf = free_cash_flow * (0.9, 1.0, 1.1)[index]
                    required_yield = base_yield * factor
                    per_security_value = scenario_fcf / required_yield / shares * adr_ratio
                    values[index].append(per_security_value)
                    bridge.append(ValuationBridgeStep(
                        ("Bear", "Base", "Bull")[index],
                        "Free cash flow",
                        scenario_fcf,
                        currency,
                        (
                            f"FCF yield: {scenario_fcf:,.0f} / {required_yield * 100:.1f}% "
                            f"= {per_security_value:.2f} per listed security"
                        ),
                        "SEC cash-flow facts",
                    ))
                methods.append("FCF yield")
                assumptions.append(
                    f"FCF yield anchored at current implied {base_yield * 100:.1f}%; "
                    "FCF -10% / base / +10%; required yield +20% / base / -20%."
                )
                references.append("Current market-implied FCF yield")
    else:
        gaps.append("Operating cash flow, capex, shares, or currency normalization is insufficient for FCF yield.")

    if not any(values):
        return _insufficient("Non-financial", "No model-specific valuation method has sufficient normalized data.", currency, gaps)
    return _result_from_values(
        "Non-financial", values, current_price, methods, assumptions, currency, gaps,
        bridge, references,
    )


def _financial_valuation(
    template: str,
    estimates: list[EstimatePoint],
    metrics: dict[str, FinancialMetric],
    current_price: float,
    currency: str,
    adr_ratio: float,
    observations: list[ProviderObservation],
) -> ValuationResult:
    values: list[list[float]] = [[], [], []]
    methods: list[str] = []
    assumptions: list[str] = []
    gaps: list[str] = []
    bridge: list[ValuationBridgeStep] = []
    references: list[str] = []
    shares = _usable_shares(metrics.get("Shares"))
    equity = metrics.get("Stockholders' Equity")
    goodwill = metrics.get("Goodwill")
    intangibles = metrics.get("Intangible Assets")
    net_income = metrics.get("Net Income")
    eps = _estimate(estimates, "EPS")

    if _usable_per_share_estimate(eps, currency):
        provision = metrics.get("Credit Loss Provision") if template == "Bank" else None
        operating_adjustments = (-0.15, 0.0, 0.10) if provision else (-0.10, 0.0, 0.10)
        observed_pe = _observation_value(observations, "forward_pe")
        base_pe = observed_pe if observed_pe and observed_pe > 0 else current_price / eps.average
        for index, (multiple_factor, adjustment) in enumerate(
            zip((0.8, 1.0, 1.2), operating_adjustments)
        ):
            scenario_eps = eps.average * (1 + adjustment)
            values[index].append(scenario_eps * base_pe * multiple_factor)
            bridge.append(ValuationBridgeStep(
                ("Bear", "Base", "Bull")[index], "Forward EPS", scenario_eps,
                currency, f"EPS x {base_pe * multiple_factor:.1f}x P/E", eps.source,
            ))
        methods.append("forward P/E")
        assumptions.append(
            f"Forward P/E anchored at {base_pe:.1f}x and flexed -20% / base / +20%."
        )
        references.append(
            "Provider forward P/E" if observed_pe else "Current price divided by forward EPS"
        )
        if provision:
            assumptions.append("Credit-cost EPS adjustment: -15% / 0% / +10%")
    else:
        gaps.append("Forward EPS in the listed security currency is unavailable.")

    if equity and shares and equity.value > 0 and _currency(equity.unit) == _currency(currency):
        book_per_security = equity.value / shares * adr_ratio
        base_pb = current_price / book_per_security
        for index, factor in enumerate((0.8, 1.0, 1.2)):
            multiple = base_pb * factor
            per_security_value = book_per_security * multiple
            values[index].append(per_security_value)
            bridge.append(ValuationBridgeStep(
                ("Bear", "Base", "Bull")[index],
                "Book value per listed security",
                book_per_security,
                currency,
                f"P/B: {book_per_security:.2f} x {multiple:.2f}x = {per_security_value:.2f}",
                equity.source_kind or equity.form or "SEC company facts",
            ))
        methods.append("price-to-book")
        assumptions.append(
            f"P/B anchored at current {base_pb:.2f}x and flexed -20% / base / +20%."
        )
        references.append("Current market-implied P/B")
        if net_income and _currency(net_income.unit) == _currency(currency):
            roe = net_income.value / equity.value
            roe_multiples = tuple(base_pb * factor for factor in (0.75, 1.0, 1.25))
            for index, multiple in enumerate(roe_multiples):
                per_security_value = book_per_security * multiple
                values[index].append(per_security_value)
                bridge.append(ValuationBridgeStep(
                    ("Bear", "Base", "Bull")[index],
                    "ROE-supported book value",
                    roe * 100,
                    "%",
                    f"ROE {roe * 100:.1f}% supports P/B {multiple:.2f}x = {per_security_value:.2f}",
                    net_income.source_kind or net_income.form or "SEC company facts",
                ))
            methods.append("ROE-supported P/B")
            assumptions.append(
                f"Reported ROE: {roe * 100:.1f}%; ROE durability adjusts the current P/B "
                "by -25% / base / +25%."
            )
        tangible_equity = equity.value - (goodwill.value if goodwill and goodwill.unit == equity.unit else 0) - (intangibles.value if intangibles and intangibles.unit == equity.unit else 0)
        if tangible_equity > 0 and (goodwill or intangibles):
            tangible_per_security = tangible_equity / shares * adr_ratio
            base_ptbv = current_price / tangible_per_security
            for index, factor in enumerate((0.8, 1.0, 1.2)):
                multiple = base_ptbv * factor
                per_security_value = tangible_per_security * multiple
                values[index].append(per_security_value)
                bridge.append(ValuationBridgeStep(
                    ("Bear", "Base", "Bull")[index],
                    "Tangible book per listed security",
                    tangible_per_security,
                    currency,
                    f"P/TBV: {tangible_per_security:.2f} x {multiple:.2f}x = {per_security_value:.2f}",
                    equity.source_kind or equity.form or "SEC company facts",
                ))
            methods.append("price-to-tangible-book")
            rotce = net_income.value / tangible_equity * 100 if net_income and net_income.unit == equity.unit else None
            assumptions.append(f"Tangible book normalized; ROTCE: {rotce:.1f}%" if rotce is not None else "Tangible book normalized; ROTCE unavailable")
    else:
        gaps.append("Book value, shares, or currency normalization is insufficient for book-value methods.")

    if template == "Insurer":
        insurance_revenue = metrics.get("Insurance Revenue")
        assumptions.append(
            f"Insurance revenue monitored at {insurance_revenue.value:,.0f} {insurance_revenue.unit}."
            if insurance_revenue else "Insurance operating metrics unavailable; confidence reduced."
        )
    if not any(values):
        return _insufficient(template, "No model-specific valuation method has sufficient normalized data.", currency, gaps)
    return _result_from_values(
        template, values, current_price, methods, assumptions, currency, gaps,
        bridge, references,
    )


def _reit_valuation(
    estimates: list[EstimatePoint],
    metrics: dict[str, FinancialMetric],
    current_price: float,
    currency: str,
    adr_ratio: float,
    observations: list[ProviderObservation],
) -> ValuationResult:
    values: list[list[float]] = [[], [], []]
    methods: list[str] = []
    assumptions: list[str] = []
    gaps: list[str] = []
    bridge: list[ValuationBridgeStep] = []
    references: list[str] = []
    ffo = _estimate(estimates, "FFO") or _estimate(estimates, "AFFO")
    if _usable_per_share_estimate(ffo, currency):
        base_multiple = current_price / ffo.average
        for index, factor in enumerate((0.8, 1.0, 1.2)):
            scenario_ffo = ffo.average * (0.9, 1.0, 1.1)[index]
            values[index].append(scenario_ffo * base_multiple * factor)
            bridge.append(ValuationBridgeStep(
                ("Bear", "Base", "Bull")[index], "Forward FFO/AFFO",
                scenario_ffo, currency,
                f"FFO/AFFO x {base_multiple * factor:.1f}x", ffo.source,
            ))
        methods.append("price-to-FFO/AFFO")
        assumptions.append(
            f"P/FFO anchored at current {base_multiple:.1f}x; FFO -10% / base / +10%; "
            "multiple -20% / base / +20%."
        )
        references.append("Current market-implied P/FFO")
    else:
        gaps.append("Forward FFO or AFFO per share is required; EPS is not substituted.")

    nav = _estimate(estimates, "NAV Per Share")
    if _usable_per_share_estimate(nav, currency):
        base_nav_multiple = current_price / nav.average
        for index, factor in enumerate((0.8, 1.0, 1.2)):
            multiple = base_nav_multiple * factor
            per_security_value = nav.average * multiple
            values[index].append(per_security_value)
            bridge.append(ValuationBridgeStep(
                ("Bear", "Base", "Bull")[index],
                "NAV per share",
                nav.average,
                currency,
                f"NAV: {nav.average:.2f} x {multiple:.2f}x = {per_security_value:.2f}",
                nav.source,
            ))
        methods.append("NAV premium/discount")
        assumptions.append(
            f"NAV multiple anchored at current {base_nav_multiple:.2f}x and flexed "
            "-20% / base / +20%."
        )
        references.append("Current market-implied NAV premium/discount")
    else:
        gaps.append("Explicit NAV per share is unavailable; book value is not relabeled as NAV.")

    debt = _sum_metrics(metrics, ("Long-term Debt", "Current Debt"), currency)
    assets = metrics.get("Total Assets")
    if debt is not None and assets and assets.value > 0 and _currency(assets.unit) == _currency(currency):
        assumptions.append(f"Debt/assets leverage: {debt / assets.value * 100:.1f}%")
    else:
        gaps.append("REIT leverage could not be normalized.")
    dividend = _estimate(estimates, "Dividend")
    if ffo and dividend and ffo.average and dividend.average is not None:
        assumptions.append(f"Dividend/FFO coverage ratio: {dividend.average / ffo.average * 100:.1f}%")
    else:
        gaps.append("Forward dividend coverage is unavailable.")
    if not any(values):
        return _insufficient("REIT", "Forward FFO/AFFO or explicit NAV per share is required.", currency, gaps)
    return _result_from_values(
        "REIT", values, current_price, methods, assumptions, currency, gaps,
        bridge, references,
    )


def _result_from_values(
    template: str,
    values: list[list[float]],
    current_price: float,
    methods: list[str],
    assumptions: list[str],
    currency: str,
    gaps: list[str],
    bridge: list[ValuationBridgeStep] | None = None,
    reference_sources: list[str] | None = None,
) -> ValuationResult:
    names = ("Bear", "Base", "Bull")
    probabilities = (0.25, 0.5, 0.25)
    cases: list[ValuationCase] = []
    for name, probability, candidates in zip(names, probabilities, values):
        fair_value = sum(candidates) / len(candidates) if candidates else None
        case_assumptions = list(assumptions) + [f"Operational case: {name.lower()}."]
        cases.append(ValuationCase(name, probability, fair_value, " + ".join(methods), case_assumptions))
    valid = [case for case in cases if case.fair_value is not None]
    probability = sum(case.probability for case in valid)
    weighted = sum(case.probability * case.fair_value for case in valid) / probability if probability else None
    base_value = cases[1].fair_value if len(cases) > 1 else weighted
    sensitivity = _sensitivity_grid(base_value)
    for case in cases:
        if case.fair_value is not None:
            (bridge := bridge or []).append(ValuationBridgeStep(
                case.name, "Fair value", case.fair_value, currency,
                "Average of available model-specific method outputs",
                "Internal scenario model",
            ))
    return ValuationResult(
        template=template,
        status="Available",
        currency=currency,
        cases=cases,
        probability_weighted_value=weighted,
        expected_return_pct=(weighted / current_price - 1) * 100 if weighted is not None else None,
        confidence="High" if len(methods) >= 3 else "Medium" if len(methods) >= 2 else "Low",
        methodology=(
            "Triangulates company-specific market-implied anchors with explicit operating "
            "and multiple sensitivities; analyst targets are comparison-only."
        ),
        missing_data=gaps,
        bridge=bridge or [],
        sensitivity=sensitivity,
        reference_sources=list(dict.fromkeys(reference_sources or [])),
    )


def _insufficient(
    template: str,
    message: str,
    currency: str = "USD",
    gaps: list[str] | None = None,
) -> ValuationResult:
    return ValuationResult(
        template=template,
        status="Insufficient data",
        currency=currency,
        methodology="No generic fallback was applied.",
        missing_data=[message] + list(gaps or []),
    )


def _future_estimates(estimates: list[EstimatePoint]) -> list[EstimatePoint]:
    today = date.today().isoformat()
    return sorted(
        [item for item in estimates if item.period_end[:10] >= today],
        key=lambda item: (item.period_end, item.metric),
    )


def _estimate(estimates: list[EstimatePoint], metric: str) -> EstimatePoint | None:
    return next((item for item in estimates if item.metric == metric and item.period_type == "annual"), None)


def _append_per_share(values: list[list[float]], amount: float | None, multiples) -> None:
    if amount is None:
        return
    for index, multiple in enumerate(multiples):
        values[index].append(amount * multiple)


def _usable_per_share_estimate(estimate: EstimatePoint | None, currency: str) -> bool:
    return bool(
        estimate and estimate.average is not None and estimate.average > 0
        and _currency(estimate.currency) == _currency(currency)
    )


def _usable_shares(metric: FinancialMetric | None) -> float | None:
    if not metric or metric.value <= 0:
        return None
    return metric.value if metric.unit.lower() in {"shares", "share"} else None


def _net_debt(metrics: dict[str, FinancialMetric], currency: str) -> float | None:
    cash = metrics.get("Cash")
    debt_values = [metrics.get("Long-term Debt"), metrics.get("Current Debt")]
    debts = [item for item in debt_values if item]
    if not cash or not debts or _currency(cash.unit) != _currency(currency):
        return None
    if any(_currency(item.unit) != _currency(currency) for item in debts):
        return None
    return sum(item.value for item in debts) - cash.value


def _sum_metrics(
    metrics: dict[str, FinancialMetric],
    names: tuple[str, ...],
    currency: str,
) -> float | None:
    available = [metrics.get(name) for name in names if metrics.get(name)]
    if not available or any(_currency(item.unit) != _currency(currency) for item in available):
        return None
    return sum(item.value for item in available)


def _same_metric_currency(metrics, currency: str) -> bool:
    return all(metric is not None and _currency(metric.unit) == _currency(currency) for metric in metrics)


def _currency(value: str) -> str:
    return value.upper().strip()


def _consensus_target_value(target) -> float | None:
    if not target:
        return None
    if target.target_kind == "aggregate":
        return target.target_aggregate
    if target.target_kind == "median":
        return target.target_median
    return target.target_mean


def _observation_value(
    observations: list[ProviderObservation],
    field: str,
) -> float | None:
    official = [
        item for item in observations
        if item.field == field and item.official and item.value_numeric is not None
    ]
    candidates = official or [
        item for item in observations
        if item.field == field and item.value_numeric is not None
    ]
    return candidates[0].value_numeric if candidates else None


def _normalized_market_cap(
    observations: list[ProviderObservation],
    current_price: float,
    shares: float | None,
    adr_ratio: float,
    currency: str,
) -> float | None:
    observation = next(
        (
            item for item in observations
            if item.field == "market_cap" and item.value_numeric is not None
            and _currency(item.currency or currency) == _currency(currency)
        ),
        None,
    )
    if observation:
        return observation.value_numeric
    return current_price * shares / adr_ratio if shares else None


def _sensitivity_grid(base_value: float | None) -> list[SensitivityPoint]:
    if base_value is None:
        return []
    points: list[SensitivityPoint] = []
    for operating_label, operating_factor in (("-10% operating", 0.9), ("Base operating", 1.0), ("+10% operating", 1.1)):
        for multiple_label, multiple_factor in (("-20% multiple", 0.8), ("Base multiple", 1.0), ("+20% multiple", 1.2)):
            points.append(SensitivityPoint(
                operating_label, multiple_label,
                base_value * operating_factor * multiple_factor,
            ))
    return points


def format_label(value: str) -> str:
    return value.replace("_", " ").replace("/", " / ")
