from __future__ import annotations

from .analysis import format_number
from .models import CompanyIdentity, CreditBridgeCheck, CreditLens, CreditMetric, FinancialMetric


def build_credit_lens(identity: CompanyIdentity, metrics: list[FinancialMetric]) -> CreditLens:
    by_name = {metric.name: metric for metric in metrics}
    cash = _first_metric(by_name, "Cash", "Cash and Cash Equivalents")
    debt_metric = _first_metric(by_name, "Long-term Debt", "Current Debt", "Short-term Debt")
    debt = _sum_metrics(by_name, "Long-term Debt", "Current Debt", "Short-term Debt")
    revenue = _first_metric(by_name, "Revenue")
    operating_income = _first_metric(by_name, "Operating Income")
    interest = _first_metric(by_name, "Interest Expense")
    operating_cash_flow = _first_metric(by_name, "Operating Cash Flow", "Net Cash Provided by Operating Activities")
    capex = _first_metric(by_name, "Capital Expenditure", "Capital Expenditures")
    current_assets = _first_metric(by_name, "Current Assets")
    current_liabilities = _first_metric(by_name, "Current Liabilities")

    metrics_out: list[CreditMetric] = []
    positives: list[str] = []
    risks: list[str] = []
    gaps: list[str] = []

    if cash:
        metrics_out.append(_metric("Cash", cash.value, cash.unit, "Available", "Liquidity buffer available from structured facts.", cash))
    else:
        gaps.append("Cash balance is unavailable.")

    if debt is not None:
        debt_unit = debt_metric.unit if debt_metric else (cash.unit if cash else "currency")
        metrics_out.append(_metric("Total debt", debt, debt_unit, "Available", "Debt burden from current and long-term debt where available.", debt_metric))
    else:
        gaps.append("Current debt and long-term debt are unavailable.")

    net_debt = debt - cash.value if debt is not None and cash else None
    if net_debt is not None:
        status = "Net cash" if net_debt < 0 else "Net debt"
        interpretation = (
            "Net cash position improves financial flexibility."
            if net_debt < 0 else
            "Net debt increases refinancing and downside sensitivity."
        )
        metrics_out.append(_metric("Net debt", net_debt, cash.unit, status, interpretation, cash))
        if net_debt < 0:
            positives.append(f"Net cash position of {format_number(abs(net_debt))} {cash.unit}.")
        else:
            risks.append(f"Net debt position of {format_number(net_debt)} {cash.unit}.")

    debt_to_revenue = debt / revenue.value if debt is not None and revenue and revenue.value else None
    if debt_to_revenue is not None:
        metrics_out.append(_metric("Debt / revenue", debt_to_revenue, "x", _ratio_status(debt_to_revenue, 0.5, 1.5), "Debt load relative to revenue scale.", revenue))
        if debt_to_revenue >= 1.5:
            risks.append(f"Debt/revenue is elevated at {debt_to_revenue:.1f}x.")
        elif debt_to_revenue <= 0.5:
            positives.append(f"Debt/revenue is modest at {debt_to_revenue:.1f}x.")
    else:
        gaps.append("Debt/revenue could not be computed.")

    interest_coverage = operating_income.value / interest.value if operating_income and interest and interest.value else None
    if interest_coverage is not None:
        metrics_out.append(_metric("Operating income / interest expense", interest_coverage, "x", _coverage_status(interest_coverage), "Coverage of financing cost by operating income.", operating_income))
        if interest_coverage < 2:
            risks.append(f"Interest coverage is thin at {interest_coverage:.1f}x.")
        elif interest_coverage >= 5:
            positives.append(f"Interest coverage is comfortable at {interest_coverage:.1f}x.")
    else:
        gaps.append("Interest coverage could not be computed.")

    fcf = _free_cash_flow(operating_cash_flow, capex)
    if fcf is not None:
        metrics_out.append(_metric("Free cash flow", fcf, operating_cash_flow.unit, "Available", "Operating cash flow less capex, normalized for capex sign.", operating_cash_flow))
        if fcf > 0:
            positives.append(f"Free cash flow was positive at {format_number(fcf)} {operating_cash_flow.unit}.")
        else:
            risks.append(f"Free cash flow was negative at {format_number(fcf)} {operating_cash_flow.unit}.")
    else:
        gaps.append("Operating cash flow and capex are needed for free-cash-flow conversion.")

    current_ratio = current_assets.value / current_liabilities.value if current_assets and current_liabilities and current_liabilities.value else None
    if current_ratio is not None:
        metrics_out.append(_metric("Current ratio", current_ratio, "x", _coverage_status(current_ratio, weak=1.0, strong=1.5), "Short-term asset coverage of current liabilities.", current_assets))
        if current_ratio < 1:
            risks.append(f"Current ratio is below 1.0x at {current_ratio:.1f}x.")
        elif current_ratio >= 1.5:
            positives.append(f"Current ratio is healthy at {current_ratio:.1f}x.")
    else:
        gaps.append("Current assets/current liabilities are needed for short-term liquidity analysis.")

    risk_level = _risk_level(risks, positives, metrics_out)
    status = "Available" if metrics_out else "Unavailable"
    if metrics_out and gaps:
        status = "Partial"
    summary = _summary(identity, risk_level, positives, risks, gaps)
    required = [
        "Debt footnote and maturity schedule.",
        "Cash location/restriction disclosure.",
        "Interest-rate mix, refinancing terms, covenants, and rating/spread evidence.",
        "Operating cash flow, capex, working-capital bridge, and dividend/buyback commitments.",
    ]
    credit_bridge = _credit_bridge_checks(
        by_name,
        cash,
        debt_metric,
        debt,
        net_debt,
        debt_to_revenue,
        interest_coverage,
        fcf,
        current_ratio,
        gaps,
    )
    monitor_rules = _monitor_rules(metrics_out, risks, gaps)
    credit_catalysts = _credit_catalysts(risk_level, risks, positives, gaps)
    falsification_tests = _falsification_tests(risk_level, risks, positives, gaps)
    return CreditLens(
        status=status,
        risk_level=risk_level,
        summary=summary,
        metrics=metrics_out,
        credit_bridge=credit_bridge,
        positives=_dedupe(positives)[:6],
        risks=_dedupe(risks)[:6],
        required_evidence=required,
        monitor_rules=monitor_rules,
        credit_catalysts=credit_catalysts,
        falsification_tests=falsification_tests,
        data_gaps=_dedupe(gaps)[:8],
    )


