from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from . import config
from .models import BudgetPolicy, ConsensusPackage, ExternalEvidenceBundle


BUDGET_MODES = ("Free", "Lean", "Stable", "Premium")


DEFAULT_MODE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "Free": {
        "cost_target": "$0/month plus optional user-owned free keys",
        "description": "Use official/keyless sources and manual imports; do not require paid data or LLM calls.",
        "max_monthly_budget_usd": 0,
        "allow_llm": False,
        "allow_paid_data": False,
        "primary_llm_profile": "",
        "secondary_llm_profile": "",
        "secondary_llm_enabled": False,
        "provider_policy": {
            "company_evidence": ["SEC EDGAR", "issuer filings", "issuer IR/manual imports"],
            "consensus": ["Alpha Vantage if keyed", "Finnhub recommendations if keyed", "CSV/manual"],
            "prices": ["EODHD free EOD if keyed", "Stooq", "Yahoo price fallback", "CSV/manual"],
            "macro": ["BLS no-key", "Treasury/Fiscal Data", "configured official macro keys"],
            "llm": [],
        },
        "disabled_sources": [
            "Paid consensus/fundamental providers unless explicitly injected by a caller",
            "LLM synthesis by default",
            "Paid price/options feeds",
        ],
        "llm_policy": "Disabled by default; deterministic IC brief remains available.",
    },
    "Lean": {
        "cost_target": "$5-$30/month expected LLM usage",
        "description": "Use the free evidence stack plus one low-cost LLM for synthesis and critique.",
        "max_monthly_budget_usd": 30,
        "allow_llm": True,
        "allow_paid_data": False,
        "primary_llm_profile": "deepseek_or_low_cost_openai_compatible",
        "secondary_llm_profile": "optional_openai_mini_or_anthropic_haiku",
        "secondary_llm_enabled": True,
        "provider_policy": {
            "company_evidence": ["SEC EDGAR", "issuer filings", "issuer IR/manual imports"],
            "consensus": ["Alpha Vantage if keyed", "Finnhub recommendations if keyed", "CSV/manual"],
            "prices": ["EODHD free EOD if keyed", "Stooq", "Yahoo price fallback", "CSV/manual"],
            "macro": ["BLS no-key", "Treasury/Fiscal Data", "configured FRED/BEA/Census"],
            "llm": ["primary low-cost synthesis", "secondary review for Research-Ready+"],
        },
        "llm_policy": (
            "Primary low-cost LLM can synthesize only curated excerpts; secondary review is optional "
            "and should run only on Research-Ready+ ideas."
        ),
    },
    "Stable": {
        "cost_target": "$30-$80/month if one low-cost market-data provider is configured",
        "description": "Lean stack plus one paid/low-cost price or fundamentals provider when configured.",
        "max_monthly_budget_usd": 80,
        "allow_llm": True,
        "allow_paid_data": True,
        "primary_llm_profile": "deepseek_or_openai_mini",
        "secondary_llm_profile": "optional_openai_mini_or_anthropic_haiku",
        "secondary_llm_enabled": True,
        "provider_policy": {
            "company_evidence": ["SEC EDGAR", "issuer filings", "issuer IR/manual imports"],
            "consensus": ["FMP/Tiingo/Finnhub/Alpha Vantage if configured", "CSV/manual"],
            "prices": ["Tiingo/EODHD/Polygon/FMP if configured", "Stooq", "Yahoo price fallback", "CSV/manual"],
            "macro": ["official macro default policy", "configured global macro for ADR/FPI"],
            "llm": ["primary synthesis", "secondary review for top thesis clusters"],
        },
        "llm_policy": "Low-cost primary LLM; stronger secondary read only for top thesis clusters.",
    },
    "Premium": {
        "cost_target": "$180+/month depending on enabled premium providers",
        "description": "Adapter-ready premium posture for richer consensus, estimates, prices, options, and transcripts.",
        "max_monthly_budget_usd": None,
        "allow_llm": True,
        "allow_paid_data": True,
        "primary_llm_profile": "strongest_configured_profile",
        "secondary_llm_profile": "independent_secondary_reader",
        "secondary_llm_enabled": True,
        "provider_policy": {
            "company_evidence": ["SEC EDGAR", "issuer filings", "issuer IR/manual imports", "licensed report excerpts"],
            "consensus": ["FMP/Intrinio/Visible Alpha/FactSet/LSEG if configured", "CSV/manual"],
            "prices": ["Polygon/Tiingo/EODHD/institutional market data if configured", "fallback free providers"],
            "macro": ["official macro default policy", "global macro", "factor/positioning data"],
            "llm": ["strong primary synthesis", "independent secondary read", "bilingual audit"],
        },
        "llm_policy": "Allow stronger primary/secondary readers, still constrained to curated evidence packs.",
    },
}


