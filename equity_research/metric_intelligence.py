from __future__ import annotations

import hashlib

from .models import (
    CausalHypothesis,
    ChangeEvent,
    FinancialMetric,
    MetricChangeAssessment,
    MetricInterpretationPolicy,
)
from .research_profiles import event_identifier


_POLICIES = {
    "goodwill": MetricInterpretationPolicy(
        metric_key="Goodwill",
        driver_family="acquisition_accounting",
        default_polarity="neutral",
        interpretation="Ambiguous / acquisition accounting",
        constructive_mechanisms=[
            "A strategic acquisition may add durable revenue, technology, distribution, or talent.",
            "Purchase accounting can reveal a deliberate reinvestment cycle with identifiable synergies.",
        ],
        adverse_mechanisms=[
            "A large premium over identifiable net assets may indicate overpayment or weak capital discipline.",
            "Execution shortfalls or weaker acquired cash flows can create future impairment risk.",
        ],
        required_evidence=[
            "Acquisition agreement and purchase-price allocation.",
            "Consideration paid, acquired revenue/profit, identifiable intangibles, and goodwill by segment.",
            "Synergy targets, integration costs, impairment tests, and post-acquisition performance.",
        ],
        valuation_effects=["Acquired earnings and synergies", "ROIC versus cost of capital", "Impairment risk"],
        credit_effects=["Acquisition funding", "Leverage and liquidity", "Covenant and rating headroom"],
        falsification_tests=[
            "Acquired growth or synergies fail to materialize.",
            "Management records an impairment or acquisition ROIC remains below the cost of capital.",
        ],
    ),
    "capital expenditure": MetricInterpretationPolicy(
        metric_key="Capital Expenditure",
        driver_family="investment_cycle",
        default_polarity="neutral",
        interpretation="Ambiguous / investment cycle",
        constructive_mechanisms=[
            "Capacity, infrastructure, or product investment may support future revenue and strategic control.",
            "Investment may relieve supply constraints or improve unit economics at scale.",
        ],
        adverse_mechanisms=[
            "Capex intensity may depress near-term FCF and returns if demand or utilization disappoints.",
            "Rapid spending can signal an arms race, maintenance burden, or weak capital efficiency.",
        ],
        required_evidence=[
            "Capex purpose, capacity, geography, and project timing.",
            "Capex/revenue, depreciation, utilization, operating cash flow, and FCF history.",
            "Management return targets and aligned peer investment plans.",
        ],
        valuation_effects=["FCF conversion", "Revenue capacity", "Depreciation and return on invested capital"],
        credit_effects=["Liquidity consumption", "Funding requirement", "Coverage and leverage headroom"],
        falsification_tests=[
            "Utilization, demand, or incremental margins do not support the investment case.",
            "Capex remains elevated without a credible revenue, cost, or strategic payoff.",
        ],
    ),
    "revenue": MetricInterpretationPolicy(
        "Revenue", "revenue", "directional", "Demand / price / mix",
        ["Volume, price, mix, FX, or segment demand improved."],
        ["Growth may be acquisition-led, FX-led, low quality, or offset by weaker margins."],
        ["Segment revenue", "Volume/price or customer KPI", "Comparable-period peer demand"],
        ["Growth and operating leverage"], ["Cash generation and coverage"],
        ["Organic growth or demand KPIs do not corroborate reported revenue."],
    ),
    "gross profit": MetricInterpretationPolicy(
        "Gross Profit", "margin", "directional", "Revenue and gross-margin bridge",
        ["Revenue, pricing, mix, or input-cost efficiency improved gross profit."],
        ["Gross profit growth may trail revenue or rely on temporary mix and cost benefits."],
        ["Revenue", "COGS", "Gross margin", "Segment mix and pricing"],
        ["EPS and FCF conversion"], ["Cash conversion and coverage"],
        ["Gross margin or peer economics contradict the reported direction."],
    ),
    "cash": MetricInterpretationPolicy(
        "Cash", "liquidity", "neutral", "Ambiguous / cash-flow reconciliation",
        ["Durable free cash flow may expand capital-return and downside optionality."],
        ["Debt issuance, asset sales, working-capital timing, or restricted cash may explain the increase."],
        ["Cash-flow statement", "Capex", "Buybacks/dividends", "Debt issuance and repayment"],
        ["Net cash and capital allocation"], ["Liquidity and refinancing capacity"],
        ["Ending cash is not supported by recurring free cash flow."],
    ),
    "long-term debt": MetricInterpretationPolicy(
        "Long-term Debt", "debt", "directional_inverse", "Debt / refinancing",
        ["Debt reduction can improve equity optionality and rating headroom."],
        ["Debt growth can increase interest burden and refinancing risk."],
        ["Debt footnote", "Maturity schedule", "Interest expense", "Cash and covenants"],
        ["Enterprise value and equity optionality"], ["Leverage, coverage, and maturity risk"],
        ["Cash, coverage, or low-risk refinancing neutralizes the balance-sheet change."],
    ),
}


