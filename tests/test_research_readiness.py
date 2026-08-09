from equity_research.application.readiness import preview_research_readiness
from equity_research.models import (
    HybridExecutionPreflight,
    HybridProviderReadiness,
)


def _role(role: str, status: str, model: str = "") -> HybridProviderReadiness:
    return HybridProviderReadiness(
        role=role,
        provider="provider" if model else "",
        model=model,
        status=status,
    )


def test_missing_optional_models_never_disable_deterministic_research() -> None:
    readiness = preview_research_readiness(
        HybridExecutionPreflight(
            ticker="AAPL",
            status="blocked",
            research_profile="adaptive_ic",
            deepseek=_role("primary", "missing"),
            sol=_role("auditor", "missing"),
            blockers=["Missing primary model", "Missing auditor model"],
        ),
        deterministic_available=True,
        independent_qa_requested=True,
    )

    assert readiness.run_allowed is True
    assert "Deterministic research remains available" in readiness.primary_status
    assert "Optional" in readiness.independent_qa_status
    assert readiness.blockers == ()
    assert readiness.qa_requested is True
    assert readiness.qa_resolved is False


def test_same_model_relationship_is_transparent_without_blocking_run() -> None:
    readiness = preview_research_readiness(
        HybridExecutionPreflight(
            ticker="AAPL",
            status="ready",
            research_profile="deep_initiation",
            deepseek=_role("primary", "ready", "shared-model"),
            sol=_role("auditor", "ready", "shared-model"),
        ),
        deterministic_available=True,
        independent_qa_requested=True,
        qa_relationship="same-model second pass",
    )

    assert readiness.run_allowed is True
    assert readiness.execution_status == "Ready to start"
    assert readiness.qa_resolved is True
    assert readiness.qa_relationship == "same-model second pass"
    assert any("second pass" in notice for notice in readiness.notices)
