"""Application use cases coordinating research without UI dependencies."""

from .research_runs import (
    ResearchJobRepository,
    ResearchReadiness,
    ResearchRunCommand,
    ResearchRunEvent,
    ResearchRunService,
    ResearchRunSnapshot,
    ResearchRunner,
    RunStatus,
)
from .contracts import (
    audit_finding_from_payload,
    candidate_discovery_from_payload,
    closure_audit_from_payload,
    evidence_admission_from_payload,
    evidence_request_from_payload,
    primary_audit_response_from_payload,
    research_request_from_payload,
    run_manifest_from_payload,
)
from .research_requests import (
    ConnectorAttempt,
    RegisteredResearchConnector,
    ResearchRequestDispatcher,
    ResearchRequestExecution,
)
from .research_request_outcomes import (
    CashConversionOutcomeHandler,
    InvestigationAnswerState,
    ResearchIntentAnswer,
    ResearchIntentAnswerResolver,
    ResearchRequestAnswerStatus,
    ResearchRequestOutcome,
    ResearchRequestOutcomeHandler,
    ResearchRequestOutcomeService,
    build_research_request_outcome,
    research_request_topic,
)
from .cash_conversion import (
    CashConversionBridgeValidation,
    validate_cash_conversion_recurrence_bridge,
)
from .credentials import (
    CredentialLeaseError,
    CredentialLeaseReference,
    EphemeralCredentialBroker,
    InMemoryEphemeralCredentialBroker,
)
from .investigation_actions import (
    InvestigationActionCommand,
    InvestigationActionResult,
    InvestigationActionService,
    parse_event_price_csv,
    price_csv_template,
)
from .investigation_journey import (
    INVESTIGATION_STAGE_ORDER,
    InvestigationJourney,
    InvestigationOutcomeSummary,
    InvestigationStage,
    ResearchOutcomeDiff,
    advance_investigation_stage,
    build_investigation_journey,
    resolve_investigation_stage,
)
from .readiness import (
    ProviderConnectionStatus,
    build_provider_connection_statuses,
    preview_research_readiness,
)
from .research_launch import (
    ResearchParentReference,
    ResearchLaunchIntent,
    ResearchLaunchIntentStatus,
    ResearchLaunchResult,
    ResearchLaunchService,
    ResearchOperationKind,
    build_operation_fingerprint,
    build_research_launch_intent,
    resolve_research_parent_reference,
)
from .saved_research import (
    AdoptCompletedInMemoryResult,
    build_displayed_research_context,
    build_displayed_investigation_reference,
    DeleteSavedResearch,
    ListSavedResearch,
    LoadLatestCompletedResearch,
    LoadSavedResearch,
    PinSavedResearch,
    SavedResearchError,
    SavedResearchLoadError,
    SavedResearchService,
    SaveResearchResult,
    should_auto_load_saved_result,
)
from .publication_readiness import (
    DETERMINISTIC_SAFE_REVISION_VERSION,
    build_deterministic_safe_primary_revision,
    canonicalize_artifact_for_publication,
    record_analog_candidate_revision,
    CallableRepairStrategy,
    build_source_lineage_graph,
    DurableRepairJobRepository,
    InMemoryRepairJobRepository,
    PublicationGateEvaluator,
    RepairControllerResult,
    RepairExecutionContext,
    RepairExecutionResult,
    RepairStrategy,
    RepairStrategyRegistry,
    ResearchRequestRepairStrategy,
    ResearchRepairController,
    SemanticQualityScorer,
    compile_repair_actions,
    independent_origin_requirements,
    invalidate_derived_artifacts,
)
from .research_agenda import (
    DEFAULT_RESEARCH_QUESTIONS,
    RESEARCH_QUESTION_LIBRARY_VERSION,
    ResearchAgendaBuilder,
    ResearchQuestionLibrary,
    agenda_from_payload,
)
from .source_capabilities import (
    SourceCapabilityRegistry,
    build_source_capability_registry,
)
from .investigation_action_canonicalization import (
    CANONICAL_ACTION_IDENTITY_VERSION,
    CanonicalActionAliases,
    CanonicalInvestigationActionSet,
    InvestigationActionDisplayCollision,
    canonicalize_investigation_actions,
)
from .codex_quality_supervisor import (
    AuditBundleKind,
    CodexQualityRole,
    CodexQualitySupervisor,
    CodexQualityTriggerPolicy,
    CodexRoleReport,
    ConditionalSpecialistRequest,
    FindingCategory,
    FindingSeverity,
    FindingStatus,
    InMemoryCodexQualityRepository,
    QualityAuditBundleReference,
    QualityFinding,
    QualityRunStatus,
    QualityTrigger,
    QualityTriggerMode,
    RoleCheckpointStatus,
    RoleReportStatus,
    build_role_report,
)
from .repair_stage_execution import (
    REPAIR_STAGE_SCHEMA_VERSION,
    DeterministicSafeRevisionProvider,
    InMemoryRepairStageCheckpointRepository,
    RepairExecutionDeadlineExceeded,
    RepairExecutionError,
    RepairExecutionPolicy,
    RepairStage,
    RepairStageCheckpoint,
    RepairStageCheckpointRepository,
    RepairStageState,
    RepairStageSupervisor,
    SupervisedRepairProvider,
)
from .codex_execution import (
    CODEX_EXECUTION_TRANSPORT_VERSION,
    CodexExecutionEvent,
    CodexExecutionFailure,
    CodexExecutionPolicy,
    CodexExecutionResult,
    CodexExecutionRunner,
    CodexExecutionState,
)