def metric_policy_for(metric_name: str | None) -> MetricInterpretationPolicy:
    normalized = str(metric_name or "").strip().lower()
    if normalized in _POLICIES:
        return _POLICIES[normalized]
    if "goodwill" in normalized:
        return _POLICIES["goodwill"]
    if "capital expenditure" in normalized or normalized == "capex":
        return _POLICIES["capital expenditure"]
    if "revenue" in normalized and "cost" not in normalized:
        return _POLICIES["revenue"]
    if "gross profit" in normalized or "gross margin" in normalized or "cost of revenue" in normalized:
        return _POLICIES["gross profit"]
    if normalized in {"cash", "operating cash flow", "free cash flow"}:
        return _POLICIES["cash"]
    if "debt" in normalized or "borrow" in normalized:
        return _POLICIES["long-term debt"]
    if "operating income" in normalized or "operating expense" in normalized:
        return MetricInterpretationPolicy(
            metric_name or "Operating metric", "operating", "directional", "Operating leverage",
            ["Revenue and gross profit may be scaling faster than operating costs."],
            ["Restructuring, capitalization, or temporary cost timing may distort the change."],
            ["Revenue", "Gross profit", "Major opex lines", "Segment operating income"],
            ["EBIT, EPS, and FCF"], ["Interest coverage"],
            ["Underlying margin or cash conversion does not corroborate operating leverage."],
        )
    if "net income" in normalized or "earnings" in normalized or "eps" in normalized:
        return MetricInterpretationPolicy(
            metric_name or "Earnings", "net_income", "directional", "Net income / EPS bridge",
            ["Operating profit and cash conversion may support recurring earnings."],
            ["Tax, interest, investment gains, FX, or share count may make earnings non-recurring."],
            ["Operating income", "Interest", "Tax", "Other income", "Share reconciliation"],
            ["EPS and P/E"], ["Retained earnings and coverage"],
            ["Operating profit and cash flow fail to corroborate earnings."],
        )
    return MetricInterpretationPolicy(
        metric_name or "Unknown",
        "unmapped",
        "neutral",
        "Unmapped / investigation required",
        ["The change may reflect a constructive operating or accounting development."],
        ["The change may reflect deterioration, one-time accounting, or a basis mismatch."],
        ["Metric definition", "Comparable period", "Footnote or source table", "Related operating KPI"],
        ["Unknown until mapped"],
        ["Unknown until mapped"],
        ["The metric cannot be reconciled to a comparable operating or accounting basis."],
    )