def _first_metric(by_name: dict[str, FinancialMetric], *names: str) -> FinancialMetric | None:
    for name in names:
        if name in by_name:
            return by_name[name]
    return None


def _sum_metrics(by_name: dict[str, FinancialMetric], *names: str) -> float | None:
    values = [by_name[name].value for name in names if name in by_name]
    return sum(values) if values else None


def _free_cash_flow(
    operating_cash_flow: FinancialMetric | None,
    capex: FinancialMetric | None,
) -> float | None:
    if not operating_cash_flow or not capex:
        return None
    if capex.value < 0:
        return operating_cash_flow.value + capex.value
    return operating_cash_flow.value - capex.value


def _metric(
    name: str,
    value: float | None,
    unit: str,
    status: str,
    interpretation: str,
    source_metric: FinancialMetric | None,
) -> CreditMetric:
    source = ""
    if source_metric:
        source = source_metric.source_kind or source_metric.form or "financial metric"
        if source_metric.period_end:
            source += f"; period {source_metric.period_end}"
    return CreditMetric(name, value, unit, status, interpretation, source)


def _ratio_status(value: float, low: float, high: float) -> str:
    if value <= low:
        return "Strong"
    if value >= high:
        return "Weak"
    return "Monitor"


def _coverage_status(value: float, weak: float = 2.0, strong: float = 5.0) -> str:
    if value < weak:
        return "Weak"
    if value >= strong:
        return "Strong"
    return "Monitor"


def _risk_level(risks: list[str], positives: list[str], metrics: list[CreditMetric]) -> str:
    weak = sum(1 for metric in metrics if metric.status == "Weak")
    strong = sum(1 for metric in metrics if metric.status in {"Strong", "Net cash"})
    if weak >= 2 or len(risks) >= 3:
        return "High"
    if weak or risks:
        return "Medium"
    if strong or positives:
        return "Low"
    return "Unknown"


