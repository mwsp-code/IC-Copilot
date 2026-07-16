from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from . import config
from .adr_profiles import adr_profile_for

if TYPE_CHECKING:
    from .pipeline import ResearchResult


def build_contribution_pack(result: "ResearchResult") -> dict:
    economics = result.company_economics
    playbook = economics.industry_playbook
    adr_profile = adr_profile_for(result.identity.ticker)
    missing_metrics = [
        item for item in result.metric_resolution_audit.items
        if item.status == "metric missing"
    ]
    source_gaps = [
        entry for entry in result.source_coverage_matrix.entries
        if entry.status in {"source unavailable", "source not attempted", "parse failed"}
    ]
    return {
        "schema_version": "contribution-pack-v1",
        "ticker": result.identity.ticker,
        "company_name": result.identity.name,
        "generated_at": _utc_now(),
        "review_required": True,
        "methodology_note": (
            "This pack is a draft. Applying aliases, playbooks, profiles, or adapters requires "
            "fixture coverage and source-integrity review."
        ),
        "sector_playbook": {
            "status": "existing" if playbook.playbook_source else "draft",
            "target_file": str(config.SECTOR_KPI_PLAYBOOK_CSV),
            "sector_template": playbook.sector_template,
            "industry_label": playbook.industry_label,
            "key_kpis": playbook.key_kpis,
            "leading_indicators": playbook.leading_indicators,
            "valuation_methods": playbook.valuation_methods,
            "macro_sensitivities": playbook.macro_sensitivities,
            "normal_catalysts": playbook.normal_catalysts,
            "fixture_requirement": f"Add a no-network {result.identity.ticker} playbook regression test.",
        },
        "adr_profile": {
            "status": "existing" if adr_profile else "not_applicable_or_missing",
            "target_file": str(config.ADR_PROFILE_CSV),
            "ticker": result.identity.ticker,
            "home_exchange": adr_profile.home_exchange if adr_profile else "Unknown",
            "adr_ratio": adr_profile.ordinary_share_ratio if adr_profile else None,
            "reporting_currency": adr_profile.reporting_currency if adr_profile else "Unknown",
            "primary_forms": adr_profile.primary_forms if adr_profile else [],
            "issuer_ir_sources": adr_profile.issuer_ir_sources if adr_profile else [],
        },
        "metric_alias_drafts": [
            {
                "metric": item.metric,
                "target_file": str(config.METRIC_ONTOLOGY_CSV),
                "status": item.status,
                "blocker": item.blocker,
                "candidate_alias": "",
                "formula": item.formula,
                "required_validation": "Exact source tag/label, unit, period, and fixture expected value.",
            }
            for item in missing_metrics
        ],
        "source_adapter_specs": [
            {
                "source_type": entry.source_type,
                "label": entry.label,
                "jurisdiction": entry.jurisdiction,
                "source_family": entry.source_family,
                "current_status": entry.status,
                "blocker": entry.blocker,
                "required_contract": [
                    "provider health and failure classification",
                    "source URL and source-as-of timestamp",
                    "citation and licensing metadata",
                    "deterministic parser validation",
                    "fixture tests with no live-network dependency",
                ],
                "apply_mode": "scaffold_and_review_only",
            }
            for entry in source_gaps
        ],
        "demo_case_draft": {
            "ticker": result.identity.ticker,
            "lesson": _demo_lesson(result),
            "network_required": False,
            "required_test": "Sanitized deterministic payload loads without keys or network.",
        },
    }


def save_contribution_pack(
    pack: dict,
    directory: Path | None = None,
) -> Path:
    target_dir = directory or Path("data/contribution_drafts")
    target_dir.mkdir(parents=True, exist_ok=True)
    ticker = re.sub(r"[^A-Z0-9._-]", "", str(pack.get("ticker") or "TICKER").upper())
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = target_dir / f"{ticker}_{stamp}.json"
    path.write_text(json.dumps(pack, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _demo_lesson(result: "ResearchResult") -> str:
    top = result.ideas[0] if result.ideas else None
    if not top:
        return "Demonstrates how the app reports exhaustive evidence gaps without fabricating a thesis."
    return (
        f"Demonstrates {top.stage} analysis for {result.identity.ticker}: "
        f"{top.title}, with evidence, peer, valuation, attribution, and monitoring audit trails."
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