def apply_metric_policy(event: ChangeEvent) -> MetricInterpretationPolicy:
    metric_name = str(event.metrics.get("metric_name") or event.metrics.get("economic_driver") or event.title)
    policy = metric_policy_for(metric_name)
    event.metrics["metric_policy_key"] = policy.metric_key
    event.metrics["driver_family"] = policy.driver_family
    event.metrics["metric_interpretation"] = policy.interpretation
    event.metrics["default_polarity"] = policy.default_polarity
    event.metrics["constructive_mechanisms"] = list(policy.constructive_mechanisms)
    event.metrics["adverse_mechanisms"] = list(policy.adverse_mechanisms)
    event.metrics["required_evidence"] = list(policy.required_evidence)
    event.metrics["valuation_effects"] = list(policy.valuation_effects)
    event.metrics["credit_effects"] = list(policy.credit_effects)
    event.metrics["falsification_tests"] = list(policy.falsification_tests)
    if policy.default_polarity == "neutral":
        event.direction = "neutral"
        event.metrics["direction_validation_required"] = True
    if policy.driver_family == "unmapped":
        event.metrics["economic_driver"] = "Unmapped"
        event.metrics["driver_materiality"] = "Unknown"
    return policy


def build_metric_change_assessments(
    events: list[ChangeEvent], metrics: list[FinancialMetric], selected_event_ids: list[str] | None = None,
) -> list[MetricChangeAssessment]:
    selected = set(selected_event_ids or [])
    metric_history = {item.name: item for item in metrics}
    rows: list[MetricChangeAssessment] = []
    for event in events:
        if event.category not in {"financial_kpi", "margin"}:
            continue
        event_id = event_identifier(event)
        if selected and event_id not in selected:
            continue
        policy = apply_metric_policy(event)
        metric_name = str(event.metrics.get("metric_name") or policy.metric_key)
        metric = metric_history.get(metric_name)
        observation = event.summary
        if metric and metric.yoy_change_pct is not None:
            observation = f"{metric.name} changed {metric.yoy_change_pct:+.1f}% for {metric.period_end}."
        constructive = _hypothesis(event_id, "Constructive", policy.constructive_mechanisms, policy)
        adverse = _hypothesis(event_id, "Adverse", policy.adverse_mechanisms, policy)
        rows.append(MetricChangeAssessment(
            event_id=event_id,
            metric_name=metric_name,
            event_label=f"{event.source} {event.event_date or 'date unknown'}: {event.title}",
            observed_change=observation,
            polarity="Neutral pending investigation" if policy.default_polarity == "neutral" else event.direction.title(),
            driver_family=policy.driver_family,
            interpretation=policy.interpretation,
            constructive_hypothesis=constructive,
            adverse_hypothesis=adverse,
            historical_trend=_historical_trend(metric),
            evidence_labels=["Primary fact"] if event.citations else ["Unknown"],
            next_automatic_action=(
                "Fetch and reconcile " + ", ".join(policy.required_evidence[:3])
                if policy.required_evidence else "Map the metric before directional interpretation."
            ),
            data_gaps=list(policy.required_evidence),
        ))
    return rows


def _hypothesis(
    event_id: str, side: str, mechanisms: list[str], policy: MetricInterpretationPolicy,
) -> CausalHypothesis:
    mechanism = mechanisms[0] if mechanisms else "No mechanism mapped."
    raw = f"{event_id}|{side}|{mechanism}"
    return CausalHypothesis(
        hypothesis_id=hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16],
        side=side,
        mechanism=mechanism,
        status="Investigation required",
        financial_effects=list(policy.valuation_effects + policy.credit_effects),
        next_action=(
            "Validate with " + ", ".join(policy.required_evidence[:2])
            if policy.required_evidence else "Map a source-backed causal mechanism."
        ),
    )


def _historical_trend(metric: FinancialMetric | None) -> str:
    if not metric or metric.yoy_change_pct is None:
        return "Comparable historical trend unavailable."
    return (
        f"Latest comparable change is {metric.yoy_change_pct:+.1f}% for {metric.period_end}; "
        "the profile history should confirm whether this is persistent or event-specific."
    )