def _summary(
    identity: CompanyIdentity,
    risk_level: str,
    positives: list[str],
    risks: list[str],
    gaps: list[str],
) -> str:
    if risk_level == "Unknown":
        return f"{identity.ticker} credit lens is inconclusive because structured liquidity and debt metrics are sparse."
    lead = f"{identity.ticker} credit risk screens as {risk_level.lower()} based on available structured metrics."
    if risks:
        return lead + " Main issue: " + risks[0]
    if positives:
        return lead + " Main support: " + positives[0]
    if gaps:
        return lead + " Key missing input: " + gaps[0]
    return lead


def _monitor_rules(
    metrics: list[CreditMetric],
    risks: list[str],
    gaps: list[str],
) -> list[str]:
    by_name = {metric.name: metric for metric in metrics}
    rows: list[str] = []
    coverage = by_name.get("Operating income / interest expense")
    if coverage and coverage.value is not None:
        rows.append(
            "interest_coverage_x <= 2.0 breaks credit comfort; >= 5.0 supports refinancing flexibility."
        )
    else:
        rows.append("interest_coverage_x must be calculated from operating income and interest expense.")
    leverage = by_name.get("Debt / revenue")
    if leverage and leverage.value is not None:
        rows.append("debt_to_revenue_x >= 1.5 flags elevated leverage; <= 0.5 supports balance-sheet flexibility.")
    else:
        rows.append("debt_to_revenue_x must be calculated from total debt and revenue.")
    current_ratio = by_name.get("Current ratio")
    if current_ratio and current_ratio.value is not None:
        rows.append("current_ratio_x < 1.0 flags short-term liquidity pressure; >= 1.5 supports liquidity.")
    elif any("Current assets/current liabilities" in gap for gap in gaps):
        rows.append("current_ratio_x requires current assets and current liabilities.")
    fcf = by_name.get("Free cash flow")
    if fcf and fcf.value is not None:
        rows.append("free_cash_flow turns negative or fails to cover dividends/buybacks requires thesis review.")
    else:
        rows.append("free_cash_flow requires operating cash flow and capex.")
    if risks:
        rows.append("new rating downgrade, covenant disclosure, or refinancing at materially higher coupon escalates risk.")
    return _dedupe(rows)[:8]


def _credit_catalysts(
    risk_level: str,
    risks: list[str],
    positives: list[str],
    gaps: list[str],
) -> list[str]:
    rows = [
        "Next earnings cash-flow statement and balance-sheet update.",
        "Debt maturity, refinancing, covenant, rating, or spread update.",
    ]
    if risk_level in {"High", "Medium"} or risks:
        rows.extend([
            "Management liquidity commentary and capex/capital-return guidance.",
            "Credit rating action, bond spread move, or bank facility amendment.",
        ])
    if positives:
        rows.append("Capital return, debt paydown, or cash-deployment update that confirms financial flexibility.")
    if gaps:
        rows.append("Disclosure that fills missing debt maturity, cash restriction, interest-rate mix, or FCF conversion inputs.")
    return _dedupe(rows)[:8]


def _credit_bridge_checks(
    by_name: dict[str, FinancialMetric],
    cash: FinancialMetric | None,
    debt_metric: FinancialMetric | None,
    debt: float | None,
    net_debt: float | None,
    debt_to_revenue: float | None,
    interest_coverage: float | None,
    fcf: float | None,
    current_ratio: float | None,
    gaps: list[str],
) -> list[CreditBridgeCheck]:
    rows = [
        _liquidity_bridge(cash, current_ratio, fcf, gaps),
        _refinancing_bridge(debt_metric, debt, net_debt, debt_to_revenue),
        _cash_flow_coverage_bridge(interest_coverage, fcf, gaps),
        _capital_allocation_bridge(by_name, fcf),
        _market_credit_confirmation_bridge(by_name),
    ]
    return rows


