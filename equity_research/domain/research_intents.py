from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Iterable, Mapping, Optional, Sequence, Tuple


RESEARCH_INTENT_SCHEMA_VERSION = "research-intent-v1"


class ResearchIntentQuestionStatus(str, Enum):
    PLANNED = "planned"
    RESEARCHING = "researching"
    ANSWERED = "answered"
    PARTIALLY_ANSWERED = "partially_answered"
    BLOCKED_BY_SOURCE_ACCESS = "blocked_by_source_access"
    BLOCKED_BY_PROVIDER_FAILURE = "blocked_by_provider_failure"
    INVALIDATED_BY_NEW_EVIDENCE = "invalidated_by_new_evidence"
    CANCELLED = "cancelled"


class ResearchIntentAnswerStatus(str, Enum):
    PLANNED = "planned"
    RESEARCHING = "researching"
    ANSWERED = "answered"
    PARTIALLY_ANSWERED = "partially_answered"
    BLOCKED_BY_SOURCE_ACCESS = "blocked_by_source_access"
    BLOCKED_BY_PROVIDER_FAILURE = "blocked_by_provider_failure"
    INVALIDATED_BY_NEW_EVIDENCE = "invalidated_by_new_evidence"
    CANCELLED = "cancelled"


class LegacyIntentMappingStatus(str, Enum):
    EXACT = "exact"
    ALIAS = "alias"
    INFERRED = "inferred"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class ResearchIntentDiagnostic:
    code: str
    message: str
    severity: str = "info"


@dataclass(frozen=True)
class ResearchIntent:
    """One decision-relevant question, independent of its execution details.

    Provider, connector, source family, display wording, and workflow status are
    intentionally absent from the identity payload. They can change without
    creating a second user-facing investigation.
    """

    security_key: str
    question_key: str
    mechanism_key: str
    target_type: str
    target_id: str
    period_scope: str
    event_id: str
    decision_dependency: str
    title: str = ""
    objective: str = ""
    question_status: str = ResearchIntentQuestionStatus.PLANNED.value
    answer_status: str = ResearchIntentAnswerStatus.PLANNED.value
    remaining_work: Tuple[str, ...] = ()
    aliases: Tuple[str, ...] = ()
    origin_refs: Tuple[str, ...] = ()
    materiality: str = "Medium"
    schema_version: str = RESEARCH_INTENT_SCHEMA_VERSION
    intent_id: str = field(init=False)

    def __post_init__(self) -> None:
        identity = ResearchIntentIdentity(
            security_key=self.security_key,
            question_key=self.question_key,
            mechanism_key=self.mechanism_key,
            target_type=self.target_type,
            target_id=self.target_id,
            period_scope=self.period_scope,
            event_id=self.event_id,
            decision_dependency=self.decision_dependency,
        )
        for name, value in identity.as_dict().items():
            object.__setattr__(self, name, value)
        ResearchIntentQuestionStatus(self.question_status)
        ResearchIntentAnswerStatus(self.answer_status)
        object.__setattr__(self, "remaining_work", _dedupe_text(self.remaining_work))
        object.__setattr__(self, "aliases", _dedupe_text(self.aliases))
        object.__setattr__(self, "origin_refs", _dedupe_text(self.origin_refs))
        object.__setattr__(self, "intent_id", identity.intent_id)


