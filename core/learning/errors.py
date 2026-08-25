"""Closed browser-safe failure codes for Learning APIs."""

from enum import StrEnum


class LearningFailureCode(StrEnum):
    """Exhaustive public failure vocabulary for the Learning control plane."""

    REQUEST_INVALID = "learning_request_invalid"
    TASK_INVALID_ID = "learning_task_invalid_id"
    TASK_NOT_FOUND = "learning_task_not_found"
    TASK_REVISION_CONFLICT = "learning_task_revision_conflict"
    TASK_NOT_DRAFT = "learning_task_not_draft"
    TASK_NOT_READY = "learning_task_not_ready"
    TASK_SERVICE_UNAVAILABLE = "learning_task_service_unavailable"
    WORKSPACE_UNAVAILABLE = "learning_workspace_unavailable"

    ACTIVATION_CAPABILITY_CONFLICT = "learning_activation_capability_conflict"
    ACTIVATION_IDEMPOTENCY_CONFLICT = "activation_idempotency_conflict"
    ACTIVATION_CONFLICT = "learning_activation_conflict"
    ACTIVATION_CONTRACT_CONFLICT = "learning_activation_contract_conflict"
    ACTIVATION_CORRUPT = "learning_activation_corrupt"
    ACTIVATION_FAILED = "learning_activation_failed"
    ACTIVATION_GOAL_CONFLICT = "learning_activation_goal_conflict"
    ACTIVATION_NOT_ACTIVE = "learning_activation_not_active"
    ACTIVATION_NOT_FOUND = "learning_activation_not_found"
    ACTIVATION_PLAN_CONFLICT = "learning_activation_plan_conflict"
    ACTIVATION_SCOPE_MISMATCH = "learning_activation_scope_mismatch"
    ACTIVATION_SERVICE_UNAVAILABLE = "learning_activation_service_unavailable"
    ACTIVATION_SESSION_CONFLICT = "learning_activation_session_conflict"
    ACTIVATION_SOURCE_POLICY_CONFLICT = "learning_activation_source_policy_conflict"
    ACTIVATION_WORKSPACE_CONFLICT = "learning_activation_workspace_conflict"

    KICKOFF_ACTIVATION_REQUIRED = "learning_kickoff_activation_required"
    KICKOFF_BINDING_CONFLICT = "learning_kickoff_binding_conflict"
    KICKOFF_CONFLICT = "learning_kickoff_conflict"
    KICKOFF_CORRUPT = "learning_kickoff_corrupt"
    KICKOFF_DISPATCH_FAILED = "learning_kickoff_dispatch_failed"
    KICKOFF_IDEMPOTENCY_CONFLICT = "learning_kickoff_idempotency_conflict"
    KICKOFF_JOURNAL_CONFLICT = "learning_kickoff_journal_conflict"
    KICKOFF_NOT_FOUND = "learning_kickoff_not_found"
    KICKOFF_SERVICE_UNAVAILABLE = "learning_kickoff_service_unavailable"
    KICKOFF_SESSION_CONFLICT = "learning_kickoff_session_conflict"

    # Compatibility names retained for callers of LearningKickoffErrorCode.
    ACTIVATION_REQUIRED = KICKOFF_ACTIVATION_REQUIRED
    BINDING_CONFLICT = KICKOFF_BINDING_CONFLICT
    CONFLICT = KICKOFF_CONFLICT
    CORRUPT = KICKOFF_CORRUPT
    DISPATCH_FAILED = KICKOFF_DISPATCH_FAILED
    IDEMPOTENCY_CONFLICT = KICKOFF_IDEMPOTENCY_CONFLICT
    JOURNAL_CONFLICT = KICKOFF_JOURNAL_CONFLICT
    NOT_FOUND = KICKOFF_NOT_FOUND
    SERVICE_UNAVAILABLE = KICKOFF_SERVICE_UNAVAILABLE
    SESSION_CONFLICT = KICKOFF_SESSION_CONFLICT

    SCOPE_ALLOWLIST_MISMATCH = "learning_scope_allowlist_mismatch"
    SCOPE_CAPABILITY_REVISION_MISMATCH = "learning_scope_capability_revision_mismatch"
    SCOPE_CATALOG_REVISION_MISMATCH = "learning_scope_catalog_revision_mismatch"
    SCOPE_FUTURE_IDENTITY_PRESENT = "learning_scope_future_identity_present"
    SCOPE_NOT_ACTIVE = "learning_scope_not_active"
    SCOPE_PLAN_MISMATCH = "learning_scope_plan_mismatch"
    SCOPE_PLAN_MISSING = "learning_scope_plan_missing"
    SCOPE_REVISION_MISSING = "learning_scope_revision_missing"
    SCOPE_SERVICE_UNAVAILABLE = "learning_scope_service_unavailable"
    SCOPE_SESSION_INVALID = "learning_scope_session_invalid"
    SCOPE_SESSION_MISMATCH = "learning_scope_session_mismatch"
    SCOPE_SKILL_NOT_ACTIVATED = "learning_scope_skill_not_activated"
    SCOPE_SOURCE_GAP = "learning_scope_source_gap"
    SCOPE_SOURCE_POLICY_MISMATCH = "learning_scope_source_policy_mismatch"
    SCOPE_TASK_MISMATCH = "learning_scope_task_mismatch"
    SCOPE_TOOL_FORBIDDEN = "learning_scope_tool_forbidden"
    SCOPE_VALIDATION_FAILED = "learning_scope_validation_failed"
    SCOPE_WEB_FORBIDDEN = "learning_scope_web_forbidden"

    RESEARCH_BUDGET_EXHAUSTED = "learning_research_budget_exhausted"
    RESEARCH_CANCELLED = "learning_research_cancelled"
    RESEARCH_CAPABILITY_REVISION_CONFLICT = "learning_research_capability_revision_conflict"
    RESEARCH_CAPABILITY_UNAVAILABLE = "learning_research_capability_unavailable"
    RESEARCH_CONFLICT = "learning_research_conflict"
    RESEARCH_DOMAIN_FORBIDDEN = "learning_research_domain_forbidden"
    RESEARCH_FRESHNESS_UNVERIFIED = "learning_research_freshness_unverified"
    RESEARCH_NO_EVIDENCE = "learning_research_no_evidence"
    RESEARCH_POLICY_FORBIDDEN = "learning_research_policy_forbidden"
    RESEARCH_PROVIDER_UNAVAILABLE = "learning_research_provider_unavailable"
    RESEARCH_STEP_BUDGET_EXHAUSTED = "learning_research_step_budget_exhausted"
    RESEARCH_TIMEOUT = "learning_research_timeout"

    ARTIFACT_CONTRACT_CONFLICT = "learning_artifact_contract_conflict"
    ARTIFACT_NOT_FOUND = "learning_artifact_not_found"
    ARTIFACT_STORE_ERROR = "learning_artifact_store_error"
    ARTIFACT_STORE_UNAVAILABLE = "learning_artifact_store_unavailable"
    RUNTIME_REHYDRATE_FAILED = "learning_runtime_rehydrate_failed"
    PERSISTENCE_INTEGRITY_ERROR = "learning_persistence_integrity_error"
    RESUME_CHECKPOINT_CONFLICT = "learning_resume_checkpoint_conflict"
    RESUME_FENCING_CONFLICT = "learning_resume_fencing_conflict"
    RESUME_NOT_FOUND = "learning_resume_not_found"
    RESUME_REVISION_CONFLICT = "learning_resume_revision_conflict"
    RESUME_VALIDATION_FAILED = "learning_resume_validation_failed"


__all__ = ["LearningFailureCode"]