def _liquidity_bridge(
    cash: FinancialMetric | None,
    current_ratio: float | None,
    fcf: float | None,
    gaps: list[str],
) -> CreditBridgeCheck:
    evidence: list[str] = []
    missing: list[str] = []
    if cash:
        evidence.append(f"Cash {format_number(cash.value)} {cash.unit}.")
    else:
        missing.append("Cash balance from structured facts.")
    if current_ratio is not None:
        evidence.append(f"Current ratio {current_ratio:.1f}x.")
    else:
        missing.append("Current assets and current liabilities.")
    if fcf is not None:
        evidence.append(f"Free cash flow {format_number(fcf)} {cash.unit if cash else 'currency'}.")
    else:
        missing.append("Operating cash flow and capex.")
    missing.extend([
        "Cash location/restriction disclosure.",
        "Revolver availability and covenant headroom.",
    ])
    if not evidence:
        status = "Missing structured evidence"
    elif current_ratio is not None and current_ratio >= 1.5 and fcf is not None and fcf > 0:
        status = "Supported, needs footnote corroboration"
    else:
        status = "Partial, needs liquidity bridge"
    return CreditBridgeCheck(
        area="Liquidity runway",
        status=status,
        credit_question="Can available liquidity cover working capital, near-term obligations, and stress without new financing?",
        current_evidence=" ".join(evidence) if evidence else "No structured liquidity evidence yet.",
        required_evidence=[
            "Balance sheet cash and working-capital facts.",
            "Cash restriction/location disclosure.",
            "Revolver availability, covenant headroom, and near-term obligations.",
        ],
        missing_evidence=_dedupe(missing)[:6],
        next_source="Liquidity footnote, debt footnote, revolver table, covenant disclosure, and cash-flow statement.",
        equity_implication="Liquidity support can protect buybacks, capex, and valuation downside if cash is usable.",
        credit_implication="Liquidity weakness raises refinancing and default-risk sensitivity before income-statement stress appears.",
        falsification_test="Liquidity support is falsified if cash is restricted/trapped, revolver availability is low, or FCF turns negative.",
        stage_impact="Research-Ready only if liquidity evidence is sourced; High-Conviction needs footnote and maturity corroboration.",
    )


def _refinancing_bridge(
    debt_metric: FinancialMetric | None,
    debt: float | None,
    net_debt: float | None,
    debt_to_revenue: float | None,
) -> CreditBridgeCheck:
    evidence: list[str] = []
    missing: list[str] = []
    unit = debt_metric.unit if debt_metric else "currency"
    if debt is not None:
        evidence.append(f"Total debt {format_number(debt)} {unit}.")
    else:
        missing.append("Current debt and long-term debt.")
    if net_debt is not None:
        label = "net cash" if net_debt < 0 else "net debt"
        evidence.append(f"{label.title()} {format_number(abs(net_debt))} {unit}.")
    else:
        missing.append("Net debt from cash and debt.")
    if debt_to_revenue is not None:
        evidence.append(f"Debt/revenue {debt_to_revenue:.1f}x.")
    else:
        missing.append("Revenue-aligned leverage ratio.")
    missing.extend([
        "Debt maturity schedule.",
        "Coupon/rate mix and secured/unsecured ranking.",
        "Refinancing transactions, amendments, and covenant terms.",
    ])
    if debt is None:
        status = "Missing debt evidence"
    elif net_debt is not None and net_debt < 0:
        status = "Lower refinancing pressure, needs maturity proof"
    elif debt_to_revenue is not None and debt_to_revenue >= 1.5:
        status = "Refinancing risk needs primary evidence"
    else:
        status = "Monitor, maturity evidence missing"
    return CreditBridgeCheck(
        area="Debt maturity and refinancing",
        status=status,
        credit_question="Do upcoming maturities, coupons, and covenant terms change refinancing risk or equity optionality?",
        current_evidence=" ".join(evidence) if evidence else "No structured debt evidence yet.",
        required_evidence=[
            "Current and long-term debt facts.",
            "Debt maturity schedule and coupon/rate mix.",
            "Facility amendments, covenant headroom, and refinancing disclosures.",
        ],
        missing_evidence=_dedupe(missing)[:6],
        next_source="Debt footnote, maturity table, covenant/credit agreement exhibit, 8-K financing disclosure, and bond/pricing data if available.",
        equity_implication="Refinancing pressure can reduce buyback capacity and compress equity multiples even before EPS changes.",
        credit_implication="Near-term maturities or higher coupons can move spread/rating risk and recovery assumptions.",
        falsification_test="Refinancing concern is falsified if maturities are long-dated, pre-funded, or refinanced without higher spread/covenant cost.",
        stage_impact="Debt-sensitive ideas need maturity and covenant evidence before High-Conviction.",
    )


