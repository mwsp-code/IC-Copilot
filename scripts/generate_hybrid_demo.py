from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from equity_research import config
from equity_research.external_evidence import external_evidence_stack_from_config
from equity_research.hybrid_research import (
    CODEX_LOCAL_BACKEND,
    OPENAI_RESPONSES_BACKEND,
    CodexLocalProvider,
    OpenAIResponsesProvider,
    hybrid_run_from_payload,
)
from equity_research.auditable_retrieval import evaluate_demo_freeze
from equity_research.application.publication_readiness import (
    record_analog_candidate_revision,
)
from equity_research.domain.publication_readiness import (
    PublicationQualificationCode,
)
from equity_research.historical_analogs import apply_hybrid_analog_results
from equity_research.llm_vault import profile_api_key
from equity_research.local_secrets import LocalSecretsManager
from equity_research.pipeline import run_us_equity_research
from equity_research.models import DemoQualityPolicy
from equity_research.providers import (
    StooqPriceClient,
    build_consensus_provider,
)
from equity_research.research_store import ResearchStore
from equity_research.sample_data import demo_result
from equity_research.thesis_synthesis import OpenAICompatibleProvider


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a sanitized five-round DeepSeek + GPT-5.6 Sol demo fixture."
        )
    )
    parser.add_argument("--ticker", required=True)
    parser.add_argument(
        "--executor",
        choices=("codex_local", "openai_responses"),
        default="codex_local",
    )
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "data" / "demo_hybrid"),
    )
    selectors = parser.add_mutually_exclusive_group()
    selectors.add_argument("--run-id", default="")
    selectors.add_argument("--resume-run-id", default="")
    selectors.add_argument("--latest-eligible", action="store_true")
    args = parser.parse_args()

    store = ResearchStore()
    ticker = args.ticker.strip().upper()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.run_id:
        payload = store.load_hybrid_research_run(args.run_id)
        if payload is None:
            raise SystemExit(f"Saved hybrid research run {args.run_id} was not found.")
        return _freeze_saved_candidates(
            ticker,
            [payload],
            output_dir=output_dir,
        )
    if args.latest_eligible:
        return _freeze_saved_candidates(
            ticker,
            store.list_hybrid_research_runs(ticker),
            output_dir=output_dir,
        )

    manager = LocalSecretsManager()
    manager.load_into_environment()
    config.refresh_runtime_secrets()
    requested_parent = None
    if args.resume_run_id:
        requested_parent = store.load_hybrid_research_run(args.resume_run_id)
        if requested_parent is None:
            raise SystemExit(
                f"Saved hybrid research run {args.resume_run_id} was not found."
            )
        if str(requested_parent.get("ticker") or "").upper() != ticker:
            raise SystemExit("The requested resume run belongs to another ticker.")
    deepseek_key, deepseek_model, deepseek_base_url, deepseek_source = (
        _resolve_deepseek_credentials(store, manager)
    )
    if not deepseek_key:
        raise SystemExit(
            "DeepSeek is not configured. Save a DeepSeek profile in the LLM "
            "Provider Vault or configure DEEPSEEK_API_KEY."
        )
    planner = OpenAICompatibleProvider(
        api_key=deepseek_key,
        model=deepseek_model,
        base_url=deepseek_base_url,
        provider_name="deepseek",
        timeout_seconds=config.HYBRID_RESEARCH_TIMEOUT_SECONDS,
        max_tokens=config.HYBRID_DEEPSEEK_MAX_TOKENS,
        reasoning_effort=config.HYBRID_DEEPSEEK_REASONING_EFFORT,
        thinking_enabled=config.HYBRID_DEEPSEEK_THINKING,
    )
    if args.executor == "codex_local":
        executor = CodexLocalProvider(
            model=config.HYBRID_OPENAI_MODEL,
            reasoning_effort="max",
            timeout_seconds=config.HYBRID_RESEARCH_TIMEOUT_SECONDS,
            executable=config.CODEX_EXECUTABLE,
            enabled=True,
        )
        backend = CODEX_LOCAL_BACKEND
    else:
        if not config.OPENAI_API_KEY:
            raise SystemExit(
                "OPENAI_API_KEY is required for the openai_responses executor."
            )
        executor = OpenAIResponsesProvider(
            config.OPENAI_API_KEY,
            model=config.HYBRID_OPENAI_MODEL,
            reasoning_mode="pro",
            reasoning_effort=config.HYBRID_SOL_REASONING_EFFORT,
            max_output_tokens=config.HYBRID_SOL_MAX_OUTPUT_TOKENS,
            timeout_seconds=config.HYBRID_RESEARCH_TIMEOUT_SECONDS,
        )
        backend = OPENAI_RESPONSES_BACKEND

    consensus = build_consensus_provider(
        store=store,
        alpha_vantage_key=config.ALPHAVANTAGE_API_KEY or None,
        finnhub_key=config.FINNHUB_API_KEY or None,
        fmp_key=config.FMP_API_KEY or None,
        enable_nasdaq=False,
        enable_tradingview=False,
        enable_yahoo=False,
    )
    external = external_evidence_stack_from_config(
        fred_api_key=config.FRED_API_KEY or None,
        bea_api_key=config.BEA_API_KEY or None,
        census_api_key=config.CENSUS_API_KEY or None,
        enable_default_macro=True,
        global_macro_mode=True,
        enable_gdelt=False,
        wisburg_api_key=config.WISBURG_API_KEY or None,
        enable_wisburg=bool(config.WISBURG_API_KEY),
        refresh_cache=False,
        store=store,
    )
    result = run_us_equity_research(
        ticker,
        price_client=StooqPriceClient(
            store=store,
            tiingo_key=config.TIINGO_API_KEY or None,
            eodhd_key=config.EODHD_API_KEY or None,
        ),
        consensus=consensus,
        external_evidence_provider=external,
        llm_provider=planner,
        secondary_llm_provider=None,
        budget_mode="Premium",
        store=store,
        research_profile="deep_initiation",
        enable_hybrid_research=True,
        hybrid_planner_provider=planner,
        hybrid_executor_provider=executor,
        hybrid_checker_provider=planner,
        hybrid_critic_provider=executor,
        hybrid_execution_backend=backend,
        # The five-round artifact already supplies the model-authored IC
        # narrative. Avoid a redundant post-run thesis call in the maintainer
        # path; normal live research keeps that lane enabled.
        enable_thesis_synthesis_llm=False,
        resume_hybrid_run_id=args.resume_run_id,
    )
    run = result.hybrid_research
    if run is not None and requested_parent is not None:
        run.generation_provenance["requested_resume_run_id"] = args.resume_run_id
        parent_hash = str(requested_parent.get("evidence_hash") or "")
        run.generation_provenance["resume_evidence_relationship"] = (
            "unchanged_or_additive"
            if parent_hash == run.evidence_hash
            or bool(getattr(run, "partial_rerun_manifest", None))
            else "fresh_run_due_to_changed_evidence"
        )
    analogs = list(result.historical_analogs.dossiers)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{ticker}.json"
    freeze_decision = _evaluate_with_canonical_analog_downgrade(
        ticker,
        run=run,
        audited_dossiers=analogs,
        output_path=str(output_path),
        policy=DemoQualityPolicy(),
    )
    freeze_decision.output_path = _public_output_reference(output_path)
    if run is not None:
        run.demo_freeze_decision = freeze_decision
    if not freeze_decision.allowed:
        report_path = output_dir / f"{ticker}.freeze-report.json"
        repair_report = _bounded_freeze_report(
            ticker,
            [freeze_decision],
            output_path=output_path,
            selection_policy="requested_repair_candidate",
        )
        report_path.write_text(
            json.dumps(
                repair_report,
                ensure_ascii=True,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        raise SystemExit(
            "Hybrid demo was not frozen. Existing fixture was preserved. "
            f"Review {report_path}: "
            + "; ".join(freeze_decision.blockers[:5])
        )
    _mark_run_published(run, freeze_decision)
    payload = asdict(run)
    serialized = json.dumps(payload, ensure_ascii=True, indent=2)
    _assert_no_secrets(serialized)
    _assert_license_safe(payload)
    freeze_decision.sanitization_status = "passed_secrets_and_licensing"
    payload = asdict(run)
    serialized = json.dumps(payload, ensure_ascii=True, indent=2)
    hybrid_run_from_payload(json.loads(serialized))
    temporary = output_path.with_suffix(".json.tmp")
    temporary.write_text(serialized + "\n", encoding="utf-8")
    temporary.replace(output_path)
    _write_success_report(ticker, freeze_decision, output_path)
    print(
        f"Wrote {output_path} with {len(run.rounds)} validated rounds; "
        f"backend={run.execution_backend.label}; "
        f"deepseek_credentials={deepseek_source}."
    )
    return 0


def _freeze_saved_candidates(
    ticker: str,
    payloads: list[dict],
    *,
    output_dir: Path,
) -> int:
    """Publish the newest completed, closed, policy-passing saved run."""

    output_path = output_dir / f"{ticker}.json"
    policy = DemoQualityPolicy()
    evaluated = []
    ordered_payloads = _sort_saved_candidate_payloads(payloads)
    for payload in ordered_payloads:
        run = hybrid_run_from_payload(payload)
        if run.ticker.upper() != ticker:
            continue
        base_report = demo_result(ticker).historical_analogs
        report = apply_hybrid_analog_results(base_report, run)
        analogs = list(report.dossiers)
        decision = _evaluate_with_canonical_analog_downgrade(
            ticker,
            run=run,
            audited_dossiers=analogs,
            output_path=str(output_path),
            policy=policy,
        )
        decision.output_path = _public_output_reference(output_path)
        evaluated.append(decision)
        if not decision.allowed:
            continue
        run.demo_freeze_decision = decision
        _mark_run_published(run, decision)
        serialized = json.dumps(asdict(run), ensure_ascii=True, indent=2)
        _assert_no_secrets(serialized)
        _assert_license_safe(asdict(run))
        decision.sanitization_status = "passed_secrets_and_licensing"
        serialized = json.dumps(asdict(run), ensure_ascii=True, indent=2)
        hybrid_run_from_payload(json.loads(serialized))
        temporary = output_path.with_suffix(".json.tmp")
        temporary.write_text(serialized + "\n", encoding="utf-8")
        temporary.replace(output_path)
        _write_success_report(ticker, decision, output_path)
        print(
            f"Published {run.run_id} to {output_path}; "
            f"completed_at={run.completed_at}; policy={policy.policy_version}."
        )
        return 0

    report_path = output_dir / f"{ticker}.freeze-report.json"
    repair_report = _bounded_freeze_report(
        ticker,
        evaluated,
        output_path=output_path,
        selection_policy="newest_completed_closed_and_gate_passing",
    )
    temporary = report_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(repair_report, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(report_path)
    raise SystemExit(
        "No saved AAPL run passed the versioned demo quality policy. "
        f"The existing public artifact was preserved; review {report_path}."
    )


def _bounded_freeze_report(
    ticker: str,
    decisions: list,
    *,
    output_path: Path,
    selection_policy: str,
) -> dict:
    """Keep maintainer repair reports readable without copying full manifests."""

    policy_version = next(
        (str(item.policy_version) for item in decisions if item.policy_version),
        DemoQualityPolicy().policy_version,
    )
    candidates = []
    for decision in decisions:
        evaluation = decision.publication_gate_evaluation
        candidates.append({
            "source_run_id": decision.source_run_id,
            "source_run_completed_at": decision.source_run_completed_at,
            "status": decision.status,
            "allowed": decision.allowed,
            "publication_label": decision.publication_label,
            "canonical_revision_id": decision.canonical_revision_id,
            "validation_status": decision.validation_status,
            "qa_closure_state": decision.qa_closure_state,
            "evidence_hash": decision.evidence_hash,
            "policy_version": decision.policy_version,
            "failure_codes": [
                item.code for item in evaluation.failures
            ] if evaluation is not None else [],
            "qualification_codes": [
                item.code for item in evaluation.qualifications
            ] if evaluation is not None else [],
            "repair_kinds": list(dict.fromkeys(
                item.repair_kind for item in decision.repair_actions
            )),
            "blockers": list(decision.blockers[:12]),
            "next_actions": list(decision.next_actions[:12]),
        })
    return {
        "ticker": ticker,
        "status": "blocked",
        "policy_version": policy_version,
        "candidate_count": len(candidates),
        "selection_policy": selection_policy,
        "sanitization_status": "passed_decision_metadata_only",
        "candidates": candidates,
        "published_artifact_preserved": output_path.exists(),
        "built_in_fallback_available": True,
    }


def _write_success_report(
    ticker: str,
    decision,
    output_path: Path,
) -> None:
    report = _bounded_freeze_report(
        ticker,
        [decision],
        output_path=output_path,
        selection_policy="newest_completed_closed_and_gate_passing",
    )
    report.update({
        "status": "published",
        "published_artifact_preserved": True,
        "built_in_fallback_available": True,
    })
    report_path = output_path.with_name(f"{ticker}.freeze-report.json")
    temporary = report_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(report_path)


def _mark_run_published(run, decision) -> None:
    published_at = str(decision.evaluated_at or "")
    if not getattr(run, "publishable_at", ""):
        run.publishable_at = published_at
    run.published_at = published_at
    run.lifecycle_state = "published"
    if not getattr(run, "terminal_at", ""):
        run.terminal_at = published_at


def _evaluate_with_canonical_analog_downgrade(
    ticker: str,
    *,
    run,
    audited_dossiers: list,
    output_path: str,
    policy: DemoQualityPolicy,
):
    decision = evaluate_demo_freeze(
        ticker,
        run=run,
        audited_dossiers=audited_dossiers,
        output_path=output_path,
        policy=policy,
    )
    evaluation = decision.publication_gate_evaluation
    analog_ids = {
        analog_id
        for qualification in (
            evaluation.qualifications if evaluation is not None else ()
        )
        if qualification.code == (
            PublicationQualificationCode.ANALOG_DOWNGRADED_TO_CANDIDATE.value
        )
        for analog_id in qualification.affected_analog_ids
        if analog_id
    }
    revision = record_analog_candidate_revision(run, analog_ids)
    if revision is None:
        return decision
    return evaluate_demo_freeze(
        ticker,
        run=run,
        audited_dossiers=audited_dossiers,
        output_path=output_path,
        policy=policy,
    )


def _sort_saved_candidate_payloads(payloads: list[dict]) -> list[dict]:
    """Evaluate newest attempts first without treating recency as eligibility."""

    return sorted(
        payloads,
        key=lambda item: str(
            item.get("published_at")
            or item.get("publishable_at")
            or item.get("closure_completed_at")
            or item.get("completed_at")
            or item.get("research_attempt_finished_at")
            or item.get("generated_at")
            or ""
        ),
        reverse=True,
    )


def _public_output_reference(output_path: Path) -> str:
    try:
        return output_path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return output_path.name


def _resolve_deepseek_credentials(
    store: ResearchStore,
    manager: LocalSecretsManager,
) -> tuple[str, str, str, str]:
    selection = store.get_llm_selection()
    selected_id = (
        selection.get("primary_profile_id")
        or config.LLM_PRIMARY_PROFILE_ID
    )
    selected = store.get_llm_profile(selected_id) if selected_id else None
    if selected and selected.provider_preset == "deepseek":
        key = profile_api_key(selected, manager)
        if key:
            return (
                key,
                selected.model or config.HYBRID_DEEPSEEK_MODEL,
                selected.base_url or "https://api.deepseek.com",
                "provider_vault",
            )

    for profile in store.list_llm_profiles():
        if profile.provider_preset != "deepseek":
            continue
        key = profile_api_key(profile, manager)
        if key:
            return (
                key,
                profile.model or config.HYBRID_DEEPSEEK_MODEL,
                profile.base_url or "https://api.deepseek.com",
                "provider_vault",
            )

    return (
        config.DEEPSEEK_API_KEY,
        config.HYBRID_DEEPSEEK_MODEL,
        "https://api.deepseek.com",
        "fixed_environment" if config.DEEPSEEK_API_KEY else "missing",
    )


def _assert_no_secrets(serialized: str) -> None:
    for key_name in (
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
        "FMP_API_KEY",
        "WISBURG_API_KEY",
        "TIINGO_API_KEY",
        "EODHD_API_KEY",
    ):
        value = os.getenv(key_name, "")
        if value and value in serialized:
            raise RuntimeError(
                f"Refusing to freeze demo because {key_name} was detected."
            )


def _assert_license_safe(payload: object, *, path: str = "root") -> None:
    """Reject fields that can carry licensed or provider-private raw payloads."""

    prohibited = {
        "raw_response",
        "raw_payload",
        "licensed_payload",
        "full_report_text",
        "provider_private_payload",
        "hidden_reasoning",
    }
    if isinstance(payload, dict):
        for key, value in payload.items():
            normalized = str(key).casefold()
            if normalized in prohibited and value not in (None, "", [], {}):
                raise RuntimeError(
                    f"Refusing to freeze demo because {path}.{key} contains "
                    "non-sanitized provider or licensed content."
                )
            _assert_license_safe(value, path=f"{path}.{key}")
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            _assert_license_safe(value, path=f"{path}[{index}]")


if __name__ == "__main__":
    raise SystemExit(main())
