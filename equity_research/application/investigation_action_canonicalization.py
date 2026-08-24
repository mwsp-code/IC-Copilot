"""Compatibility canonicalization for user-facing investigation actions.

Saved research is immutable, so older action queues cannot be rewritten when the
semantic-key contract improves.  This module derives a canonical, displayable
queue in memory and retains indexes from every historical identifier to the new
identity.  It deliberately has no persistence or provider dependencies.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import re
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from ..models import PrioritizedResearchAction


CANONICAL_ACTION_IDENTITY_VERSION = "investigation-action-v2"

_RESOLVED_STATUSES = {
    "answered",
    "closed",
    "completed",
    "not_needed",
    "resolved",
}
_MATERIALITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}
_BLOCKER_RANK = {
    "research-ready": 4,
    "research ready": 4,
    "research qa": 3,
    "high-conviction": 2,
    "high conviction": 2,
    "thesis quality": 1,
}
_STATUS_RANK = {
    "failed": 8,
    "retrieval_failed": 8,
    "unavailable": 7,
    "genuinely_unavailable": 7,
    "licensed_or_manual_required": 6,
    "not_attempted_missing_connector": 6,
    "attempted_unresolved": 5,
    "in_progress": 4,
    "retrieval_requested": 4,
    "planned": 3,
    "pending": 3,
    "open": 3,
}
_COMPANY_LEVEL_KINDS = {
    "cash_conversion_analysis",
    "operating_driver_analysis",
    "valuation_scenarios",
}

_SOURCE_FAMILY_ALIASES = {
    "10_k": "sec_filing",
    "10_q": "sec_filing",
    "6_k": "sec_filing",
    "8_k": "sec_filing",
    "cash_flow_statement": "sec_filing",
    "company_facts": "sec_filing",
    "issuer_filing": "sec_filing",
    "issuer_filings": "sec_filing",
    "sec_companyfacts": "sec_filing",
    "sec_filings": "sec_filing",
    "earnings_release": "issuer_ir_report",
    "issuer_release": "issuer_ir_report",
    "investor_presentation": "issuer_ir_report",
    "presentation": "issuer_ir_report",
    "transcript": "earnings_transcript",
    "consensus": "point_in_time_consensus",
    "consensus_manual": "point_in_time_consensus",
    "point_in_time_consensus": "point_in_time_consensus",
    "historical_price": "quantitative_historical_price",
    "market_data": "quantitative_historical_price",
    "price_data": "quantitative_historical_price",
}


@dataclass(frozen=True)
class CanonicalActionAliases:
    """Historical references that resolve to one canonical action."""

    canonical_action_id: str
    canonical_semantic_key: str
    action_ids: tuple[str, ...]
    semantic_keys: tuple[str, ...]


@dataclass(frozen=True)
class InvestigationActionDisplayCollision:
    """Distinct research targets that would look identical to a user."""

    normalized_display: str
    display_text: str
    canonical_action_ids: tuple[str, ...]
    original_action_ids: tuple[str, ...]
    reason: str = (
        "Distinct canonical investigation targets produced the same display text."
    )


@dataclass(frozen=True)
class CanonicalInvestigationActionSet:
    """Canonical actions plus compatibility indexes for historical references."""

    actions: tuple[PrioritizedResearchAction, ...]
    aliases_by_canonical_id: Mapping[str, CanonicalActionAliases]
    action_id_index: Mapping[str, str]
    semantic_key_index: Mapping[str, str]
    display_collisions: tuple[InvestigationActionDisplayCollision, ...]
    identity_version: str = CANONICAL_ACTION_IDENTITY_VERSION

    def resolve_action_id(self, reference: str) -> str | None:
        """Resolve a historical action ID or semantic key."""

        return self.action_id_index.get(reference) or self.semantic_key_index.get(
            reference
        )


@dataclass
class _ActionGroup:
    action: PrioritizedResearchAction
    first_position: int
    action_ids: list[str]
    semantic_keys: list[str]


def canonicalize_investigation_actions(
    ticker: str,
    actions: Iterable[PrioritizedResearchAction],
    *,
    intent_mapping: Mapping[str, str | Mapping[str, Any]] | None = None,
) -> CanonicalInvestigationActionSet:
    """Clone, normalize, merge, filter, and rerank an investigation queue.

    ``intent_mapping`` is an optional compatibility escape hatch for callers
    that possess target context not present in an old serialized action.  Keys
    may be an action ID or semantic key.  Values may be an opaque target string
    or a mapping containing ``target``, ``event_id``, ``claim_id``, ``theme``,
    or ``valuation_input``.
    """

    normalized_ticker = str(ticker or "").strip().upper() or "UNKNOWN"
    groups: dict[str, _ActionGroup] = {}

    for position, original in enumerate(actions):
        candidate = deepcopy(original)
        original_action_id = str(getattr(original, "action_id", "") or "")
        original_semantic_key = str(
            getattr(original, "semantic_key", "") or ""
        )
        inherited_action_ids = list(
            getattr(original, "alias_action_ids", ()) or ()
        )
        inherited_semantic_keys = list(
            getattr(original, "alias_semantic_keys", ()) or ()
        )
        kind = _normalize_token(
            str(getattr(candidate, "action_kind", "") or "primary_source_validation")
        ) or "primary_source_validation"
        kind = _canonical_kind(candidate, kind)
        candidate.action_kind = kind
        candidate.required_source_families = _normalize_source_families(
            getattr(candidate, "required_source_families", ()) or ()
        )
        target_type, target = _canonical_target(
            normalized_ticker,
            candidate,
            intent_mapping or {},
            original_action_id=original_action_id,
            original_semantic_key=original_semantic_key,
        )
        canonical_semantic_key = _canonical_semantic_key(
            normalized_ticker,
            kind,
            target_type,
            target,
            intent_id=str(getattr(candidate, "intent_id", "") or ""),
        )
        canonical_action_id = _canonical_action_id(canonical_semantic_key)
        candidate.action_id = canonical_action_id
        candidate.canonical_action_id = canonical_action_id
        candidate.semantic_key = canonical_semantic_key
        candidate.semantic_key_version = CANONICAL_ACTION_IDENTITY_VERSION
        candidate.target_type = target_type
        candidate.target_id = target

        group = groups.get(canonical_semantic_key)
        if group is None:
            groups[canonical_semantic_key] = _ActionGroup(
                action=candidate,
                first_position=position,
                action_ids=_unique((
                    original_action_id,
                    canonical_action_id,
                    *inherited_action_ids,
                )),
                semantic_keys=_unique(
                    (
                        original_semantic_key,
                        canonical_semantic_key,
                        *inherited_semantic_keys,
                    )
                ),
            )
            continue

        _merge_action(group.action, candidate)
        group.action_ids = _unique((
            *group.action_ids,
            original_action_id,
            *inherited_action_ids,
        ))
        group.semantic_keys = _unique(
            (
                *group.semantic_keys,
                original_semantic_key,
                *inherited_semantic_keys,
            )
        )

    visible_groups = [
        group for group in groups.values()
        if not _is_resolved(group.action.status)
    ]
    visible_groups.sort(key=_group_sort_key)
    for rank, group in enumerate(visible_groups, start=1):
        group.action.rank = rank

    action_id_index: dict[str, str] = {}
    semantic_key_index: dict[str, str] = {}
    aliases_by_canonical: dict[str, CanonicalActionAliases] = {}
    for group in groups.values():
        canonical_id = group.action.action_id
        aliases = CanonicalActionAliases(
            canonical_action_id=canonical_id,
            canonical_semantic_key=group.action.semantic_key,
            action_ids=tuple(group.action_ids),
            semantic_keys=tuple(group.semantic_keys),
        )
        aliases_by_canonical[canonical_id] = aliases
        group.action.alias_action_ids = list(aliases.action_ids)
        group.action.alias_semantic_keys = list(aliases.semantic_keys)
        for value in aliases.action_ids:
            if value:
                action_id_index[value] = canonical_id
        for value in aliases.semantic_keys:
            if value:
                semantic_key_index[value] = canonical_id

    visible_actions = tuple(group.action for group in visible_groups)
    return CanonicalInvestigationActionSet(
        actions=visible_actions,
        aliases_by_canonical_id=MappingProxyType(aliases_by_canonical),
        action_id_index=MappingProxyType(action_id_index),
        semantic_key_index=MappingProxyType(semantic_key_index),
        display_collisions=_display_collisions(
            visible_groups, aliases_by_canonical
        ),
    )


def _canonical_target(
    ticker: str,
    action: PrioritizedResearchAction,
    intent_mapping: Mapping[str, str | Mapping[str, Any]],
    *,
    original_action_id: str,
    original_semantic_key: str,
) -> tuple[str, str]:
    explicit = _mapped_target(
        intent_mapping,
        original_action_id,
        original_semantic_key,
    )
    if explicit:
        return explicit

    kind = action.action_kind
    event_id = str(getattr(action, "event_id", "") or "").strip()
    if event_id:
        return "event", _normalized_identity_value(event_id)

    objective = _action_display(action)
    if kind == "market_event_attribution":
        dated = _date_from_text(objective)
        return "event", dated or _objective_fingerprint(objective)

    if kind == "valuation_input_resolution":
        return "valuation_input", _valuation_input_target(objective, ticker)

    if kind == "external_theme_validation":
        return "theme", _theme_target(objective, getattr(action, "why_it_matters", ""), ticker)

    if kind == "consensus_revision_capture":
        dated = _date_from_text(objective)
        return ("event", dated) if dated else ("company", "company")

    if kind in _COMPANY_LEVEL_KINDS:
        return "company", "company"

    scoped_ids = _unique(
        (
            *list(getattr(action, "related_claim_ids", ()) or ()),
            *list(getattr(action, "related_finding_ids", ()) or ()),
        )
    )
    if scoped_ids:
        return "claim_scope", _stable_digest("|".join(sorted(scoped_ids)), 16)

    return "topic", _objective_fingerprint(objective)


def _canonical_kind(
    action: PrioritizedResearchAction,
    declared_kind: str,
) -> str:
    """Let a strong canonical intent repair stale legacy display metadata."""

    intent = " ".join((
        str(getattr(action, "intent_id", "") or ""),
        str(getattr(action, "mechanism_key", "") or ""),
        _action_display(action),
    )).casefold().replace("-", " ")
    cash_components = sum(
        token in intent
        for token in (
            "working capital",
            "capex",
            "cash taxes",
            "financing",
            "capital returns",
            "repurchases",
            "dividends",
        )
    )
    if (
        "recurring_cash_conversion" in intent
        or "recurring cash conversion" in intent
        or "cash conversion" in intent and cash_components >= 1
    ):
        return "cash_conversion_analysis"
    return declared_kind


def _mapped_target(
    intent_mapping: Mapping[str, str | Mapping[str, Any]],
    action_id: str,
    semantic_key: str,
) -> tuple[str, str] | None:
    raw = intent_mapping.get(action_id) or intent_mapping.get(semantic_key)
    if not raw:
        return None
    if isinstance(raw, str):
        return "intent", _normalized_identity_value(raw)
    for field_name, target_type in (
        ("event_id", "event"),
        ("claim_id", "claim_scope"),
        ("valuation_input", "valuation_input"),
        ("theme", "theme"),
        ("target", "intent"),
    ):
        value = raw.get(field_name)
        if value:
            return target_type, _normalized_identity_value(str(value))
    return None


def _canonical_semantic_key(
    ticker: str,
    kind: str,
    target_type: str,
    target: str,
    *,
    intent_id: str = "",
) -> str:
    if intent_id:
        return (
            f"{CANONICAL_ACTION_IDENTITY_VERSION}:intent:{ticker}:"
            f"{_normalized_identity_value(intent_id)}"
        )
    target_digest = _stable_digest(f"{target_type}:{target}", 16)
    return (
        f"{CANONICAL_ACTION_IDENTITY_VERSION}:{kind}:{ticker}:"
        f"{target_type}-{target_digest}"
    )


def _canonical_action_id(semantic_key: str) -> str:
    return "action-" + _stable_digest(semantic_key, 20)


def _merge_action(
    existing: PrioritizedResearchAction,
    incoming: PrioritizedResearchAction,
) -> None:
    if _display_quality(incoming, incoming.objective) > _display_quality(
        existing, existing.objective
    ):
        existing.objective = incoming.objective
    if _display_quality(incoming, incoming.title) > _display_quality(
        existing, existing.title
    ):
        existing.title = incoming.title
    if _text_quality(incoming.why_it_matters) > _text_quality(
        existing.why_it_matters
    ):
        existing.why_it_matters = incoming.why_it_matters
    if _MATERIALITY_RANK.get(incoming.materiality.casefold(), 0) > _MATERIALITY_RANK.get(
        existing.materiality.casefold(), 0
    ):
        existing.materiality = incoming.materiality
    if _BLOCKER_RANK.get(incoming.blocks_stage.casefold(), 0) > _BLOCKER_RANK.get(
        existing.blocks_stage.casefold(), 0
    ):
        existing.blocks_stage = incoming.blocks_stage
    if _status_rank(incoming.status) > _status_rank(existing.status):
        existing.status = incoming.status
        if incoming.blocking_reason:
            existing.blocking_reason = incoming.blocking_reason
    existing.auto_resolvable = existing.auto_resolvable or incoming.auto_resolvable
    if existing.auto_resolvable:
        existing.execution_mode = "automatic"
    for field_name in (
        "intent_id",
        "mechanism_key",
        "target_type",
        "target_id",
        "period_scope",
        "decision_dependency",
        "answer_status",
    ):
        if not str(getattr(existing, field_name, "") or ""):
            setattr(existing, field_name, getattr(incoming, field_name, ""))

    for field_name in (
        "related_idea_ids",
        "related_work_order_ids",
        "related_research_request_ids",
        "related_claim_ids",
        "related_finding_ids",
        "required_source_families",
        "preferred_connectors",
        "completion_criteria",
        "origin_labels",
        "remaining_work",
    ):
        setattr(
            existing,
            field_name,
            _unique(
                (
                    *list(getattr(existing, field_name, ()) or ()),
                    *list(getattr(incoming, field_name, ()) or ()),
                )
            ),
        )

    if _attempt_quality(incoming.attempt_summary) > _attempt_quality(
        existing.attempt_summary
    ):
        existing.attempt_summary = incoming.attempt_summary
    if _text_quality(incoming.resolution_summary) > _text_quality(
        existing.resolution_summary
    ):
        existing.resolution_summary = incoming.resolution_summary


def _group_sort_key(group: _ActionGroup) -> tuple[int, int, int, int, str]:
    action = group.action
    return (
        -_MATERIALITY_RANK.get(action.materiality.casefold(), 0),
        -_BLOCKER_RANK.get(action.blocks_stage.casefold(), 0),
        -_status_rank(action.status),
        group.first_position,
        action.semantic_key,
    )


def _display_collisions(
    visible_groups: list[_ActionGroup],
    aliases_by_canonical: Mapping[str, CanonicalActionAliases],
) -> tuple[InvestigationActionDisplayCollision, ...]:
    buckets: dict[str, list[_ActionGroup]] = {}
    for group in visible_groups:
        display = _normalize_display(_action_display(group.action))
        if display:
            buckets.setdefault(display, []).append(group)
    collisions = []
    for normalized, groups in buckets.items():
        if len(groups) < 2:
            continue
        canonical_ids = tuple(group.action.action_id for group in groups)
        original_ids = _unique(
            action_id
            for canonical_id in canonical_ids
            for action_id in aliases_by_canonical[canonical_id].action_ids
            if action_id != canonical_id
        )
        collisions.append(InvestigationActionDisplayCollision(
            normalized_display=normalized,
            display_text=_action_display(groups[0].action),
            canonical_action_ids=canonical_ids,
            original_action_ids=tuple(original_ids),
        ))
    return tuple(collisions)


def _normalize_source_families(values: Iterable[Any]) -> list[str]:
    normalized = []
    for value in values:
        token = _normalize_token(str(value or ""))
        if not token:
            continue
        canonical = _SOURCE_FAMILY_ALIASES.get(token, token)
        if canonical not in normalized:
            normalized.append(canonical)
    return sorted(normalized)


def _valuation_input_target(objective: str, ticker: str) -> str:
    value = _normalize_display(objective)
    value = re.sub(r"^(resolve|retrieve|validate|find)\s+", "", value)
    value = re.sub(rf"\b{re.escape(ticker.casefold())}\b", "", value)
    value = re.sub(r"\b(for|in|through)\s+(the\s+)?valuation( bridge| model)?\b", "", value)
    return _normalized_identity_value(value) or _objective_fingerprint(objective)


def _theme_target(objective: str, why_it_matters: str, ticker: str) -> str:
    combined = f"{objective} {why_it_matters}"
    labeled = re.search(
        r"(?:wisburg\s+)?theme\s*:\s*([^.;]+)", combined, flags=re.I
    )
    objective_theme = re.search(
        rf"\b{re.escape(ticker)}\s+(.+?)\s+theme\b",
        objective,
        flags=re.I,
    )
    if labeled:
        value = labeled.group(1)
    elif objective_theme:
        value = objective_theme.group(1)
    else:
        value = objective
    value = re.sub(r"\bwith issuer evidence\b", "", value, flags=re.I)
    value = re.sub(r"\b(validate|corroborate|the|theme)\b", " ", value, flags=re.I)
    value = re.sub(rf"\b{re.escape(ticker)}\b", " ", value, flags=re.I)
    normalized = _normalized_identity_value(value)
    if normalized in {"", "external", "external_research", "general_external_research"}:
        return "external_research"
    return normalized


def _date_from_text(value: str) -> str:
    match = re.search(r"\b(20\d{2})[-/](0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])\b", value)
    return "-".join(match.groups()) if match else ""


def _objective_fingerprint(value: str) -> str:
    return _stable_digest(_normalize_display(value), 16)


def _action_display(action: PrioritizedResearchAction) -> str:
    return str(
        getattr(action, "objective", "")
        or getattr(action, "title", "")
        or "Investigation"
    ).strip()


def _normalize_display(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def _normalized_identity_value(value: str) -> str:
    return _normalize_token(value) or "unspecified"


def _normalize_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").casefold()).strip("_")


def _display_quality(action: PrioritizedResearchAction, value: str) -> tuple[int, int, int]:
    text = str(value or "").strip()
    ticker = re.search(r"\b[A-Z]{1,5}\b", text)
    generic = text.casefold() in {"", "investigation", "complete investigation"}
    return (int(bool(ticker)), int(not generic), len(text))


def _text_quality(value: str) -> int:
    return len(str(value or "").strip())


def _attempt_quality(value: str) -> tuple[int, int]:
    text = str(value or "").strip()
    return (int(bool(text) and text.casefold() != "not attempted"), len(text))


def _status_rank(value: str) -> int:
    return _STATUS_RANK.get(_normalize_token(value), 1)


def _is_resolved(value: str) -> bool:
    return _normalize_token(value) in _RESOLVED_STATUSES


def _stable_digest(value: str, length: int) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:length]


def _unique(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if item and item not in result:
            result.append(item)
    return result