def _cash_flow_coverage_bridge(
    interest_coverage: float | None,
    fcf: float | None,
    gaps: list[str],
) -> CreditBridgeCheck:
    evidence: list[str] = []
    missing: list[str] = []
    if interest_coverage is not None:
        evidence.append(f"Operating income/interest expense {interest_coverage:.1f}x.")
    else:
        missing.append("Operating income and interest expense.")
    if fcf is not None:
        evidence.append(f"Free cash flow {format_number(fcf)}.")
    else:
        missing.append("Operating cash flow and capex.")
    missing.extend([
        "Working-capital bridge.",
        "Interest cash paid and capitalized interest where disclosed.",
        "Dividend, buyback, lease, and debt-service commitments.",
    ])
    if interest_coverage is not None and interest_coverage < 2:
        status = "Coverage pressure"
    elif interest_coverage is not None and interest_coverage >= 5 and fcf is not None and fcf > 0:
        status = "Coverage support, needs cash-flow quality"
    elif evidence:
        status = "Partial coverage evidence"
    else:
        status = "Missing coverage evidence"
    if gaps:
        missing.extend([gap for gap in gaps if "coverage" in gap.lower() or "cash" in gap.lower()][:2])
    return CreditBridgeCheck(
        area="Cash-flow debt service",
        status=status,
        credit_question="Can recurring cash generation cover interest, capex, maturities, and capital returns?",
        current_evidence=" ".join(evidence) if evidence else "No structured coverage evidence yet.",
        required_evidence=[
            "Operating income, interest expense, OCF, and capex.",
            "Working-capital and nonrecurring cash-flow detail.",
            "Debt-service, dividend, buyback, and lease commitments.",
        ],
        missing_evidence=_dedupe(missing)[:6],
        next_source="Cash-flow statement, MD&A liquidity discussion, interest footnote, commitment table, and capital-allocation disclosure.",
        equity_implication="Durable cash coverage supports valuation and capital return; weak coverage can make headline earnings low quality.",
        credit_implication="Coverage deterioration can lead rating/spread pressure even if liquidity appears adequate.",
        falsification_test="Coverage concern is falsified if FCF after capex and commitments remains positive through the cycle.",
        stage_impact="Coverage bridge must be explicit for credit-sensitive Research-Ready ideas.",
    )


def _capital_allocation_bridge(
    by_name: dict[str, FinancialMetric],
    fcf: float | None,
) -> CreditBridgeCheck:
    dividends = _first_metric(by_name, "Dividends Paid", "Payments of Dividends")
    buybacks = _first_metric(
        by_name,
        "Share Repurchases",
        "Payments for Repurchase of Common Stock",
        "Repurchases of Common Stock",
    )
    evidence: list[str] = []
    missing: list[str] = []
    if dividends:
        evidence.append(f"Dividends paid {format_number(abs(dividends.value))} {dividends.unit}.")
    else:
        missing.append("Dividend cash paid.")
    if buybacks:
        evidence.append(f"Share repurchases {format_number(abs(buybacks.value))} {buybacks.unit}.")
    else:
        missing.append("Buyback cash paid or authorization detail.")
    if fcf is not None:
        evidence.append(f"Free cash flow {format_number(fcf)}.")
    else:
        missing.append("Free cash flow after capex.")
    status = "Partial capital-allocation evidence" if evidence else "Missing capital-allocation evidence"
    if fcf is not None and (dividends or buybacks):
        status = "Needs creditor/equity tradeoff analysis"
    return CreditBridgeCheck(
        area="Capital allocation versus creditors",
        status=status,
        credit_question="Are dividends, buybacks, capex, or M&A competing with debt service and liquidity preservation?",
        current_evidence=" ".join(evidence) if evidence else "No structured capital-allocation evidence yet.",
        required_evidence=[
            "Dividend and buyback cash flows.",
            "Capex, M&A, lease, and debt-service commitments.",
            "Management capital-allocation commitments.",
        ],
        missing_evidence=_dedupe(missing)[:6],
        next_source="Cash-flow statement, repurchase footnote, capital-return authorization, MD&A liquidity section, and earnings-call capital-allocation comments.",
        equity_implication="Capital returns can support EPS and shareholder yield, but may be lower quality if debt-funded.",
        credit_implication="Debt-funded shareholder returns can pressure creditor protection and rating outlook.",
        falsification_test="Capital-allocation concern is falsified if shareholder returns are fully covered by recurring FCF after debt service.",
        stage_impact="Capital-return thesis needs source-linked funding bridge before Research-Ready.",
    )


