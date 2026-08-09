from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


WELL_COVERED_SECURITIES = {
    "AAPL", "AMZN", "GOOGL", "GOOG", "META", "MSFT", "NVDA", "TSLA",
}


@dataclass(frozen=True)
class MappingEscalationStep:
    step: str
    status: str
    explanation: str
    admitted_evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class MappingEscalationResult:
    observation_kind: str
    status: str
    label: str
    resolved_mapping: str = ""
    resolution_method: str = ""
    steps: tuple[MappingEscalationStep, ...] = ()
    unresolved_reason: str = ""
    mapping_regression: bool = False


def resolve_observation_mapping(
    *,
    ticker: str,
    metric_name: str,
    category: str,
    title: str,
    metrics: dict[str, Any] | None = None,
    taxonomy_aliases: dict[str, str] | None = None,
    primary_model_candidate: dict[str, Any] | None = None,
    frozen_auditor_candidate: dict[str, Any] | None = None,
    discovery_candidate: dict[str, Any] | None = None,
    specialist_candidate: dict[str, Any] | None = None,
    automation_exhausted: bool = False,
) -> MappingEscalationResult:
    """Apply the shared automated mapping ladder before asking a user.

    Narrative clues are disclosure research leads, never metric-map errors.
    Model proposals resolve a mapping only when they cite admitted evidence.
    """
    metrics = dict(metrics or {})
    observation_kind = _observation_kind(metric_name, category, title, metrics)
    if observation_kind == "narrative_disclosure":
        return MappingEscalationResult(
            observation_kind=observation_kind,
            status="research_lead",
            label="Disclosure risk / research lead",
            resolution_method="narrative_disclosure_classification",
            steps=(MappingEscalationStep(
                step="observation_classification",
                status="resolved",
                explanation=(
                    "Narrative filing language is routed to disclosure comparison and "
                    "corroboration, not numeric metric mapping."
                ),
            ),),
        )

    steps: list[MappingEscalationStep] = []
    aliases = {
        str(key).casefold().strip(): str(value).strip()
        for key, value in (taxonomy_aliases or {}).items()
        if key and value
    }
    alias = aliases.get(metric_name.casefold().strip())
    steps.append(MappingEscalationStep(
        step="taxonomy_and_issuer_aliases",
        status="resolved" if alias else "not_resolved",
        explanation=(
            f"Resolved to {alias}." if alias
            else "No canonical taxonomy or issuer alias resolved the numeric observation."
        ),
    ))
    if alias:
        return _resolved(observation_kind, alias, "taxonomy_and_issuer_aliases", steps)

    contextual = str(
        metrics.get("canonical_metric")
        or metrics.get("contextual_metric_mapping")
        or ""
    ).strip()
    context_complete = bool(
        contextual
        and (metrics.get("period_end") or metrics.get("current_period"))
        and (metrics.get("unit") or metrics.get("currency"))
    )
    steps.append(MappingEscalationStep(
        step="filing_table_segment_period_unit_context",
        status="resolved" if context_complete else "not_resolved",
        explanation=(
            f"Resolved to {contextual} using filing context." if context_complete
            else "Filing, table, segment, period, and unit context did not fully resolve the metric."
        ),
    ))
    if context_complete:
        return _resolved(observation_kind, contextual, "filing_context", steps)

    for step_name, candidate in (
        ("primary_model_mapping", primary_model_candidate),
        ("frozen_auditor_challenge", frozen_auditor_candidate),
        ("adversarial_mapping_discovery", discovery_candidate),
        ("registered_retrieval_and_specialist_review", specialist_candidate),
    ):
        proposed = str((candidate or {}).get("proposed_mapping") or "").strip()
        evidence_ids = tuple(
            str(value)
            for value in (candidate or {}).get("admitted_evidence_ids", [])
            if value
        )
        admitted = bool(proposed and evidence_ids)
        steps.append(MappingEscalationStep(
            step=step_name,
            status="resolved" if admitted else "not_resolved",
            explanation=(
                f"Resolved to {proposed} with admitted evidence."
                if admitted else
                "No evidence-admitted mapping proposal resolved this step."
            ),
            admitted_evidence_ids=evidence_ids,
        ))
        if admitted:
            return _resolved(observation_kind, proposed, step_name, steps)

    regression = bool(
        automation_exhausted
        and ticker.upper() in WELL_COVERED_SECURITIES
    )
    return MappingEscalationResult(
        observation_kind=observation_kind,
        status="unresolved",
        label="Numeric metric mapping unresolved",
        steps=tuple(steps),
        unresolved_reason=(
            "All automated mapping steps were exhausted without an evidence-admitted canonical mapping."
            if automation_exhausted else
            "Automated mapping remains in progress; do not ask the user to map it yet."
        ),
        mapping_regression=regression,
    )


def _resolved(
    observation_kind: str,
    mapping: str,
    method: str,
    steps: Iterable[MappingEscalationStep],
) -> MappingEscalationResult:
    return MappingEscalationResult(
        observation_kind=observation_kind,
        status="resolved",
        label="Numeric metric mapped",
        resolved_mapping=mapping,
        resolution_method=method,
        steps=tuple(steps),
    )


def _observation_kind(
    metric_name: str,
    category: str,
    title: str,
    metrics: dict[str, Any],
) -> str:
    text = f"{metric_name} {category} {title}".casefold()
    if any(token in text for token in (
        "risk factor", "risk_factors", "disclosure", "item 1a", "item 3.d",
    )):
        return "narrative_disclosure"
    numeric_value = any(
        isinstance(metrics.get(key), (int, float))
        for key in ("value", "current_value", "previous_value", "change_pct")
    )
    if numeric_value or category in {"financial_kpi", "margin"}:
        return "numeric_observation"
    return "narrative_clue"
