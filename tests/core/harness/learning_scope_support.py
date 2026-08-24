from __future__ import annotations

from pathlib import Path

from core.coding.memory import workspace_id_from_path
from core.coding.persistence import CodingSessionStore
from core.harness.learning_scope import LearningReadonlyScope, LearningReadonlyScopeResolver
from core.learning import (
    LearningActivationService,
    LearningSourcePolicy,
    LearningTaskCreate,
    LearningTaskRepository,
    LearningTaskService,
)
from core.learning.runtime_resources import SageLearningActivationResources


def make_scope(
    *,
    allowed: tuple[str, ...] = (
        "local:knowledge_search",
        "subagent:research",
        "web:fetch",
        "web:search",
    ),
    web_policy: str = "allowed_when_insufficient",
    knowledge_policy: str = "preferred",
) -> LearningReadonlyScope:
    return LearningReadonlyScope(
        task_id="ltask_scope",
        task_revision=1,
        session_id="session_scope",
        owner_id="local",
        workspace_id="workspace_scope",
        turn_context_plan_id="turnplan_scope",
        turn_context_plan_hash="sha256:plan-scope",
        catalog_revision="catalog-v1",
        capability_revision="lcap-v1",
        allowed_capabilities=allowed,
        source_policy_revision="lsrc-v1",
        knowledge_policy=knowledge_policy,
        web_policy=web_policy,
        domains=(),
        freshness="all",
    )


def make_real_scope(
    tmp_path: Path,
) -> tuple[
    LearningReadonlyScope,
    LearningReadonlyScopeResolver,
    dict[str, object],
    Path,
    str,
]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    storage = tmp_path / ".coding"
    repository = LearningTaskRepository(storage / "learning-tasks.sqlite3")
    resources = SageLearningActivationResources(
        storage_root=storage,
        workspace_root=workspace,
        runtime_profile="deerflow_v2",
        sandbox_provider="local_workspace",
        sandbox_image="python:3.11-slim",
        knowledge_available=True,
        web_search_available=True,
        web_fetch_available=True,
    )
    tasks = LearningTaskService(repository)
    activation_service = LearningActivationService(repository, resources)
    task = tasks.create_draft(
        owner_id="local",
        workspace_id=workspace_id_from_path(workspace),
        request=LearningTaskCreate(
            topic="系统学习源码，不要联网",
            desired_outcome="能够解释关键调用链",
            starting_level="beginner",
            time_budget_minutes_per_week=180,
            source_policy=LearningSourcePolicy(web="forbidden"),
        ),
    )
    activation = activation_service.activate(
        owner_id="local",
        workspace_id=task.workspace_id,
        task_id=task.task_id,
        expected_revision=task.task_revision,
        idempotency_key="learning-scope-v1",
    )
    session = CodingSessionStore(storage / "sessions").load(activation.session_id)
    resolver = LearningReadonlyScopeResolver(repository, resources)
    scope = resolver.resolve_runtime_session(
        session,
        owner_id="local",
        workspace_id=task.workspace_id,
    )
    assert scope is not None
    return scope, resolver, session, storage, activation.kickoff_run_id
