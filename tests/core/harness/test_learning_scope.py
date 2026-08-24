"""首轮 Learning Turn 的只读 catalog 与执行前门禁合同。"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.prebuilt.tool_node import ToolCallRequest

from core.coding.memory import workspace_id_from_path
from core.coding.persistence import CodingSessionStore, TurnPlanStore
from core.coding.skills import SkillLifecycleSnapshot
from core.harness.learning_scope import (
    LearningReadonlyScope,
    LearningReadonlyScopeResolver,
    LearningScopeConflict,
    LearningScopeMiddleware,
)
from core.learning import (
    LearningActivationService,
    LearningSourcePolicy,
    LearningTaskCreate,
    LearningTaskRepository,
    LearningTaskService,
)
from core.learning.runtime_resources import SageLearningActivationResources


def _scope(
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


def _tools() -> list[StructuredTool]:
    return [
        StructuredTool.from_function(
            func=lambda query: query,
            name="knowledge_search",
            description="Search Knowledge",
            metadata={"capability_id": "local:knowledge_search"},
        ),
        StructuredTool.from_function(
            func=lambda query: query,
            name="search_web",
            description="Search Web",
            metadata={"capability_id": "web:search"},
        ),
        StructuredTool.from_function(
            func=lambda url: url,
            name="fetch_web",
            description="Fetch Web",
            metadata={"capability_id": "web:fetch"},
        ),
        StructuredTool.from_function(
            func=lambda subagent_type: subagent_type,
            name="task",
            description="Launch child",
            metadata={"capability_id": "subagent:explore"},
        ),
        StructuredTool.from_function(
            func=lambda command: command,
            name="run_shell",
            description="Run shell",
            metadata={"capability_id": "local:run_shell"},
        ),
        StructuredTool.from_function(
            func=lambda fact: fact,
            name="remember",
            description="Write memory",
            metadata={"capability_id": "local:remember"},
        ),
        StructuredTool.from_function(
            func=lambda query: query,
            name="tool_search",
            description="Discover deferred tools",
        ),
    ]


def _model_request() -> ModelRequest:
    return ModelRequest(
        model=FakeMessagesListChatModel(responses=[AIMessage(content="ok")]),
        messages=[HumanMessage(content="learn")],
        tools=_tools(),
    )


def _tool_request(name: str, args: dict[str, object]) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": name, "args": args, "id": "call-scope"},
        tool=None,
        state={"messages": [HumanMessage(content="learn")]},
        runtime=MagicMock(),
    )


def test_learning_scope_filters_model_catalog_to_frozen_readonly_capabilities() -> None:
    middleware = LearningScopeMiddleware(
        _scope(),
        revalidate=lambda: _scope(),
    )
    captured: list[ModelRequest] = []

    middleware.wrap_model_call(
        _model_request(),
        lambda request: captured.append(request) or ModelResponse(result=[AIMessage(content="ok")]),
    )

    assert [tool.name for tool in captured[0].tools] == [
        "knowledge_search",
        "task",
    ]
    assert "run_shell" not in [tool.name for tool in captured[0].tools]
    assert "remember" not in [tool.name for tool in captured[0].tools]


def test_learning_scope_allows_direct_web_only_when_knowledge_is_disabled() -> None:
    middleware = LearningScopeMiddleware(
        _scope(
            allowed=("subagent:research", "web:fetch", "web:search"),
            knowledge_policy="disabled",
        ),
        revalidate=lambda: _scope(
            allowed=("subagent:research", "web:fetch", "web:search"),
            knowledge_policy="disabled",
        ),
    )
    captured: list[ModelRequest] = []

    middleware.wrap_model_call(
        _model_request(),
        lambda request: captured.append(request) or ModelResponse(result=[AIMessage(content="ok")]),
    )

    assert [tool.name for tool in captured[0].tools] == [
        "search_web",
        "fetch_web",
        "task",
        "tool_search",
    ]


def test_learning_scope_constrains_retrieval_sources_to_executable_policy() -> None:
    knowledge_first = _scope(
        allowed=("local:knowledge_search", "web:search"),
        knowledge_policy="preferred",
        web_policy="allowed_when_insufficient",
    )
    direct_web = _scope(
        allowed=("web:search",),
        knowledge_policy="disabled",
        web_policy="allowed_when_insufficient",
    )

    assert knowledge_first.constrain_retrieval_sources(
        ("web",),
        available=("knowledge", "web"),
    ) == ("knowledge",)
    assert direct_web.constrain_retrieval_sources(
        ("knowledge", "web"),
        available=("knowledge", "web"),
    ) == ("web",)


def test_web_forbidden_scope_rejects_frozen_web_or_research_capabilities() -> None:
    with pytest.raises(LearningScopeConflict) as caught:
        _scope(web_policy="forbidden")

    assert caught.value.code == "learning_scope_web_forbidden"
    assert caught.value.status_code == 409


@pytest.mark.parametrize(
    ("tool_name", "args"),
    [
        ("run_shell", {"command": "cat /private/source-secret"}),
        ("remember", {"fact": "SECRET_TOKEN_SENTINEL"}),
        ("task", {"subagent_type": "practice", "prompt": "SKILL_PROMPT_SENTINEL"}),
    ],
)
def test_forged_or_writing_tool_call_is_denied_before_handler(
    tool_name: str,
    args: dict[str, object],
) -> None:
    middleware = LearningScopeMiddleware(_scope(), revalidate=lambda: _scope())
    handler = MagicMock(return_value=ToolMessage(content="executed", tool_call_id="call-scope"))

    result = middleware.wrap_tool_call(_tool_request(tool_name, args), handler)

    handler.assert_not_called()
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert result.content == "learning_scope_tool_forbidden"
    receipt = result.additional_kwargs["sage_learning_scope"]
    assert receipt == {
        "task_id": "ltask_scope",
        "status": "denied",
        "reason_code": "learning_scope_tool_forbidden",
    }
    serialized = str(result)
    assert "SECRET_TOKEN_SENTINEL" not in serialized
    assert "/private/source-secret" not in serialized
    assert "SKILL_PROMPT_SENTINEL" not in serialized


def test_research_child_is_allowed_but_practice_child_is_not() -> None:
    middleware = LearningScopeMiddleware(_scope(), revalidate=lambda: _scope())
    handler = MagicMock(return_value=ToolMessage(content="executed", tool_call_id="call-scope"))

    result = middleware.wrap_tool_call(
        _tool_request("task", {"subagent_type": "research", "prompt": "bounded"}),
        handler,
    )

    assert isinstance(result, ToolMessage)
    handler.assert_called_once()


@pytest.mark.parametrize(
    ("drifted", "reason_code"),
    [
        (
            {"capability_revision": "lcap-v2"},
            "learning_scope_capability_revision_mismatch",
        ),
        (
            {"catalog_revision": "catalog-v2"},
            "learning_scope_catalog_revision_mismatch",
        ),
        (
            {"turn_context_plan_hash": "sha256:plan-v2"},
            "learning_scope_plan_mismatch",
        ),
    ],
)
def test_execution_revalidates_canonical_scope_before_handler(
    drifted: dict[str, object],
    reason_code: str,
) -> None:
    frozen = _scope()
    current = replace(frozen, **drifted)
    middleware = LearningScopeMiddleware(frozen, revalidate=lambda: current)
    handler = MagicMock(return_value=ToolMessage(content="executed", tool_call_id="call-scope"))

    result = middleware.wrap_tool_call(
        _tool_request("knowledge_search", {"query": "PRIVATE_QUERY_SENTINEL"}),
        handler,
    )

    handler.assert_not_called()
    assert isinstance(result, ToolMessage)
    assert result.content == reason_code
    assert result.additional_kwargs["sage_learning_scope"]["reason_code"] == reason_code
    assert "PRIVATE_QUERY_SENTINEL" not in str(result)


def test_model_catalog_revalidates_canonical_scope_before_provider() -> None:
    frozen = _scope()
    current = replace(frozen, capability_revision="lcap-v2")
    middleware = LearningScopeMiddleware(frozen, revalidate=lambda: current)
    handler = MagicMock(return_value=ModelResponse(result=[AIMessage(content="unsafe")]))

    with pytest.raises(LearningScopeConflict) as caught:
        middleware.wrap_model_call(_model_request(), handler)

    handler.assert_not_called()
    assert caught.value.code == "learning_scope_capability_revision_mismatch"


def test_skill_capability_requires_an_exact_active_lifecycle() -> None:
    scope = _scope(allowed=("local:knowledge_search", "skill:builtin:review"))
    inactive = SkillLifecycleSnapshot(catalog_revision="skills-v1")

    assert scope.skill_allowed_tool_names(inactive, skill_capability_id=None) == frozenset()

    active = SkillLifecycleSnapshot(
        catalog_revision="skills-v1",
        activation_ref="skill://builtin/review",
        activation_revision="review-v1",
        allowed_tools=("knowledge_search",),
    )
    assert scope.skill_allowed_tool_names(
        active,
        skill_capability_id="skill:builtin:review",
    ) == frozenset({"knowledge_search"})

    with pytest.raises(LearningScopeConflict) as caught:
        scope.skill_allowed_tool_names(active, skill_capability_id=None)
    assert caught.value.code == "learning_scope_skill_not_activated"


def _real_scope(
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


def test_real_l0_receipt_resolves_to_web_forbidden_readonly_scope(tmp_path: Path) -> None:
    scope, resolver, session, _, _ = _real_scope(tmp_path)

    assert scope.allowed_capabilities == (
        "local:evidence_read",
        "local:knowledge_search",
        "local:memory_read",
    )
    assert scope.web_policy == "forbidden"
    assert resolver.revalidate(scope) == scope
    assert session["learning_task_id"] == scope.task_id


def test_real_plan_tamper_is_denied_before_tool_handler(tmp_path: Path) -> None:
    scope, resolver, _, storage, kickoff_run_id = _real_scope(tmp_path)
    plan_store = TurnPlanStore(storage, scope.session_id)
    plan = plan_store.load_for_run(kickoff_run_id)
    assert plan is not None
    with sqlite3.connect(plan_store.path) as connection:
        connection.execute(
            "UPDATE turn_context_plans SET plan_hash = ? WHERE run_id = ?",
            ("sha256:tampered-plan", kickoff_run_id),
        )
        connection.commit()
    middleware = LearningScopeMiddleware(scope, revalidate=lambda: resolver.revalidate(scope))
    handler = MagicMock(return_value=ToolMessage(content="executed", tool_call_id="call-scope"))

    result = middleware.wrap_tool_call(
        _tool_request("knowledge_search", {"query": "PRIVATE_QUERY_SENTINEL"}),
        handler,
    )

    handler.assert_not_called()
    assert isinstance(result, ToolMessage)
    assert result.content == "learning_scope_validation_failed"
    assert "PRIVATE_QUERY_SENTINEL" not in str(result)