__all__ = [
    "ResearchJobRepository",
    "ResearchReadiness",
    "ResearchRunCommand",
    "ResearchRunEvent",
    "ResearchRunService",
    "ResearchRunSnapshot",
    "ResearchRunner",
    "RunStatus",
    "REPAIR_STAGE_SCHEMA_VERSION",
    "InMemoryRepairStageCheckpointRepository",
    "RepairExecutionDeadlineExceeded",
    "RepairExecutionError",
    "RepairExecutionPolicy",
    "RepairStage",
    "RepairStageCheckpoint",
    "RepairStageCheckpointRepository",
    "RepairStageState",
    "RepairStageSupervisor",
    "SupervisedRepairProvider",
    "DeterministicSafeRevisionProvider",
    "CODEX_EXECUTION_TRANSPORT_VERSION",
    "CodexExecutionEvent",
    "CodexExecutionFailure",
    "CodexExecutionPolicy",
    "CodexExecutionResult",
    "CodexExecutionRunner",
    "CodexExecutionState",
    "CashConversionOutcomeHandler",
    "InvestigationAnswerState",
    "ResearchIntentAnswer",
    "ResearchIntentAnswerResolver",
    "ResearchRequestAnswerStatus",
    "ResearchRequestOutcome",
    "ResearchRequestOutcomeHandler",
    "ResearchRequestOutcomeService",
    "build_research_request_outcome",
    "research_request_topic",
    "CashConversionBridgeValidation",
    "validate_cash_conversion_recurrence_bridge",
    "CredentialLeaseError",
    "CredentialLeaseReference",
    "EphemeralCredentialBroker",
    "InMemoryEphemeralCredentialBroker",
    "InvestigationActionCommand",
    "InvestigationActionResult",
    "InvestigationActionService",
    "InvestigationJourney",
    "INVESTIGATION_STAGE_ORDER",
    "InvestigationOutcomeSummary",
    "InvestigationStage",
    "ResearchOutcomeDiff",
    "advance_investigation_stage",
    "build_investigation_journey",
    "resolve_investigation_stage",
    "ProviderConnectionStatus",
    "build_provider_connection_statuses",
    "parse_event_price_csv",
    "price_csv_template",
    "preview_research_readiness",
    "ResearchLaunchIntent",
    "ResearchParentReference",
    "ResearchLaunchIntentStatus",
    "ResearchLaunchResult",
    "ResearchLaunchService",
    "ResearchOperationKind",
    "build_operation_fingerprint",
    "build_research_launch_intent",
    "resolve_research_parent_reference",
    "AdoptCompletedInMemoryResult",
    "build_displayed_research_context",
    "build_displayed_investigation_reference",
    "DeleteSavedResearch",
    "ListSavedResearch",
    "LoadLatestCompletedResearch",
    "LoadSavedResearch",
    "PinSavedResearch",
    "SavedResearchError",
    "SavedResearchLoadError",
    "SavedResearchService",
    "SaveResearchResult",
    "should_auto_load_saved_result",
    "DurableRepairJobRepository",
    "CallableRepairStrategy",
    "canonicalize_artifact_for_publication",
    "DETERMINISTIC_SAFE_REVISION_VERSION",
    "build_deterministic_safe_primary_revision",
    "record_analog_candidate_revision",
    "build_source_lineage_graph",
    "InMemoryRepairJobRepository",
    "PublicationGateEvaluator",
    "RepairControllerResult",
    "RepairExecutionContext",
    "RepairExecutionResult",
    "RepairStrategy",
    "RepairStrategyRegistry",
    "ResearchRequestRepairStrategy",
    "ResearchRepairController",
    "SemanticQualityScorer",
    "compile_repair_actions",
    "independent_origin_requirements",
    "invalidate_derived_artifacts",
    "ResearchAgentRuntime",
    "agent_session_artifact_payload",
    "budget_for_research",
    "session_from_payload",
    "DEFAULT_RESEARCH_QUESTIONS",
    "RESEARCH_QUESTION_LIBRARY_VERSION",
    "ResearchAgendaBuilder",
    "ResearchQuestionLibrary",
    "agenda_from_payload",
    "ContextResearchToolGateway",
    "ResearchToolGateway",
    "SourceCapabilityRegistry",
    "build_source_capability_registry",
    "CANONICAL_ACTION_IDENTITY_VERSION",
    "CanonicalActionAliases",
    "CanonicalInvestigationActionSet",
    "InvestigationActionDisplayCollision",
    "canonicalize_investigation_actions",
    "AuditBundleKind",
    "CodexQualityRole",
    "CodexQualitySupervisor",
    "CodexQualityTriggerPolicy",
    "CodexRoleReport",
    "ConditionalSpecialistRequest",
    "FindingCategory",
    "FindingSeverity",
    "FindingStatus",
    "InMemoryCodexQualityRepository",
    "QualityAuditBundleReference",
    "QualityFinding",
    "QualityRunStatus",
    "QualityTrigger",
    "QualityTriggerMode",
    "RoleCheckpointStatus",
    "RoleReportStatus",
    "build_role_report",
    "CashConversionRecurrenceService",
    "DeterministicCashConversionRefreshService",
    "DeterministicEvidenceRefreshCommand",
    "DeterministicEvidenceRefreshResult",
    "SecCompanyFactsFinancialFactSeries",
    "cash_conversion_bridge_from_dict",
    "cash_conversion_intent_id",
]