def normalize_budget_mode(value: str | None) -> str:
    normalized = (value or config.BUDGET_MODE or "Lean").strip().lower()
    definitions = load_budget_mode_definitions()
    for mode in definitions:
        if normalized == mode.lower():
            return mode
    aliases = {
        "free": "Free",
        "0": "Free",
        "lean": "Lean",
        "low": "Lean",
        "stable": "Stable",
        "paid": "Stable",
        "premium": "Premium",
        "institutional": "Premium",
    }
    return aliases.get(normalized, "Lean")


def budget_allows_paid_data(mode: str | None) -> bool:
    selected = normalize_budget_mode(mode)
    return _optional_bool(
        load_budget_mode_definitions().get(selected, {}).get("allow_paid_data"),
        selected in {"Stable", "Premium"},
    )


def available_budget_modes() -> tuple[str, ...]:
    definitions = load_budget_mode_definitions()
    ordered = [mode for mode in BUDGET_MODES if mode in definitions]
    ordered.extend(mode for mode in definitions if mode not in ordered)
    return tuple(ordered)


def load_budget_mode_definitions(path: Path | None = None) -> dict[str, dict[str, Any]]:
    definitions = deepcopy(DEFAULT_MODE_DEFINITIONS)
    local_path = path or getattr(config, "BUDGET_POLICY_PATH", Path("data/budget_modes.json"))
    if not local_path:
        return definitions
    try:
        local_path = Path(local_path)
    except TypeError:
        return definitions
    if not local_path.exists():
        return definitions
    try:
        payload = json.loads(local_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return definitions
    mode_payload = payload.get("modes", payload) if isinstance(payload, dict) else {}
    if not isinstance(mode_payload, dict):
        return definitions
    for mode, overrides in mode_payload.items():
        if not isinstance(overrides, dict):
            continue
        canonical = str(mode).strip()
        if not canonical:
            continue
        base = deepcopy(definitions.get(canonical, {}))
        base.update(overrides)
        base["_config_source"] = str(local_path)
        definitions[canonical] = base
    return definitions


def build_budget_policy(
    mode: str | None,
    consensus: ConsensusPackage | None = None,
    external_evidence: ExternalEvidenceBundle | None = None,
    *,
    llm_enabled: bool = False,
) -> BudgetPolicy:
    selected = normalize_budget_mode(mode)
    definitions = load_budget_mode_definitions()
    definition = definitions.get(selected, definitions["Lean"])
    enabled = [
        "SEC EDGAR submissions, filings, and XBRL company facts",
        "Issuer-filed exhibits, proxies, 20-F/6-K, and management-source artifacts",
        "CSV/manual import fallbacks for open-source or personal datasets",
    ]
    disabled: list[str] = list(definition.get("disabled_sources", []))
    warnings: list[str] = []
    upgrades = list(definition.get("optional_upgrade_slots") or [
        "FMP or Intrinio for richer consensus, estimates, fundamentals, and transcripts",
        "Tiingo or Polygon/Massive for cleaner adjusted prices, corporate actions, and options",
        "Visible Alpha, FactSet, LSEG, or Capital IQ for institutional consensus and segment models",
    ])
    provider_policy = _provider_policy(definition)
    for group, sources in provider_policy.items():
        if sources:
            enabled.append(f"{group.replace('_', ' ').title()}: {', '.join(sources)}")

    if external_evidence and external_evidence.provider_statuses:
        enabled.append("Official macro/context providers selected by default policy")
    else:
        enabled.append("Official macro/context provider slots; missing keys or disabled sources are shown as gaps")

    if consensus:
        if consensus.status == "Available":
            enabled.append(f"Consensus stack: {consensus.provider}")
        elif consensus.status.startswith("Partial"):
            enabled.append(f"Partial consensus stack: {consensus.provider}")
            warnings.append("Consensus is partial or unofficial and cannot independently support High-Conviction.")
        else:
            warnings.append("Consensus unavailable; ideas can still be generated from SEC/issuer evidence.")

    if selected == "Free":
        cost_target = str(definition.get("cost_target") or DEFAULT_MODE_DEFINITIONS["Free"]["cost_target"])
        description = str(definition.get("description") or DEFAULT_MODE_DEFINITIONS["Free"]["description"])
        llm_policy = str(definition.get("llm_policy") or DEFAULT_MODE_DEFINITIONS["Free"]["llm_policy"])
    elif selected == "Lean":
        cost_target = str(definition.get("cost_target") or DEFAULT_MODE_DEFINITIONS["Lean"]["cost_target"])
        description = str(definition.get("description") or DEFAULT_MODE_DEFINITIONS["Lean"]["description"])
        llm_policy = str(definition.get("llm_policy") or DEFAULT_MODE_DEFINITIONS["Lean"]["llm_policy"])
    elif selected == "Stable":
        cost_target = str(definition.get("cost_target") or DEFAULT_MODE_DEFINITIONS["Stable"]["cost_target"])
        description = str(definition.get("description") or DEFAULT_MODE_DEFINITIONS["Stable"]["description"])
        enabled.append("Paid data upgrade slot is active when keys are configured.")
        llm_policy = str(definition.get("llm_policy") or DEFAULT_MODE_DEFINITIONS["Stable"]["llm_policy"])
        if not (config.TIINGO_API_KEY or config.POLYGON_API_KEY or config.FMP_API_KEY):
            warnings.append("Stable mode selected but no Tiingo, Polygon, or FMP key is configured.")
    else:
        cost_target = str(definition.get("cost_target") or DEFAULT_MODE_DEFINITIONS["Premium"]["cost_target"])
        description = str(definition.get("description") or DEFAULT_MODE_DEFINITIONS["Premium"]["description"])
        enabled.append("Premium provider slots are active when keys are configured.")
        llm_policy = str(definition.get("llm_policy") or DEFAULT_MODE_DEFINITIONS["Premium"]["llm_policy"])
        if not config.FMP_API_KEY:
            warnings.append("Premium mode selected but FMP is not configured; other premium adapters remain optional.")

    if llm_enabled and selected == "Free":
        warnings.append("LLM was enabled during a Free-mode run; provider costs depend on the selected profile.")
    elif llm_enabled:
        enabled.append("Optional LLM thesis synthesis after deterministic evidence extraction")

    data_policy = (
        "Missing premium data is a disclosed gap, never a fabricated conclusion. "
        "Premium providers are upgrade slots; SEC/issuer evidence remains the backbone."
    )
    return BudgetPolicy(
        mode=selected,
        cost_target=cost_target,
        description=description,
        max_monthly_budget_usd=_optional_float(definition.get("max_monthly_budget_usd")),
        allow_llm=_optional_bool(definition.get("allow_llm"), selected != "Free"),
        allow_paid_data=_optional_bool(definition.get("allow_paid_data"), selected in {"Stable", "Premium"}),
        primary_llm_profile=str(definition.get("primary_llm_profile") or ""),
        secondary_llm_profile=str(definition.get("secondary_llm_profile") or ""),
        secondary_llm_enabled=_optional_bool(definition.get("secondary_llm_enabled"), selected != "Free"),
        provider_policy=provider_policy,
        config_source=str(definition.get("_config_source") or "builtin defaults"),
        enabled_sources=enabled,
        disabled_sources=disabled,
        optional_upgrade_slots=upgrades,
        llm_policy=llm_policy,
        data_policy=data_policy,
        warnings=warnings,
    )


def _provider_policy(definition: dict[str, Any]) -> dict[str, list[str]]:
    raw = definition.get("provider_policy") or {}
    if not isinstance(raw, dict):
        return {}
    policy: dict[str, list[str]] = {}
    for group, sources in raw.items():
        if isinstance(sources, list):
            policy[str(group)] = [str(source) for source in sources if str(source).strip()]
        elif isinstance(sources, str):
            policy[str(group)] = [sources]
    return policy


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in config.TRUE_VALUES:
        return True
    if normalized in config.FALSE_VALUES:
        return False
    return default
