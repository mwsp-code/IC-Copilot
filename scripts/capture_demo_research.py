from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, is_dataclass
from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from equity_research import config
from equity_research.external_evidence import external_evidence_stack_from_config
from equity_research.llm_vault import profile_to_provider
from equity_research.local_secrets import LocalSecretsManager, MANAGED_SECRET_KEYS
from equity_research.performance import ResearchProfiler
from equity_research.pipeline import run_us_equity_research
from equity_research.providers import StooqPriceClient, build_consensus_provider
from equity_research.research_store import ResearchStore
from equity_research.thesis_synthesis import provider_from_config


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run a live, fully configured research pass and save a redacted normalized "
            "summary suitable for building a no-network demo fixture."
        ),
    )
    parser.add_argument("--ticker", required=True)
    parser.add_argument(
        "--profile",
        default="deep_initiation",
        choices=("fast_screening", "adaptive_ic", "deep_initiation"),
    )
    parser.add_argument(
        "--budget-mode",
        default="Premium",
        choices=("Free", "Lean", "Stable", "Premium"),
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=Path(".cache/demo_capture.json"),
    )
    args = parser.parse_args()

    manager = LocalSecretsManager()
    manager.load_into_environment()
    config.refresh_runtime_secrets()
    store = ResearchStore()
    selection = store.get_llm_selection()
    primary_profile = store.get_llm_profile(selection.get("primary_profile_id", ""))
    secondary_profile = store.get_llm_profile(selection.get("secondary_profile_id", ""))
    primary_llm = profile_to_provider(primary_profile, manager, enabled=True)
    if primary_llm is None:
        primary_llm = provider_from_config(enabled=True)
    secondary_llm = profile_to_provider(
        secondary_profile,
        manager,
        enabled=bool(selection.get("enable_secondary", True)),
    )

    consensus = build_consensus_provider(
        store=store,
        alpha_vantage_key=config.ALPHAVANTAGE_API_KEY,
        finnhub_key=config.FINNHUB_API_KEY,
        fmp_key=config.FMP_API_KEY,
        enable_nasdaq=True,
        enable_tradingview=True,
        enable_yahoo=False,
    )
    external = external_evidence_stack_from_config(
        fred_api_key=config.FRED_API_KEY,
        enable_fred=bool(config.FRED_API_KEY),
        bls_api_key=config.BLS_API_KEY,
        enable_bls=True,
        bea_api_key=config.BEA_API_KEY,
        enable_bea=bool(config.BEA_API_KEY),
        census_api_key=config.CENSUS_API_KEY,
        enable_census=bool(config.CENSUS_API_KEY),
        enable_treasury=True,
        enable_ofr=True,
        enable_world_bank=True,
        enable_imf=True,
        enable_gdelt=True,
        wisburg_api_key=config.WISBURG_API_KEY,
        enable_wisburg=bool(config.WISBURG_API_KEY),
        enable_default_macro=True,
        global_macro_mode=True,
        store=store,
        use_cache=True,
        refresh_cache=True,
    )
    result = run_us_equity_research(
        args.ticker.upper(),
        price_client=StooqPriceClient(
            store=store,
            tiingo_key=config.TIINGO_API_KEY,
            eodhd_key=config.EODHD_API_KEY,
        ),
        consensus=consensus,
        external_evidence_provider=external,
        llm_provider=primary_llm,
        secondary_llm_provider=secondary_llm,
        enable_secondary_llm_review=bool(selection.get("enable_secondary", True)),
        secondary_llm_min_stage=str(
            selection.get("secondary_min_stage") or "Research-Ready"
        ),
        llm_language_policy=str(
            selection.get("language_policy") or "bilingual_audit"
        ),
        budget_mode=args.budget_mode,
        store=store,
        profiler=ResearchProfiler(enabled=True),
        research_profile=args.profile,
    )

    payload = _capture_payload(
        result,
        args.budget_mode,
        args.profile,
        manager,
        primary_profile=primary_profile,
        secondary_profile=secondary_profile,
    )
    profile_secrets = tuple(
        secret
        for profile in (primary_profile, secondary_profile)
        if profile
        if (secret := manager.get(profile.secret_ref))
    )
    secrets = tuple(
        value
        for key in MANAGED_SECRET_KEYS
        if (value := os.getenv(key, "")).strip()
    ) + profile_secrets
    payload = _redact(payload, secrets)
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True),
        encoding="utf-8",
    )
    print(json.dumps({
        "ticker": result.identity.ticker,
        "output": str(args.json_out),
        "providers_configured": payload["configuration"]["configured_providers"],
        "provider_status_count": len(payload["provider_statuses"]),
        "metrics": len(result.metrics),
        "events": len(result.events),
        "ideas": len(result.ideas),
        "llm_status": payload["llm"]["execution_status"],
        "total_seconds": round(result.profiling.total_ms / 1000, 2),
    }, indent=2))


