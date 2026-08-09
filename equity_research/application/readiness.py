from __future__ import annotations

from dataclasses import dataclass

from ..models import HybridExecutionPreflight
from .research_runs import ResearchReadiness


@dataclass(frozen=True)
class ProviderConnectionStatus:
    connection_id: str
    label: str
    status: str
    source: str
    message: str = ""
    required: bool = False
    action: str = ""


def build_provider_connection_statuses(
    readiness: ResearchReadiness,
    *,
    primary_source: str,
    auditor_source: str,
    independent_qa_requested: bool,
    required_source_summary: str,
    required_sources_ready: bool = True,
) -> tuple[ProviderConnectionStatus, ...]:
    """Describe the resolved next-run plan rather than unused secret slots."""
    primary_ready = readiness.primary_status.startswith("Ready")
    auditor_ready = readiness.independent_qa_status.startswith("Ready")
    auditor_connectable = (
        independent_qa_requested
        and "connect" in str(auditor_source or "").casefold()
    )
    return (
        ProviderConnectionStatus(
            connection_id="primary_research_ai",
            label="Primary Research AI",
            status="Ready" if primary_ready else "Needs setup",
            source=primary_source if primary_ready else "Not resolved",
            message=(
                readiness.primary_status
                if primary_ready
                else "Deterministic research remains available."
            ),
            required=False,
            action="Configure primary provider" if not primary_ready else "",
        ),
        ProviderConnectionStatus(
            connection_id="independent_research_qa",
            label="Independent Research QA",
            status=(
                "Ready" if auditor_ready else
                "Connect once" if auditor_connectable else
                "Needs setup" if independent_qa_requested else
                "Optional"
            ),
            source=(
                auditor_source if auditor_ready or auditor_connectable
                else "Not requested" if not independent_qa_requested
                else "Not resolved"
            ),
            message=readiness.independent_qa_status,
            required=independent_qa_requested,
            action=(
                "Connect local Codex" if auditor_connectable else
                "Configure auditor" if independent_qa_requested and not auditor_ready
                else ""
            ),
        ),
        ProviderConnectionStatus(
            connection_id="required_research_sources",
            label="Required research sources",
            status="Ready" if required_sources_ready else "Unavailable",
            source=required_source_summary,
            message=(
                "Configured and entitled sources are eligible for the next run."
                if required_sources_ready else
                "One or more required deterministic source adapters are unavailable."
            ),
            required=True,
            action="Check source diagnostics" if not required_sources_ready else "",
        ),
    )


def preview_research_readiness(
    preflight: HybridExecutionPreflight,
    *,
    deterministic_available: bool = True,
    independent_qa_requested: bool = False,
    qa_relationship: str = "not_run",
) -> ResearchReadiness:
    """Return user-facing readiness without allowing optional AI to block a run."""

    primary_ready = preflight.deepseek.status == "ready"
    auditor_ready = preflight.sol.status == "ready"
    blockers = () if deterministic_available else (
        "Deterministic research dependencies are unavailable.",
    )
    notices: list[str] = []
    if independent_qa_requested and not primary_ready:
        notices.append(
            "Primary Research AI is not configured; deterministic research remains available."
        )
    if independent_qa_requested and primary_ready and not auditor_ready:
        notices.append(
            "Independent QA will be skipped because an auditor is not available."
        )
    if qa_relationship == "same-model second pass" and auditor_ready:
        notices.append(
            "The selected reviewer is the same provider/model and is labeled as a second pass, not independent QA."
        )

    execution_status = (
        "Ready to start"
        if deterministic_available and (not independent_qa_requested or primary_ready)
        else "Deterministic research ready; optional AI stages unavailable"
        if deterministic_available
        else "Blocked"
    )
    return ResearchReadiness(
        run_allowed=deterministic_available,
        deterministic_status="Ready" if deterministic_available else "Unavailable",
        primary_status=(
            f"Ready | {_identity(preflight.deepseek.model, preflight.deepseek.provider)}"
            if primary_ready
            else "Not configured | Deterministic research remains available"
        ),
        independent_qa_status=(
            f"Ready | {_identity(preflight.sol.model, preflight.sol.provider)}"
            if auditor_ready
            else "Optional | Independent QA will be skipped"
        ),
        evidence_status="Deterministic preflight runs when research starts",
        execution_status=execution_status,
        primary_provider=preflight.deepseek.provider,
        primary_model=preflight.deepseek.model,
        auditor_provider=preflight.sol.provider,
        auditor_model=preflight.sol.model,
        qa_requested=independent_qa_requested,
        qa_resolved=bool(
            independent_qa_requested and primary_ready and auditor_ready
        ),
        qa_relationship=qa_relationship,
        notices=tuple(notices),
        blockers=blockers,
    )


def _identity(model: str, provider: str) -> str:
    return model or provider or "Configured model"
