from __future__ import annotations

import argparse
import hashlib
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
    DETERMINISTIC_SAFE_REVISION_VERSION,
    build_deterministic_safe_primary_revision,
    record_analog_candidate_revision,
)
from equity_research.application.repair_stage_execution import (
    DeterministicSafeRevisionProvider,
    RepairExecutionPolicy,
    RepairStage,
    RepairStageSupervisor,
)
from equity_research.application.cash_conversion import (
    validate_cash_conversion_recurrence_bridge,
)
from equity_research.domain.publication_readiness import (
    PublicationQualificationCode,
)
from equity_research.domain.research_ontology import ONTOLOGY_SCHEMA_VERSION
from equity_research.historical_analogs import apply_hybrid_analog_results
from equity_research.llm_vault import profile_api_key, profile_supports_role
from equity_research.infrastructure.repair_checkpoint_repository import (
    SqliteRepairStageCheckpointRepository,
)
from equity_research.local_secrets import LocalSecretsManager
from equity_research.pipeline import run_us_equity_research
from equity_research.models import DemoQualityPolicy
from equity_research.providers import (
    StooqPriceClient,
    build_consensus_provider,
)
from equity_research.research_store import ResearchStore
from equity_research.sample_data import demo_result
from equity_research.secret_safety import contains_credential_like_token
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
    parser.add_argument(
        "--retry-stage",
        choices=(RepairStage.INDEPENDENT_CLOSURE.value,),
        default="",
        help=(
            "Explicitly retry one previously timed-out repair stage. Requires "
            "--resume-run-id and a changed backend or larger timeout boundary."
        ),
    )
    args = parser.parse_args()

    if args.retry_stage and not args.resume_run_id:
        parser.error("--retry-stage requires --resume-run-id.")

    store = ResearchStore()
    repair_repository = SqliteRepairStageCheckpointRepository(
        config.RESEARCH_DB_PATH
    )
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
            repair_checkpoints=repair_repository.list_for_attempt(
                _repair_attempt_id(payload)
            ),
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
    repair_attempt_id = ""
    closure_retry = args.retry_stage == RepairStage.INDEPENDENT_CLOSURE.value
    repair_policy = RepairExecutionPolicy(
        revision_timeout_seconds=config.REPAIR_REVISION_TIMEOUT_SECONDS,
        closure_timeout_seconds=(
            config.REPAIR_CLOSURE_RETRY_TIMEOUT_SECONDS
            if closure_retry else config.REPAIR_CLOSURE_TIMEOUT_SECONDS
        ),
        total_timeout_seconds=(
            config.REPAIR_TOTAL_RETRY_TIMEOUT_SECONDS
            if closure_retry else config.REPAIR_TOTAL_TIMEOUT_SECONDS
        ),
        max_transient_retries=config.REPAIR_MAX_TRANSIENT_RETRIES,
        policy_version=(
            "repair-execution-policy-v2-closure-retry"
            if closure_retry else "repair-execution-policy-v1"
        ),
    )
    if requested_parent is not None:
        repair_attempt_id = _repair_attempt_id(requested_parent)
    deepseek_key, deepseek_model, deepseek_base_url, deepseek_source = (
        _resolve_deepseek_credentials(store, manager)
    )
    safe_revision_without_provider = bool(
        requested_parent is not None
        and str(requested_parent.get("deterministic_validation_status") or "")
        == "passed"
        and str(requested_parent.get("lifecycle_state") or "")
        == "repair_required"
    )
    if not deepseek_key and not safe_revision_without_provider:
        raise SystemExit(
            "DeepSeek is not configured. Save a DeepSeek profile in the LLM "
            "Provider Vault or configure DEEPSEEK_API_KEY."
        )
    planner = (
        OpenAICompatibleProvider(
            api_key=deepseek_key,
            model=deepseek_model,
            base_url=deepseek_base_url,
            provider_name="deepseek",
            timeout_seconds=config.HYBRID_RESEARCH_TIMEOUT_SECONDS,
            max_tokens=config.HYBRID_DEEPSEEK_MAX_TOKENS,
            reasoning_effort=config.HYBRID_DEEPSEEK_REASONING_EFFORT,
            thinking_enabled=config.HYBRID_DEEPSEEK_THINKING,
        )
        if deepseek_key else None
    )
    if requested_parent is not None:
        planner = DeterministicSafeRevisionProvider(
            planner,
            revision_builder=build_deterministic_safe_primary_revision,
            allowed_failure_classes=(
                "provider_credit_required",
                "provider_unavailable",
                "invalid_response",
            ),
        )
        if not deepseek_key:
            deepseek_source = "deterministic_safe_revision"
    auditor_credential_source = "authenticated_local_codex"
    if args.executor == "codex_local":
        executor = CodexLocalProvider(
            model=config.HYBRID_OPENAI_MODEL,
            reasoning_effort="max",
            closure_reasoning_effort=(
                config.HYBRID_CODEX_CLOSURE_REASONING_EFFORT
            ),
            timeout_seconds=config.HYBRID_RESEARCH_TIMEOUT_SECONDS,
            executable=config.CODEX_EXECUTABLE,
            enabled=True,
            event_stream_enabled=config.CODEX_EVENT_STREAM_EXECUTION,
            startup_timeout_seconds=config.CODEX_STARTUP_TIMEOUT_SECONDS,
            quiet_after_seconds=config.CODEX_QUIET_AFTER_SECONDS,
        )
        backend = CODEX_LOCAL_BACKEND
    else:
        openai_key, openai_source = _resolve_openai_auditor_credentials(
            store,
            manager,
        )
        if not openai_key:
            _write_hosted_auditor_setup_required(
                output_dir / f"{ticker}.freeze-report.json",
                ticker=ticker,
                output_path=output_dir / f"{ticker}.json",
                resume_run_id=args.resume_run_id,
                repair_attempt_id=repair_attempt_id,
                repair_repository=repair_repository,
            )
            raise SystemExit(
                "Hosted Independent QA needs setup. Save an eligible OpenAI "
                "Sol auditor profile or configure OPENAI_API_KEY; the selected "
                "repair remains staged and completed research is preserved."
            )
        executor = OpenAIResponsesProvider(
            openai_key,
            model=config.HYBRID_OPENAI_MODEL,
            reasoning_mode="pro",
            reasoning_effort=config.HYBRID_SOL_REASONING_EFFORT,
            max_output_tokens=config.HYBRID_SOL_MAX_OUTPUT_TOKENS,
            timeout_seconds=config.HYBRID_RESEARCH_TIMEOUT_SECONDS,
        )
        backend = OPENAI_RESPONSES_BACKEND
        auditor_credential_source = openai_source

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
    last_progress_key = ""

    def report_progress(payload: dict) -> None:
        nonlocal last_progress_key
        key = "|".join((
            str(payload.get("current_stage") or ""),
            str(payload.get("status") or ""),
            str(payload.get("repair_stage_index") or ""),
            str(payload.get("retry_count") or ""),
        ))
        if not key or key == last_progress_key:
            return
        last_progress_key = key
        stage_index = payload.get("repair_stage_index")
        stage_count = payload.get("repair_stage_count")
        prefix = (
            f"Repair {stage_index}/{stage_count}: "
            if stage_index and stage_count else "Research: "
        )
        print(
            prefix
            + str(payload.get("current_stage") or payload.get("message") or "Working"),
            flush=True,
        )

    try:
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
            # narrative. Avoid a redundant post-run thesis call in the
            # maintainer path; normal live research keeps that lane enabled.
            enable_thesis_synthesis_llm=False,
            resume_hybrid_run_id=args.resume_run_id,
            hybrid_progress_callback=report_progress,
            repair_stage_repository=repair_repository,
            repair_attempt_id=repair_attempt_id,
            repair_execution_policy=repair_policy,
            repair_retry_stages=(
                (args.retry_stage,) if args.retry_stage else ()
            ),
        )
    except Exception as exc:
        report_path = output_dir / f"{ticker}.freeze-report.json"
        _write_terminal_generator_failure(
            report_path,
            ticker=ticker,
            output_path=output_dir / f"{ticker}.json",
            exc=exc,
            repair_attempt_id=repair_attempt_id,
            repair_repository=repair_repository,
        )
        raise SystemExit(
            "Demo generation stopped safely. Existing public output was "
            f"preserved. Review {report_path}: {type(exc).__name__}."
        ) from exc
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
    if run is not None:
        run.generation_provenance["auditor_credential_source"] = (
            auditor_credential_source
        )
    analogs = list(result.historical_analogs.dossiers)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{ticker}.json"

    def evaluate_publication():
        return _evaluate_with_canonical_analog_downgrade(
            ticker,
            run=run,
            audited_dossiers=analogs,
            output_path=str(output_path),
            policy=DemoQualityPolicy(),
        )

    if requested_parent is not None and repair_attempt_id:
        publication_supervisor = RepairStageSupervisor(
            attempt_id=repair_attempt_id,
            parent_run_id=str(
                requested_parent.get("run_id") or args.resume_run_id
            ),
            ticker=ticker,
            evidence_hash=str(getattr(run, "evidence_hash", "") or ""),
            repository=repair_repository,
            policy=repair_policy,
            progress_callback=report_progress,
        )
        freeze_decision = publication_supervisor.record_deterministic_stage(
            RepairStage.PUBLICATION_EVALUATION,
            operation=evaluate_publication,
            result_payload=lambda decision: {
                "value": bool(decision.allowed),
                "status": decision.status,
                "publication_label": decision.publication_label,
                "blockers": list(decision.blockers[:8]),
            },
        )
    else:
        freeze_decision = evaluate_publication()
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
            repair_checkpoints=(
                repair_repository.list_for_attempt(repair_attempt_id)
                if repair_attempt_id else []
            ),
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
    _write_success_report(ticker, freeze_decision, output_path, run=run)
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
    repair_checkpoints=(),
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
        presentation_blockers = _public_presentation_blockers(
            ticker, run, historical_report=report,
        )
        if presentation_blockers:
            # Demo-freeze policy verifies research integrity.  The public
            # renderer has an additional compatibility contract: it must be
            # able to present the showcase analysis without falling back to a
            # stale, weaker fixture.  Keep the run immutable and make the
            # reason visible in the bounded maintainer report.
            decision.allowed = False
            decision.status = "blocked"
            decision.publication_label = "Not publication-ready"
            decision.blockers = list(dict.fromkeys([
                *decision.blockers,
                *presentation_blockers,
            ]))
            decision.next_actions = list(dict.fromkeys([
                *decision.next_actions,
                "Complete the required public-demo analysis before republishing.",
            ]))
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
        _write_success_report(ticker, decision, output_path, run=run)
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
        repair_checkpoints=repair_checkpoints,
    )
    temporary = report_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(repair_report, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(report_path)
    raise SystemExit(
        f"No saved {ticker} run passed the versioned demo quality policy. "
        f"The existing public artifact was preserved; review {report_path}."
    )


def _repair_attempt_id(payload: dict) -> str:
    return hashlib.sha256(
        (
            "demo-repair|"
            + str(payload.get("run_id") or "")
            + "|"
            + str(payload.get("evidence_hash") or "")
            + "|"
            + DETERMINISTIC_SAFE_REVISION_VERSION
        ).encode("utf-8")
    ).hexdigest()[:24]


def _public_presentation_blockers(ticker: str, run, *, historical_report=None) -> list[str]:
    """Return public-rendering requirements in addition to freeze gates.

    A release can satisfy the generic integrity policy yet omit a first-class
    showcase calculation that the Decision and Evidence pages rely on.  This
    check prevents the newest such run from silently replacing a richer,
    previously validated demo.  Requirements are deliberately explicit per
    demo rather than inferred from prose or the presence of an action card.
    """

    normalized_ticker = ticker.upper()
    blockers: list[str] = []
    required_ontology = {
        "BABA": ("china-internet-platform-v1", "cloud_ai_investment_cycle"),
        "BLK": ("asset-manager-v1", "aum_fee_earnings_conversion"),
    }.get(normalized_ticker)
    if required_ontology:
        expected_profile, expected_mechanism = required_ontology
        ontology_version = str(getattr(run, "research_ontology_version", "") or "")
        profile_id = str(getattr(run, "security_profile_id", "") or "")
        mechanism_ids = set(getattr(run, "core_mechanism_ids", []) or [])
        if ontology_version != ONTOLOGY_SCHEMA_VERSION:
            blockers.append(
                f"{normalized_ticker} requires current archetype/mechanism validation "
                f"({ONTOLOGY_SCHEMA_VERSION}); found {ontology_version or 'legacy metadata'}."
            )
        if profile_id != expected_profile:
            blockers.append(
                f"{normalized_ticker} requires security profile {expected_profile}; "
                f"found {profile_id or 'unresolved'} ."
            )
        if expected_mechanism not in mechanism_ids:
            blockers.append(
                f"{normalized_ticker} requires mapped mechanism {expected_mechanism}."
            )
        invalid_dossiers = [
            item for item in list(getattr(historical_report, "dossiers", []) or [])
            if str(getattr(item, "scope_status", "legacy_unchecked") or "legacy_unchecked")
            not in {"in_scope"}
            or str(getattr(item, "eligibility_code", "legacy_unchecked") or "legacy_unchecked")
            not in {"eligible", "same_issuer"}
        ]
        if invalid_dossiers:
            blockers.append(
                "The public historical playbook contains a dossier that has not passed "
                "current issuer and mechanism scope validation."
            )
    if normalized_ticker != "AAPL":
        return blockers
    bridge = getattr(run, "cash_conversion_recurrence", None)
    validation = validate_cash_conversion_recurrence_bridge(bridge)
    if validation.valid:
        return []
    detail = "; ".join(validation.reasons[:2]) or "No bridge was saved."
    return blockers + [
        "AAPL public demo requires a validated two-period cash-conversion "
        f"bridge. {detail}"
    ]


def _bounded_freeze_report(
    ticker: str,
    decisions: list,
    *,
    output_path: Path,
    selection_policy: str,
    repair_checkpoints=(),
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
    repair_stages = [
        {
            "stage": item.stage,
            "state": item.state,
            "attempt_count": item.attempt_count,
            "failure_class": item.failure_class,
            "summary": item.summary,
        }
        for item in repair_checkpoints
    ]
    provider_action = next((
        {
            "stage": item.stage,
            "reason": (
                "Independent QA closure timed out before returning a decision."
                if item.stage == RepairStage.INDEPENDENT_CLOSURE.value
                and item.state == "deadline_exceeded"
                else "Independent QA closure was interrupted before returning a decision."
                if item.stage == RepairStage.INDEPENDENT_CLOSURE.value
                and item.state == "interrupted"
                else
                "Provider credit is required."
                if item.failure_class == "provider_credit_required"
                or "insufficient balance" in item.summary.casefold()
                else "Local Codex must run in the signed-in desktop user context."
                if "could not find home directory" in item.summary.casefold()
                or "error finding codex home" in item.summary.casefold()
                else "Provider authentication is required."
            ),
            "next_action": (
                "Retry only Independent QA closure with authenticated local "
                "Codex or another eligible auditor. The completed revision and "
                "deterministic validation will be reused; no DeepSeek research "
                "call is required."
                if item.stage == RepairStage.INDEPENDENT_CLOSURE.value
                and item.state in {"deadline_exceeded", "interrupted"}
                else
                "Add credit or select another eligible provider, then resume "
                "this saved repair. Completed stages will be reused."
                if item.failure_class == "provider_credit_required"
                or "insufficient balance" in item.summary.casefold()
                else (
                    "Run the displayed closure-resume command from the same "
                    "Windows account where Codex is authenticated. The completed "
                    "revision and validation will be reused; no DeepSeek call is required."
                )
                if "could not find home directory" in item.summary.casefold()
                or "error finding codex home" in item.summary.casefold()
                else (
                    "Reconnect the selected provider, then resume this saved "
                    "repair. Completed stages will be reused."
                )
            ),
        }
        for item in repair_checkpoints
        if item.state == "waiting_for_provider"
        or (
            item.stage == RepairStage.INDEPENDENT_CLOSURE.value
            and item.state in {"deadline_exceeded", "interrupted"}
        )
        or item.failure_class in {
            "provider_credit_required", "provider_authentication_required"
        }
        or "insufficient balance" in item.summary.casefold()
        or "could not find home directory" in item.summary.casefold()
        or "error finding codex home" in item.summary.casefold()
    ), None)
    if provider_action is not None and candidates:
        resumable_run_id = str(candidates[0].get("source_run_id") or "")
        if resumable_run_id:
            provider_action["resume_command"] = (
                ".venv\\Scripts\\python.exe scripts\\generate_hybrid_demo.py "
                f"--ticker {ticker} --resume-run-id {resumable_run_id} "
                + (
                    "--executor codex_local --retry-stage closure_checker"
                    if provider_action.get("stage")
                    == RepairStage.INDEPENDENT_CLOSURE.value
                    and any(
                        item.stage == RepairStage.INDEPENDENT_CLOSURE.value
                        and item.state in {"deadline_exceeded", "interrupted"}
                        for item in repair_checkpoints
                    )
                    else "--executor codex_local"
                )
            )
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
        "provider_action_required": provider_action,
        "repair_stages": repair_stages,
    }


def _write_success_report(
    ticker: str,
    decision,
    output_path: Path,
    *,
    run=None,
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
        "source_run_id": str(getattr(run, "run_id", "") or decision.source_run_id),
        "published_at": str(getattr(run, "published_at", "") or decision.evaluated_at),
        "evidence_hash": str(getattr(run, "evidence_hash", "") or decision.evidence_hash),
        "qa_closure_state": str(decision.qa_closure_state or ""),
        "research_ontology_version": str(
            getattr(run, "research_ontology_version", "") or ""
        ),
        "security_profile_id": str(
            getattr(run, "security_profile_id", "") or ""
        ),
        "core_mechanism_ids": list(
            getattr(run, "core_mechanism_ids", ()) or ()
        ),
        "sanitization_status": str(
            getattr(decision, "sanitization_status", "") or ""
        ),
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


def _resolve_openai_auditor_credentials(
    store: ResearchStore,
    manager: LocalSecretsManager,
) -> tuple[str, str]:
    """Resolve hosted QA through the same saved-profile boundary as the UI."""

    selection = store.get_llm_selection()
    selected_ids = tuple(dict.fromkeys(filter(None, (
        selection.get("secondary_profile_id"),
        selection.get("primary_profile_id"),
    ))))
    profiles = list(store.list_llm_profiles())
    ordered = [
        profile for profile_id in selected_ids
        for profile in profiles if profile.profile_id == profile_id
    ]
    ordered.extend(
        profile for profile in profiles
        if profile.profile_id not in selected_ids
    )
    for profile in ordered:
        if profile.provider_preset not in {"openai_sol", "openai"}:
            continue
        if not profile_supports_role(profile, "checker"):
            continue
        key = profile_api_key(profile, manager)
        if key:
            return key, "provider_vault"
    if config.OPENAI_API_KEY:
        return config.OPENAI_API_KEY, "fixed_environment"
    return "", "missing"


def _write_terminal_generator_failure(
    report_path: Path,
    *,
    ticker: str,
    output_path: Path,
    exc: Exception,
    repair_attempt_id: str,
    repair_repository,
) -> None:
    message = " ".join(str(exc or type(exc).__name__).split())[:800]
    for key_name in (
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
        "FMP_API_KEY",
        "WISBURG_API_KEY",
        "TIINGO_API_KEY",
        "EODHD_API_KEY",
    ):
        secret = os.getenv(key_name, "")
        if secret:
            message = message.replace(secret, "[redacted]")
    checkpoints = (
        repair_repository.list_for_attempt(repair_attempt_id)
        if repair_attempt_id else []
    )
    payload = {
        "ticker": ticker,
        "status": "generation_stopped_safely",
        "publication_decision": "not_published",
        "existing_public_artifact_preserved": output_path.exists(),
        "failure_class": type(exc).__name__,
        "readable_reason": message or type(exc).__name__,
        "repair_attempt_id": repair_attempt_id,
        "repair_stages": [
            {
                "stage": item.stage,
                "state": item.state,
                "attempt_count": item.attempt_count,
                "updated_at": item.updated_at,
                "failure_class": item.failure_class,
                "summary": item.summary,
            }
            for item in checkpoints
        ],
        "next_action": (
            "Resume this repair attempt after correcting the reported provider "
            "or configuration failure; completed stages will be reused."
        ),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = report_path.with_suffix(report_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(report_path)


def _write_hosted_auditor_setup_required(
    report_path: Path,
    *,
    ticker: str,
    output_path: Path,
    resume_run_id: str,
    repair_attempt_id: str,
    repair_repository,
) -> None:
    """Preserve the staged closure retry when hosted QA lacks a credential."""

    try:
        existing = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        existing = {}
    if not isinstance(existing, dict):
        existing = {}
    checkpoints = (
        repair_repository.list_for_attempt(repair_attempt_id)
        if repair_attempt_id else []
    )
    payload = {
        **existing,
        "ticker": ticker,
        "status": "blocked",
        "published_artifact_preserved": output_path.exists(),
        "provider_action_required": {
            "stage": RepairStage.INDEPENDENT_CLOSURE.value,
            "status": "Needs setup",
            "reason": "Hosted Independent QA is not connected.",
            "next_action": (
                "Save an eligible OpenAI Sol auditor profile or connect a "
                "session-only auditor credential, then retry closure. The "
                "completed revision and deterministic validation will be reused."
            ),
            "resume_command": (
                ".venv\\Scripts\\python.exe scripts\\generate_hybrid_demo.py "
                f"--ticker {ticker} --resume-run-id {resume_run_id} "
                "--executor openai_responses --retry-stage closure_checker"
            ),
        },
        "repair_stages": [
            {
                "stage": item.stage,
                "state": item.state,
                "attempt_count": item.attempt_count,
                "failure_class": item.failure_class,
                "summary": item.summary,
            }
            for item in checkpoints
        ] or list(existing.get("repair_stages") or []),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = report_path.with_suffix(report_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(report_path)


def _assert_no_secrets(serialized: str) -> None:
    if contains_credential_like_token(serialized):
        raise RuntimeError(
            "Refusing to freeze demo because a credential-like token was detected."
        )
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
