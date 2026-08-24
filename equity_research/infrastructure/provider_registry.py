from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .. import config
from ..hybrid_research import (
    CODEX_LOCAL_BACKEND,
    OPENAI_RESPONSES_BACKEND,
    CodexLocalProvider,
    OpenAIResponsesProvider,
    build_hybrid_execution_preflight,
)
from ..llm_vault import profile_to_provider
from ..models import (
    ExecutionBackend,
    HybridExecutionPreflight,
    LlmProviderProfile,
)
from ..thesis_synthesis import (
    OpenAICompatibleProvider,
    UnavailableLlmProvider,
    provider_from_config,
)


@dataclass(frozen=True)
class ProviderResolutionRequest:
    ticker: str
    research_profile: str
    primary_profile_id: str
    primary_profile: LlmProviderProfile | None
    secondary_profile: LlmProviderProfile | None
    secrets_manager: Any
    enable_llm: bool
    enable_secondary_review: bool
    enable_independent_qa: bool
    auditor_backend_id: str
    configured_secondary_available: bool
    local_codex_status: str
    primary_fallback_key: str = ""
    auditor_fallback_key: str = ""
    primary_credential_source: str = "missing"
    auditor_credential_source: str = "missing"


@dataclass(frozen=True)
class ResolvedResearchProviders:
    primary_synthesis: Any | None
    secondary_synthesis: Any | None
    primary_research: Any | None
    auditor: Any | None
    execution_backend: ExecutionBackend
    preflight: HybridExecutionPreflight
    qa_relationship: str


def resolve_research_providers(
    request: ProviderResolutionRequest,
) -> ResolvedResearchProviders:
    """Construct providers behind the infrastructure registry boundary."""
    primary_synthesis = profile_to_provider(
        request.primary_profile,
        request.secrets_manager,
        enabled=request.enable_llm,
    )
    if (
        request.enable_llm
        and request.primary_profile_id
        and primary_synthesis is None
    ):
        primary_synthesis = UnavailableLlmProvider(
            request.primary_profile.provider_preset
            if request.primary_profile else "selected_profile",
            request.primary_profile.model
            if request.primary_profile else "unknown",
            "Selected primary LLM profile is unavailable. Check its saved key and base URL.",
        )
    elif primary_synthesis is None:
        primary_synthesis = provider_from_config(enabled=request.enable_llm)

    secondary_synthesis = profile_to_provider(
        request.secondary_profile,
        request.secrets_manager,
        enabled=(
            request.enable_llm and request.enable_secondary_review
        ),
    )
    primary_research = profile_to_provider(
        request.primary_profile,
        request.secrets_manager,
        # Lead agent research is useful with or without an independent
        # auditor. QA controls the secondary roles, not whether the selected
        # primary model can use application-owned research tools.
        enabled=request.enable_llm,
    )
    if (
        primary_research is None
        and request.enable_llm
        and request.primary_fallback_key
    ):
        primary_research = OpenAICompatibleProvider(
            api_key=request.primary_fallback_key,
            model=config.HYBRID_DEEPSEEK_MODEL,
            base_url="https://api.deepseek.com",
            provider_name="deepseek",
            timeout_seconds=config.HYBRID_RESEARCH_TIMEOUT_SECONDS,
            max_tokens=config.HYBRID_DEEPSEEK_MAX_TOKENS,
            reasoning_effort=config.HYBRID_DEEPSEEK_REASONING_EFFORT,
            thinking_enabled=config.HYBRID_DEEPSEEK_THINKING,
        )

    auditor = None
    backend = OPENAI_RESPONSES_BACKEND
    if (
        request.enable_independent_qa
        and request.auditor_backend_id == "codex_local"
        and request.local_codex_status == "ready"
    ):
        auditor = CodexLocalProvider(
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
    elif (
        request.enable_independent_qa
        and request.auditor_backend_id == "configured_secondary"
        and request.configured_secondary_available
    ):
        auditor = profile_to_provider(
            request.secondary_profile,
            request.secrets_manager,
            enabled=True,
        )
    elif (
        request.enable_independent_qa
        and request.auditor_fallback_key
    ):
        auditor = OpenAIResponsesProvider(
            request.auditor_fallback_key,
            model=config.HYBRID_OPENAI_MODEL,
            reasoning_mode="pro",
            reasoning_effort=config.HYBRID_SOL_REASONING_EFFORT,
            max_output_tokens=config.HYBRID_SOL_MAX_OUTPUT_TOKENS,
            timeout_seconds=config.HYBRID_RESEARCH_TIMEOUT_SECONDS,
        )

    preflight = build_hybrid_execution_preflight(
        request.ticker,
        deepseek_provider=primary_research,
        sol_provider=auditor,
        research_profile=request.research_profile,
        deepseek_credential_source=request.primary_credential_source,
        sol_credential_source=request.auditor_credential_source,
        evidence_status="Pending deterministic preflight for this live run",
    )
    relationship = _provider_relationship(primary_research, auditor)
    return ResolvedResearchProviders(
        primary_synthesis=primary_synthesis,
        secondary_synthesis=secondary_synthesis,
        primary_research=primary_research,
        auditor=auditor,
        execution_backend=backend,
        preflight=preflight,
        qa_relationship=relationship,
    )


def _provider_relationship(primary: Any, auditor: Any) -> str:
    if auditor is None:
        return "not_run"
    primary_identity = _provider_identity(primary)
    auditor_identity = _provider_identity(auditor)
    return (
        "independent"
        if primary_identity != auditor_identity
        else "same-model second pass"
    )


def _provider_identity(provider: Any) -> tuple[str, str, str]:
    return (
        str(getattr(provider, "provider_name", "") or "").casefold(),
        str(getattr(provider, "base_url", "") or "")
        .casefold().rstrip("/"),
        str(getattr(provider, "model", "") or "").casefold(),
    )