def _capture_payload(
    result,
    budget_mode: str,
    profile: str,
    manager,
    *,
    primary_profile=None,
    secondary_profile=None,
) -> dict[str, Any]:
    provider_statuses = [
        *getattr(result.consensus, "provider_statuses", []),
        *getattr(result.external_evidence, "provider_statuses", []),
        *getattr(result.management_sources, "provider_statuses", []),
    ]
    configured = [
        item["label"]
        for item in manager.redacted_status()
        if item["configured"] and item["key"] != "SEC_USER_AGENT"
    ]
    if primary_profile and primary_profile.key_configured:
        configured.append(f"Primary LLM profile: {primary_profile.display_name}")
    if secondary_profile and secondary_profile.key_configured:
        configured.append(f"Secondary LLM profile: {secondary_profile.display_name}")
    configured = list(dict.fromkeys(configured))
    return {
        "identity": _plain(result.identity),
        "configuration": {
            "budget_mode": budget_mode,
            "research_profile": profile,
            "configured_providers": configured,
            "llm_primary": getattr(
                getattr(result, "llm_run_manifest", None), "provider", "not_run"
            ),
            "llm_model": getattr(
                getattr(result, "llm_run_manifest", None), "model", "not_run"
            ),
        },
        "provider_statuses": [_plain(item) for item in provider_statuses],
        "financial_coverage": _plain(result.financial_coverage),
        "metrics": [
            {
                "name": item.name,
                "value": item.value,
                "previous_value": item.previous_value,
                "yoy_change_pct": item.yoy_change_pct,
                "unit": item.unit,
                "period_end": item.period_end,
                "previous_period_end": item.previous_period_end,
                "fiscal_period": item.fiscal_period,
                "form": item.form,
                "filed": item.filed,
                "source_url": item.source_url,
                "source_kind": item.source_kind,
            }
            for item in result.metrics
        ],
        "events": [
            {
                "category": item.category,
                "title": item.title,
                "summary": item.summary,
                "direction": item.direction,
                "severity": item.severity,
                "event_date": item.event_date,
                "source": item.source,
                "metrics": _plain(item.metrics),
                "citations": [
                    {
                        "source": citation.source,
                        "url": citation.url,
                        "filed": citation.filed,
                        "form": citation.form,
                        "section": citation.section,
                        "snippet": citation.snippet,
                        "accession": citation.accession,
                        "period_end": citation.period_end,
                        "source_tier": citation.source_tier,
                    }
                    for citation in item.citations[:3]
                ],
            }
            for item in result.events[:20]
        ],
        "ideas": [
            {
                "idea_id": item.idea_id,
                "title": item.title,
                "stage": item.stage,
                "direction": item.direction,
                "score": _plain(item.score),
                "thesis": item.thesis,
                "variant_perception": item.variant_perception,
                "counter_thesis": item.strongest_counter_thesis,
                "direction_rationale": item.direction_rationale,
                "thesis_grade_status": item.thesis_grade_status,
                "data_gaps": _idea_gaps(item),
            }
            for item in result.ideas[:12]
        ],
        "consensus": {
            "status": result.consensus.status,
            "target": _plain(result.consensus.target),
            "recommendations": _plain(result.consensus.recommendations),
            "estimates": [_plain(item) for item in result.consensus.estimates[:12]],
            "revisions": [_plain(item) for item in result.consensus.revisions[:12]],
            "data_gaps": list(result.consensus.data_gaps),
        },
        "price_reaction": _plain(result.price_reaction),
        "valuation": _plain(result.valuation),
        "market_implied_expectations": _plain(result.market_implied_expectations),
        "company_economics": _plain(result.company_economics),
        "credit_lens": _plain(result.credit_lens),
        "peer_metric_readthrough": _plain(result.peer_metric_readthrough),
        "management": {
            "status": result.management_sources.status,
            "document_count": len(result.management_sources.documents),
            "turn_count": len(result.management_sources.transcript_turns),
            "claim_count": len(result.management_sources.claims),
            "cross_check_count": len(result.management_sources.cross_checks),
            "claims": [
                {
                    "claim_type": item.claim_type,
                    "statement": item.statement,
                    "status": item.status,
                    "event_date": item.event_date,
                    "metric": item.metric,
                    "direction": item.direction,
                    "source_type": item.source_type,
                    "source_tier": item.source_tier,
                }
                for item in result.management_sources.claims[:12]
            ],
        },
        "wisburg": {
            "status": result.wisburg_lens.status,
            "themes": [_plain(item) for item in result.wisburg_lens.themes[:12]],
            "debate_map": _plain(result.wisburg_lens.debate_map),
            "coverage_audit": _plain(result.wisburg_lens.coverage_audit),
            "data_gaps": list(result.wisburg_lens.caveats),
        },
        "llm": {
            "execution_status": getattr(
                result.llm_run_manifest, "execution_status", "not_run"
            ),
            "guardrail_status": getattr(
                result.llm_run_manifest, "guardrail_status", "not_run"
            ),
            "evidence_sufficiency": _plain(result.evidence_sufficiency),
            "thesis_brief": _plain(result.thesis_brief),
            "thesis_critique": _plain(result.thesis_critique),
            "reviews": [_plain(item) for item in result.llm_reviews],
        },
        "profiling": _plain(result.profiling),
        "coverage_notes": list(result.coverage_notes),
    }


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if is_dataclass(value):
        return {key: _plain(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(item) for item in value]
    return str(value)


def _idea_gaps(idea) -> list[str]:
    gaps = list(getattr(idea, "corroboration_gaps", []) or [])
    gate = getattr(idea, "gate_result", None)
    if gate is not None:
        gaps.extend(getattr(gate, "research_ready_failed", []) or [])
        gaps.extend(getattr(gate, "high_conviction_failed", []) or [])
    chain = getattr(idea, "conviction_chain", None)
    if chain is not None:
        gaps.extend(getattr(chain, "data_gaps", []) or [])
    return list(dict.fromkeys(str(item) for item in gaps if str(item).strip()))


def _redact(value: Any, secrets: tuple[str, ...]) -> Any:
    if isinstance(value, dict):
        return {key: _redact(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, secrets) for item in value]
    if not isinstance(value, str):
        return value
    redacted = value
    for secret in secrets:
        redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


if __name__ == "__main__":
    main()