@dataclass(frozen=True)
class ResearchIntentIdentity:
    security_key: str
    question_key: str
    mechanism_key: str
    target_type: str
    target_id: str
    period_scope: str
    event_id: str
    decision_dependency: str

    def __post_init__(self) -> None:
        values = {
            "security_key": _normalize_security_key(self.security_key),
            "question_key": _slug(self.question_key) or "unclassified_question",
            "mechanism_key": _slug(self.mechanism_key) or "general_mechanism",
            "target_type": _normalize_target_type(self.target_type),
            "target_id": _normalize_target(self.target_id),
            "period_scope": _normalize_scope(self.period_scope) or "current_research_cutoff",
            "event_id": _normalize_target(self.event_id),
            "decision_dependency": _slug(self.decision_dependency) or "decision_quality",
        }
        for name, value in values.items():
            object.__setattr__(self, name, value)

    @property
    def intent_id(self) -> str:
        payload = json.dumps(
            self.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
        return "intent-" + digest

    def as_dict(self) -> Mapping[str, str]:
        return {
            "security_key": self.security_key,
            "question_key": self.question_key,
            "mechanism_key": self.mechanism_key,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "period_scope": self.period_scope,
            "event_id": self.event_id,
            "decision_dependency": self.decision_dependency,
        }


@dataclass(frozen=True)
class LegacyResearchIntentMapping:
    status: str
    intent: Optional[ResearchIntent]
    matched_alias: str = ""
    diagnostics: Tuple[ResearchIntentDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        LegacyIntentMappingStatus(self.status)


@dataclass(frozen=True)
class ResearchIntentUpsertResult:
    intent: ResearchIntent
    inserted: bool
    diagnostics: Tuple[ResearchIntentDiagnostic, ...] = ()


@dataclass(frozen=True)
class _AliasTarget:
    question_key: str
    mechanism_key: str
    decision_dependency: str


_QUESTION_DEFAULTS = {
    "business_operating_model": _AliasTarget(
        "business_operating_model", "business_model_durability", "thesis_quality"
    ),
    "financial_statement_integrity": _AliasTarget(
        "financial_statement_integrity", "financial_fact_reconciliation", "evidence_integrity"
    ),
    "operating_drivers": _AliasTarget(
        "operating_drivers", "operating_driver_durability", "thesis_quality"
    ),
    "cash_generation_capital_allocation": _AliasTarget(
        "cash_generation_capital_allocation", "recurring_cash_conversion", "thesis_quality"
    ),
    "balance_sheet_credit": _AliasTarget(
        "balance_sheet_credit", "liquidity_downside_resilience", "risk"
    ),
    "reverse_valuation_expectations": _AliasTarget(
        "reverse_valuation_expectations", "market_implied_operating_outcome", "valuation"
    ),
    "market_priced_in": _AliasTarget(
        "market_priced_in", "point_in_time_expectations", "priced_in"
    ),
    "valuation_expected_return": _AliasTarget(
        "valuation_expected_return", "scenario_payoff", "valuation"
    ),
    "catalysts_why_now": _AliasTarget(
        "catalysts_why_now", "catalyst_transmission", "timing"
    ),
    "counter_thesis_risks": _AliasTarget(
        "counter_thesis_risks", "thesis_falsification", "risk"
    ),
    "historical_analogs_playbooks": _AliasTarget(
        "historical_analogs_playbooks", "historical_transferability", "thesis_quality"
    ),
    "monitoring_investigation": _AliasTarget(
        "monitoring_investigation", "highest_information_gain", "monitoring"
    ),
}


_ALIASES = {
    "cash_conversion_analysis": _QUESTION_DEFAULTS["cash_generation_capital_allocation"],
    "cash_generation": _QUESTION_DEFAULTS["cash_generation_capital_allocation"],
    "cash_generation_liquidity": _QUESTION_DEFAULTS["cash_generation_capital_allocation"],
    "operating_driver_analysis": _QUESTION_DEFAULTS["operating_drivers"],
    "consensus_revision_capture": _QUESTION_DEFAULTS["market_priced_in"],
    "market_event_attribution": _AliasTarget(
        "market_priced_in", "event_reaction_attribution", "priced_in"
    ),
    "valuation_scenarios": _QUESTION_DEFAULTS["valuation_expected_return"],
    "valuation_input_resolution": _QUESTION_DEFAULTS["reverse_valuation_expectations"],
    "primary_source_validation": _QUESTION_DEFAULTS["financial_statement_integrity"],
    "historical_analog_investigation": _QUESTION_DEFAULTS["historical_analogs_playbooks"],
    **_QUESTION_DEFAULTS,
}


_TEXT_RULES = (
    (
        ("repurchase", "per share"),
        _AliasTarget(
            "cash_generation_capital_allocation",
            "per_share_capital_allocation",
            "capital_allocation",
        ),
    ),
    (
        ("cash conversion",),
        _QUESTION_DEFAULTS["cash_generation_capital_allocation"],
    ),
    (
        ("working capital", "capex"),
        _QUESTION_DEFAULTS["cash_generation_capital_allocation"],
    ),
    (
        ("reverse dcf",),
        _QUESTION_DEFAULTS["reverse_valuation_expectations"],
    ),
    (
        ("market implied",),
        _QUESTION_DEFAULTS["reverse_valuation_expectations"],
    ),
    (
        ("priced in",),
        _QUESTION_DEFAULTS["market_priced_in"],
    ),
    (
        ("estimate revision",),
        _QUESTION_DEFAULTS["market_priced_in"],
    ),
    (
        ("event reaction",),
        _ALIASES["market_event_attribution"],
    ),
    (
        ("historical analog",),
        _QUESTION_DEFAULTS["historical_analogs_playbooks"],
    ),
    (
        ("counter thesis",),
        _QUESTION_DEFAULTS["counter_thesis_risks"],
    ),
    (
        ("bull", "bear", "valuation"),
        _QUESTION_DEFAULTS["valuation_expected_return"],
    ),
    (
        ("pricing", "volume", "mix"),
        _QUESTION_DEFAULTS["operating_drivers"],
    ),
)


class ResearchIntentResolver:
    """Resolve legacy records and deterministically upsert canonical intents."""

    def __init__(self) -> None:
        self._intents = {}  # type: dict[str, ResearchIntent]

    def resolve_legacy(
        self,
        *,
        ticker: str = "",
        cik: str = "",
        exchange: str = "",
        question_key: str = "",
        action_kind: str = "",
        mechanism_key: str = "",
        target_type: str = "",
        target_id: str = "",
        period_scope: str = "",
        event_id: str = "",
        decision_dependency: str = "",
        title: str = "",
        objective: str = "",
        question_status: str = "planned",
        answer_status: str = "planned",
        remaining_work: Sequence[str] = (),
        aliases: Sequence[str] = (),
        origin_refs: Sequence[str] = (),
        materiality: str = "Medium",
        legacy_provider: str = "",
        legacy_source_families: Sequence[str] = (),
    ) -> LegacyResearchIntentMapping:
        del legacy_provider, legacy_source_families
        diagnostics = []  # type: list[ResearchIntentDiagnostic]
        security_key = _security_key(cik=cik, exchange=exchange, ticker=ticker)
        if not security_key:
            return LegacyResearchIntentMapping(
                status=LegacyIntentMappingStatus.UNRESOLVED.value,
                intent=None,
                diagnostics=(ResearchIntentDiagnostic(
                    code="missing_security_identity",
                    message="A CIK or exchange/ticker identity is required.",
                    severity="error",
                ),),
            )
        if security_key.startswith("ticker:"):
            diagnostics.append(ResearchIntentDiagnostic(
                code="ticker_only_security_identity",
                message="The legacy record has no CIK or exchange; ticker-only identity was retained.",
                severity="warning",
            ))

        target = None  # type: Optional[_AliasTarget]
        mapping_status = LegacyIntentMappingStatus.UNRESOLVED
        matched_alias = ""
        explicit_key = _slug(question_key)
        action_alias = _slug(action_kind)
        if explicit_key:
            target = _ALIASES.get(explicit_key)
            if target is None:
                target = _AliasTarget(
                    explicit_key,
                    _slug(mechanism_key) or "general_mechanism",
                    _slug(decision_dependency) or "decision_quality",
                )
                mapping_status = LegacyIntentMappingStatus.EXACT
            else:
                mapping_status = (
                    LegacyIntentMappingStatus.EXACT
                    if explicit_key == target.question_key
                    else LegacyIntentMappingStatus.ALIAS
                )
                matched_alias = explicit_key
        elif action_alias and action_alias in _ALIASES:
            target = _ALIASES[action_alias]
            mapping_status = LegacyIntentMappingStatus.ALIAS
            matched_alias = action_alias
        else:
            target, matched_alias = _infer_text_target(" ".join((title, objective)))
            if target is not None:
                mapping_status = LegacyIntentMappingStatus.INFERRED

        if target is None:
            diagnostics.append(ResearchIntentDiagnostic(
                code="unmapped_legacy_intent",
                message=(
                    "The legacy wording could not be mapped safely. Supply a canonical "
                    "question_key rather than creating a wording-derived identity."
                ),
                severity="warning",
            ))
            return LegacyResearchIntentMapping(
                status=LegacyIntentMappingStatus.UNRESOLVED.value,
                intent=None,
                diagnostics=tuple(diagnostics),
            )

        resolved_mechanism = _slug(mechanism_key) or target.mechanism_key
        resolved_dependency = _slug(decision_dependency) or target.decision_dependency
        resolved_target_type = target_type or ("event" if event_id else "issuer")
        resolved_target_id = target_id or (event_id if event_id else security_key)
        all_aliases = tuple(value for value in (
            *aliases, question_key, action_kind, matched_alias
        ) if value)
        intent = ResearchIntent(
            security_key=security_key,
            question_key=target.question_key,
            mechanism_key=resolved_mechanism,
            target_type=resolved_target_type,
            target_id=resolved_target_id,
            period_scope=period_scope or "current_research_cutoff",
            event_id=event_id,
            decision_dependency=resolved_dependency,
            title=title,
            objective=objective or title,
            question_status=_coerce_question_status(question_status),
            answer_status=_coerce_answer_status(answer_status),
            remaining_work=tuple(remaining_work),
            aliases=all_aliases,
            origin_refs=tuple(origin_refs),
            materiality=materiality,
        )
        if mapping_status in {LegacyIntentMappingStatus.ALIAS, LegacyIntentMappingStatus.INFERRED}:
            diagnostics.append(ResearchIntentDiagnostic(
                code="legacy_alias_mapped",
                message=f"Mapped legacy input to {target.question_key}/{resolved_mechanism}.",
            ))
        return LegacyResearchIntentMapping(
            status=mapping_status.value,
            intent=intent,
            matched_alias=matched_alias,
            diagnostics=tuple(diagnostics),
        )

    def upsert(self, intent: ResearchIntent) -> ResearchIntentUpsertResult:
        existing = self._intents.get(intent.intent_id)
        if existing is None:
            self._intents[intent.intent_id] = intent
            return ResearchIntentUpsertResult(intent=intent, inserted=True)
        merged = merge_research_intents(existing, intent)
        self._intents[intent.intent_id] = merged
        return ResearchIntentUpsertResult(
            intent=merged,
            inserted=False,
            diagnostics=(ResearchIntentDiagnostic(
                code="canonical_intent_merged",
                message="Merged another origin into the existing canonical research intent.",
            ),),
        )

    def all(self) -> Tuple[ResearchIntent, ...]:
        return tuple(self._intents[key] for key in sorted(self._intents))


def merge_research_intents(left: ResearchIntent, right: ResearchIntent) -> ResearchIntent:
    if left.intent_id != right.intent_id:
        raise ValueError("Only research intents with the same canonical identity may merge.")
    remaining_work = _dedupe_text((*left.remaining_work, *right.remaining_work))
    answer_status = _merge_answer_status(
        left.answer_status, right.answer_status, bool(remaining_work)
    )
    question_status = _merge_question_status(
        left.question_status, right.question_status, answer_status
    )
    return replace(
        left,
        title=_preferred_display_value(left.title, right.title),
        objective=_preferred_display_value(left.objective, right.objective),
        question_status=question_status,
        answer_status=answer_status,
        remaining_work=remaining_work,
        aliases=_dedupe_text((*left.aliases, *right.aliases)),
        origin_refs=_dedupe_text((*left.origin_refs, *right.origin_refs)),
        materiality=_stronger_materiality(left.materiality, right.materiality),
    )


def _security_key(*, cik: str, exchange: str, ticker: str) -> str:
    digits = re.sub(r"\D", "", str(cik or ""))
    if digits:
        return "cik:" + digits.zfill(10)
    normalized_ticker = _slug(ticker).upper()
    normalized_exchange = _slug(exchange).upper()
    if normalized_exchange and normalized_ticker:
        return f"listing:{normalized_exchange}:{normalized_ticker}"
    if normalized_ticker:
        return "ticker:" + normalized_ticker
    return ""


def _normalize_security_key(value: str) -> str:
    raw = str(value or "").strip()
    if raw.startswith("cik:"):
        digits = re.sub(r"\D", "", raw[4:])
        return "cik:" + digits.zfill(10)
    if raw.startswith("listing:"):
        parts = raw.split(":", 2)
        if len(parts) == 3:
            return f"listing:{_slug(parts[1]).upper()}:{_slug(parts[2]).upper()}"
    if raw.startswith("ticker:"):
        return "ticker:" + _slug(raw[7:]).upper()
    return raw.casefold()


def _infer_text_target(value: str) -> Tuple[Optional[_AliasTarget], str]:
    normalized = " ".join(str(value or "").casefold().replace("-", " ").split())
    matches = []
    for tokens, target in _TEXT_RULES:
        if all(token in normalized for token in tokens):
            matches.append((len(tokens), sum(len(token) for token in tokens), tokens, target))
    if not matches:
        return None, ""
    matches.sort(key=lambda item: (-item[0], -item[1], item[3].question_key, item[3].mechanism_key))
    _, _, tokens, target = matches[0]
    return target, " + ".join(tokens)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").casefold()).strip("_")


def _normalize_target(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _normalize_target_type(value: str) -> str:
    normalized = _slug(value) or "issuer"
    return {
        "company": "issuer",
        "security": "issuer",
        "stock": "issuer",
    }.get(normalized, normalized)


def _normalize_scope(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _dedupe_text(values: Iterable[str]) -> Tuple[str, ...]:
    by_key = {}
    for value in values:
        clean = " ".join(str(value or "").split())
        if clean:
            by_key.setdefault(clean.casefold(), clean)
    return tuple(by_key[key] for key in sorted(by_key))


def _preferred_display_value(left: str, right: str) -> str:
    values = [value.strip() for value in (left, right) if value and value.strip()]
    if not values:
        return ""
    return sorted(values, key=lambda value: (
        -_display_specificity(value)[0],
        -_display_specificity(value)[1],
        value.casefold(),
    ))[0]


def _display_specificity(value: str) -> Tuple[int, int]:
    company_specific = int(bool(re.search(r"\b[A-Z]{1,5}\b", value)))
    return company_specific, min(len(value), 500)


def _coerce_question_status(value: str) -> str:
    normalized = _slug(value)
    aliases = {
        "open": "planned",
        "pending": "planned",
        "attempted": "researching",
        "attempted_unresolved": "partially_answered",
        "unavailable": "blocked_by_source_access",
        "failed": "blocked_by_provider_failure",
    }
    normalized = aliases.get(normalized, normalized or "planned")
    return ResearchIntentQuestionStatus(normalized).value


def _coerce_answer_status(value: str) -> str:
    normalized = _slug(value)
    aliases = {
        "open": "planned",
        "pending": "planned",
        "attempted": "researching",
        "attempted_unresolved": "partially_answered",
        "unanswered_after_attempt": "partially_answered",
        "unavailable": "blocked_by_source_access",
        "failed": "blocked_by_provider_failure",
    }
    normalized = aliases.get(normalized, normalized or "planned")
    return ResearchIntentAnswerStatus(normalized).value


def _merge_answer_status(left: str, right: str, has_remaining_work: bool) -> str:
    statuses = {ResearchIntentAnswerStatus(left), ResearchIntentAnswerStatus(right)}
    if ResearchIntentAnswerStatus.INVALIDATED_BY_NEW_EVIDENCE in statuses:
        return ResearchIntentAnswerStatus.INVALIDATED_BY_NEW_EVIDENCE.value
    if has_remaining_work and ResearchIntentAnswerStatus.ANSWERED in statuses:
        return ResearchIntentAnswerStatus.PARTIALLY_ANSWERED.value
    order = (
        ResearchIntentAnswerStatus.ANSWERED,
        ResearchIntentAnswerStatus.PARTIALLY_ANSWERED,
        ResearchIntentAnswerStatus.BLOCKED_BY_SOURCE_ACCESS,
        ResearchIntentAnswerStatus.BLOCKED_BY_PROVIDER_FAILURE,
        ResearchIntentAnswerStatus.INVALIDATED_BY_NEW_EVIDENCE,
        ResearchIntentAnswerStatus.RESEARCHING,
        ResearchIntentAnswerStatus.PLANNED,
        ResearchIntentAnswerStatus.CANCELLED,
    )
    return next(status.value for status in order if status in statuses)


def _merge_question_status(left: str, right: str, answer_status: str) -> str:
    answer = ResearchIntentAnswerStatus(answer_status)
    if answer in {
        ResearchIntentAnswerStatus.ANSWERED,
        ResearchIntentAnswerStatus.PARTIALLY_ANSWERED,
        ResearchIntentAnswerStatus.BLOCKED_BY_SOURCE_ACCESS,
        ResearchIntentAnswerStatus.BLOCKED_BY_PROVIDER_FAILURE,
        ResearchIntentAnswerStatus.INVALIDATED_BY_NEW_EVIDENCE,
    }:
        return ResearchIntentQuestionStatus(answer.value).value
    statuses = {ResearchIntentQuestionStatus(left), ResearchIntentQuestionStatus(right)}
    for status in (
        ResearchIntentQuestionStatus.RESEARCHING,
        ResearchIntentQuestionStatus.PLANNED,
        ResearchIntentQuestionStatus.CANCELLED,
    ):
        if status in statuses:
            return status.value
    return ResearchIntentQuestionStatus.PLANNED.value


def _stronger_materiality(left: str, right: str) -> str:
    order = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    return sorted(
        (left or "Medium", right or "Medium"),
        key=lambda value: (-order.get(value.casefold(), 0), value.casefold()),
    )[0]