def _market_credit_confirmation_bridge(by_name: dict[str, FinancialMetric]) -> CreditBridgeCheck:
    spread = _first_metric(by_name, "Credit Spread", "Bond Spread", "CDS Spread")
    rating_marker = _first_metric(by_name, "Rating Action", "Credit Rating")
    evidence: list[str] = []
    missing: list[str] = []
    if spread:
        evidence.append(f"Credit spread marker {format_number(spread.value)} {spread.unit}.")
    else:
        missing.append("Bond spread, CDS, or credit-market price evidence.")
    if rating_marker:
        evidence.append(f"Rating marker {format_number(rating_marker.value)} {rating_marker.unit}.")
    else:
        missing.append("Rating action or outlook evidence.")
    status = "Available" if evidence else "Unavailable, do not infer"
    return CreditBridgeCheck(
        area="Rating and spread confirmation",
        status=status,
        credit_question="Does market or agency evidence confirm the accounting-based credit signal?",
        current_evidence=" ".join(evidence) if evidence else "No structured rating, spread, CDS, or bond-price evidence is available.",
        required_evidence=[
            "Rating action/outlook or agency commentary.",
            "Bond spread, CDS, TRACE, or comparable credit-market pricing.",
            "Company-specific financing transaction evidence.",
        ],
        missing_evidence=_dedupe(missing)[:6],
        next_source="Rating agency release, bond spread/CDS data, TRACE-style bond pricing, 8-K financing disclosure, or manual credit-market import.",
        equity_implication="Stable credit-market evidence can limit the equity relevance of an accounting leverage signal.",
        credit_implication="Spread widening or downgrade risk can be the direct credit thesis, but it cannot be inferred from accounting facts alone.",
        falsification_test="Accounting credit concern is weakened if spreads, financing terms, and ratings remain stable after the event.",
        stage_impact="High-Conviction credit conclusions require rating/spread evidence or an explicit unavailable-data caveat.",
    )


def _falsification_tests(
    risk_level: str,
    risks: list[str],
    positives: list[str],
    gaps: list[str],
) -> list[str]:
    rows: list[str] = []
    if risk_level in {"High", "Medium"} or risks:
        rows.extend([
            "Risk thesis weakens if maturities are long-dated, cash is unrestricted, and refinancing is pre-funded.",
            "Risk thesis weakens if FCF covers interest, maturities, dividends, and committed buybacks without new debt.",
            "Risk thesis weakens if rating/spread evidence is stable despite accounting leverage concerns.",
        ])
    if positives:
        rows.extend([
            "Balance-sheet support weakens if cash is restricted, offshore/trapped, seasonal, or offset by near-term maturities.",
            "Balance-sheet support weakens if positive FCF is working-capital timing rather than durable conversion.",
        ])
    if gaps:
        rows.append("Credit conclusion is not thesis-grade until missing debt/cash/coverage evidence is explicitly sourced or marked unavailable.")
    return _dedupe(rows)[:8]


def _dedupe(values: list[str]) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            rows.append(text)
            seen.add(text)
    return rows