_LAZY_AGENT_EXPORTS = {
    "ResearchAgentRuntime": (".agent_runtime", "ResearchAgentRuntime"),
    "agent_session_artifact_payload": (
        ".agent_runtime", "agent_session_artifact_payload"
    ),
    "budget_for_research": (".agent_runtime", "budget_for_research"),
    "session_from_payload": (".agent_runtime", "session_from_payload"),
    "ContextResearchToolGateway": (
        ".research_tools", "ContextResearchToolGateway"
    ),
    "ResearchToolGateway": (".research_tools", "ResearchToolGateway"),
    "CashConversionRecurrenceService": (
        ".cash_conversion", "CashConversionRecurrenceService"
    ),
    "DeterministicCashConversionRefreshService": (
        ".cash_conversion", "DeterministicCashConversionRefreshService"
    ),
    "DeterministicEvidenceRefreshCommand": (
        ".cash_conversion", "DeterministicEvidenceRefreshCommand"
    ),
    "DeterministicEvidenceRefreshResult": (
        ".cash_conversion", "DeterministicEvidenceRefreshResult"
    ),
    "SecCompanyFactsFinancialFactSeries": (
        ".cash_conversion", "SecCompanyFactsFinancialFactSeries"
    ),
    "cash_conversion_bridge_from_dict": (
        ".cash_conversion", "cash_conversion_bridge_from_dict"
    ),
    "cash_conversion_intent_id": (
        ".cash_conversion", "cash_conversion_intent_id"
    ),
}


def __getattr__(name: str):
    """Load gateway/runtime facades without creating evidence-layer cycles."""

    target = _LAZY_AGENT_EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    from importlib import import_module

    module_name, attribute = target
    value = getattr(import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value
