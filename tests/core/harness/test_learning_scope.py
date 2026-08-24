"""首轮 Learning Turn 的只读 catalog 与执行前门禁合同。"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.prebuilt.tool_node import ToolCallRequest
from sage_harness.runtime.events import HarnessStreamItem

from api.coding import (
    _deerflow_timeline_events,
    _learning_run_detail_payload,
    _learning_run_summary_payload,
    _learning_scope_failure_events,
    _learning_workspace_diff_payload,
    _post_turn_goal_followup,
    _runtime_timeline_events,
)
from core.coding.memory import workspace_id_from_path
from core.coding.persistence import CodingSessionStore, TurnPlanStore
from core.coding.persistence.session_event_journal import SessionEventJournal
from core.coding.run_coordinator import RunEvent
from core.coding.runtime import CodingRuntime
from core.coding.skills import SkillLifecycleSnapshot
from core.harness.event_adapter import HarnessEventAdapter
from core.harness.learning_public import LearningPublicProjector
from core.harness.learning_scope import (
    LearningReadonlyScope,
    LearningReadonlyScopeResolver,
    LearningScopeConflict,
    LearningScopeMiddleware,
)
from core.harness.thread_goal import ThreadGoalService
from core.harness.tools_adapter import build_deerflow_coding_tool_bundle
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


def test_learning_tool_bundle_snapshot_contains_only_frozen_visible_capabilities(
    tmp_path: Path,
) -> None:
    runtime = CodingRuntime(
        session_id="session_scope",
        workspace_root=tmp_path,
        model=object(),
        storage_root=tmp_path / ".coding",
        runtime_profile="deerflow_v2",
    )
    knowledge_port = MagicMock()
    knowledge_port.available = True
    knowledge_port.workspace_id = "workspace_scope"
    scope = _scope(
        allowed=("local:knowledge_search",),
        web_policy="forbidden",
    )

    bundle = build_deerflow_coding_tool_bundle(
        runtime,
        run_id="run_scope",
        knowledge_port=knowledge_port,
        learning_scope=scope,
    )

    assert bundle.capability_revision == "lcap-v1"
    assert bundle.capability_ids_by_tool_name == {"knowledge_search": "local:knowledge_search"}
    assert bundle.snapshot.resident_ids == ("local:knowledge_search",)
    assert bundle.snapshot.deferred_ids == ()
    assert bundle.deferred_setup.enabled is False
    assert {tool.name for tool in bundle.tools} == {"knowledge_search"}


@pytest.mark.asyncio
async def test_required_knowledge_gap_stops_before_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = CodingRuntime(
        session_id="session_scope",
        workspace_root=workspace,
        model=object(),
        storage_root=tmp_path / ".coding",
        runtime_profile="deerflow_v2",
    )
    scope = _scope(
        allowed=("local:knowledge_search",),
        web_policy="forbidden",
        knowledge_policy="required",
    )
    provider = MagicMock(side_effect=AssertionError("provider must not be constructed"))
    monkeypatch.setattr("api.coding.SageHarnessRuntimeAdapter", provider)

    events = [
        event
        async for event in _deerflow_timeline_events(
            runtime,
            content="请只用知识库解释源码调用链 PRIVATE_QUERY_SENTINEL",
            run_id="run-source-gap",
            surface_context=None,
            thread_goal=None,
            checkpointer=object(),
            learning_scope=scope,
            learning_scope_resolver=SimpleNamespace(revalidate=lambda frozen: frozen),
        )
    ]

    provider.assert_not_called()
    assert [event.payload.get("reason_code") for event in events[-2:]] == [
        "learning_scope_source_gap",
        "learning_scope_source_gap",
    ]
    assert "PRIVATE_QUERY_SENTINEL" not in str(events)
    assert events[0].payload["type"] == "learning_user_turn"


@pytest.mark.asyncio
async def test_learning_mcp_catalog_is_never_read_and_emits_fixed_blocked_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingCatalog:
        calls = 0

        async def snapshot(self) -> object:
            self.calls += 1
            raise AssertionError("Learning must not read MCP catalog")

    class CompletingAdapter:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        async def stream_turn(self, **kwargs: object):  # type: ignore[no-untyped-def]
            del kwargs
            yield RunEvent(
                kind="assistant",
                status="completed",
                payload=LearningPublicProjector.model_output_receipt(
                    task_id="ltask_scope",
                    run_id="run-mcp-blocked",
                ),
            )

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = CodingRuntime(
        session_id="session_scope",
        workspace_root=workspace,
        model=object(),
        storage_root=tmp_path / ".coding",
        runtime_profile="deerflow_v2",
    )
    scope = _scope(
        allowed=("local:memory_read",),
        web_policy="forbidden",
        knowledge_policy="disabled",
    )
    catalog = FailingCatalog()
    monkeypatch.setattr("api.coding.SageHarnessRuntimeAdapter", CompletingAdapter)

    events = [
        event
        async for event in _deerflow_timeline_events(
            runtime,
            content="复习已有记忆",
            run_id="run-mcp-blocked",
            surface_context=None,
            thread_goal=None,
            checkpointer=object(),
            mcp_catalog=catalog,  # type: ignore[arg-type]
            learning_scope=scope,
            learning_scope_resolver=SimpleNamespace(revalidate=lambda frozen: frozen),
        )
    ]

    assert catalog.calls == 0
    blocked = next(
        event.payload for event in events if event.payload.get("type") == "learning_mcp_blocked"
    )
    assert blocked == {
        "type": "learning_mcp_blocked",
        "task_id": "ltask_scope",
        "run_id": "run-mcp-blocked",
        "status": "blocked",
        "reason_code": "learning_scope_mcp_forbidden",
        "server_count": 0,
        "tool_count": 0,
    }


@pytest.mark.asyncio
async def test_model_boundary_scope_conflict_keeps_specific_public_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DriftingAdapter:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        async def stream_turn(self, **kwargs: object):  # type: ignore[no-untyped-def]
            del kwargs
            raise LearningScopeConflict("learning_scope_catalog_revision_mismatch")
            yield  # pragma: no cover

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = CodingRuntime(
        session_id="session_scope",
        workspace_root=workspace,
        model=object(),
        storage_root=tmp_path / ".coding",
        runtime_profile="deerflow_v2",
    )
    scope = _scope(
        allowed=("local:memory_read",),
        web_policy="forbidden",
        knowledge_policy="disabled",
    )
    monkeypatch.setattr("api.coding.SageHarnessRuntimeAdapter", DriftingAdapter)

    events = [
        event
        async for event in _deerflow_timeline_events(
            runtime,
            content="复习已有记忆",
            run_id="run-model-drift",
            surface_context=None,
            thread_goal=None,
            checkpointer=object(),
            learning_scope=scope,
            learning_scope_resolver=SimpleNamespace(revalidate=lambda frozen: frozen),
        )
    ]

    assert [event.payload.get("reason_code") for event in events[-2:]] == [
        "learning_scope_catalog_revision_mismatch",
        "learning_scope_catalog_revision_mismatch",
    ]
    assert events[-1].payload["error_type"] == "learning_scope_conflict"


@pytest.mark.asyncio
async def test_learning_graph_without_public_output_receipt_is_not_completed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class EmptyAdapter:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        async def stream_turn(self, **kwargs: object):  # type: ignore[no-untyped-def]
            del kwargs
            if False:
                yield

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = CodingRuntime(
        session_id="session_scope",
        workspace_root=workspace,
        model=object(),
        storage_root=tmp_path / ".coding",
        runtime_profile="deerflow_v2",
    )
    scope = _scope(
        allowed=("local:memory_read",),
        web_policy="forbidden",
        knowledge_policy="disabled",
    )
    monkeypatch.setattr("api.coding.SageHarnessRuntimeAdapter", EmptyAdapter)

    with pytest.raises(RuntimeError, match="without a public output receipt"):
        _ = [
            event
            async for event in _deerflow_timeline_events(
                runtime,
                content="复习已有记忆",
                run_id="run-empty-output",
                surface_context=None,
                thread_goal=None,
                checkpointer=object(),
                learning_scope=scope,
                learning_scope_resolver=SimpleNamespace(revalidate=lambda frozen: frozen),
            )
        ]


@pytest.mark.asyncio
async def test_learning_session_cannot_bypass_scope_through_legacy_runtime(
    tmp_path: Path,
) -> None:
    runtime = CodingRuntime(
        session_id="session_scope",
        workspace_root=tmp_path,
        model=object(),
        storage_root=tmp_path / ".coding",
        session_state={
            "id": "session_scope",
            "workspace_root": str(tmp_path),
            "runtime_profile": "legacy",
            "session_kind": "learning",
            "learning_task_id": "ltask_scope",
        },
        runtime_profile="legacy",
    )

    events = [
        event
        async for event in _runtime_timeline_events(
            runtime,
            content="PRIVATE_QUERY_SENTINEL",
            skill_prompt=None,
            command="",
            arguments="",
            run_id="run_scope",
            surface_context=None,
        )
    ]

    assert len(events) == 2
    assert events[0].payload == {
        "type": "learning_scope_rejected",
        "version": 1,
        "run_id": "run_scope",
        "status": "denied",
        "reason_code": "learning_scope_runtime_profile_unsupported",
    }
    assert events[-1].payload["runtime_profile"] == "legacy"
    assert "PRIVATE_QUERY_SENTINEL" not in str(events)


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


@pytest.mark.parametrize(
    ("policy_aware", "expects_fetch"),
    [(False, False), (True, True)],
)
def test_domain_frozen_activation_grants_fetch_only_to_policy_aware_port(
    tmp_path: Path,
    policy_aware: bool,
    expects_fetch: bool,
) -> None:
    case_root = tmp_path / ("aware" if policy_aware else "legacy")
    workspace = case_root / "workspace"
    workspace.mkdir(parents=True)
    storage = case_root / ".coding"
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
        web_fetch_policy_aware=policy_aware,
    )
    task = LearningTaskService(repository).create_draft(
        owner_id="local",
        workspace_id=workspace_id_from_path(workspace),
        request=LearningTaskCreate(
            topic="学习指定官方站点",
            desired_outcome="能引用指定站点解释关键概念",
            starting_level="beginner",
            time_budget_minutes_per_week=120,
            source_policy=LearningSourcePolicy(
                knowledge="disabled",
                web="allowed_when_insufficient",
                domains=("example.com",),
            ),
        ),
    )

    receipt = LearningActivationService(repository, resources).activate(
        owner_id="local",
        workspace_id=task.workspace_id,
        task_id=task.task_id,
        expected_revision=task.task_revision,
        idempotency_key=f"domain-policy-{policy_aware}",
    )

    assert ("web:fetch" in receipt.allowed_capabilities) is expects_fetch


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


@pytest.mark.asyncio
async def test_post_turn_learning_goal_revalidates_scope_before_evaluator(
    tmp_path: Path,
) -> None:
    scope, resolver, session, storage, kickoff_run_id = _real_scope(tmp_path)
    runtime = CodingRuntime(
        session_id=scope.session_id,
        workspace_root=tmp_path / "workspace",
        model=object(),
        storage_root=storage,
        session_state=session,
        runtime_profile="deerflow_v2",
    )
    journal = SessionEventJournal(storage, scope.session_id)
    goal = ThreadGoalService(journal).get()
    assert goal is not None
    source_run_id = "run-goal-evaluation"
    journal.append(
        run_id=source_run_id,
        kind="system",
        status="running",
        payload={"type": "run_started", "thread_goal": goal},
    )
    journal.append_terminal_once(
        run_id=source_run_id,
        status="completed",
        payload={"event": "run_completed"},
    )
    plan_store = TurnPlanStore(storage, scope.session_id)
    with sqlite3.connect(plan_store.path) as connection:
        connection.execute(
            "UPDATE turn_context_plans SET plan_hash = ? WHERE run_id = ?",
            ("sha256:tampered-before-goal-evaluator", kickoff_run_id),
        )
        connection.commit()
    evaluator = MagicMock()
    app = SimpleNamespace(
        state=SimpleNamespace(
            coding_sessions={scope.session_id: runtime},
            coding_run_registry=SimpleNamespace(
                get=lambda session_id: SimpleNamespace(journal=journal)
            ),
            learning_readonly_scope_resolver=resolver,
            coding_goal_evaluator_factory=evaluator,
            coding_goal_followup_tasks=set(),
            coding_goal_followup_shutdown=True,
            mastery_ledger=None,
        )
    )

    await _post_turn_goal_followup(app, scope.session_id, source_run_id)

    evaluator.assert_not_called()
    failures = [
        event.payload
        for event in journal.events_for_run(source_run_id)
        if event.payload.get("type") == "learning_scope_rejected"
    ]
    assert failures[-1]["reason_code"] == "learning_scope_validation_failed"


def test_learning_scope_denial_timeline_discards_pending_tool_arguments() -> None:
    adapter = HarnessEventAdapter(session_id="session_scope", run_id="run_scope")
    call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "run_shell",
                "args": {
                    "query": "PRIVATE_QUERY_SENTINEL",
                    "path": "/private/source-secret",
                    "prompt": "SKILL_PROMPT_SENTINEL",
                    "token": "SECRET_TOKEN_SENTINEL",
                },
                "id": "call-scope",
                "type": "tool_call",
            }
        ],
    )
    assert (
        adapter.adapt(
            HarnessStreamItem(
                sequence=1,
                mode="messages",
                payload=(call, {}),
                source_event_id="graph:messages:1:call",
            )
        )
        == ()
    )
    denial = ToolMessage(
        content="learning_scope_tool_forbidden",
        tool_call_id="call-scope",
        name="run_shell",
        status="error",
        additional_kwargs={
            "sage_learning_scope": {
                "task_id": "ltask_scope",
                "status": "denied",
                "reason_code": "learning_scope_tool_forbidden",
            }
        },
    )

    events = adapter.adapt(
        HarnessStreamItem(
            sequence=2,
            mode="messages",
            payload=(denial, {}),
            source_event_id="graph:messages:2:denial",
        )
    )

    assert len(events) == 1
    assert events[0].payload == {
        "type": "learning_scope_denied",
        "task_id": "ltask_scope",
        "tool_call_id": "call-scope",
        "status": "denied",
        "reason_code": "learning_scope_tool_forbidden",
        "run_id": "run_scope",
        "session_id": "session_scope",
    }
    serialized = str(events)
    assert "PRIVATE_QUERY_SENTINEL" not in serialized
    assert "/private/source-secret" not in serialized
    assert "SKILL_PROMPT_SENTINEL" not in serialized
    assert "SECRET_TOKEN_SENTINEL" not in serialized


def test_learning_model_text_is_replaced_by_one_content_free_public_receipt() -> None:
    adapter = HarnessEventAdapter(
        session_id="session_scope",
        run_id="run_scope",
        learning_scope_task_id="ltask_scope",
    )

    first = adapter.adapt(
        HarnessStreamItem(
            sequence=1,
            mode="messages",
            payload=(
                AIMessage(
                    content=(
                        "PRIVATE_QUERY_SENTINEL /private/source-secret "
                        "WEB_BODY_SENTINEL SECRET_TOKEN_SENTINEL"
                    )
                ),
                {},
            ),
            source_event_id="graph:messages:1:text",
        )
    )
    second = adapter.adapt(
        HarnessStreamItem(
            sequence=2,
            mode="messages",
            payload=(AIMessage(content="another private chunk"), {}),
            source_event_id="graph:messages:2:text",
        )
    )

    expected = LearningPublicProjector.model_output_receipt(
        task_id="ltask_scope",
        run_id="run_scope",
    )
    expected["session_id"] = "session_scope"
    assert [event.payload for event in first] == [expected]
    assert second == ()
    assert adapter.finish() == ()
    serialized = str((first, second))
    assert "text_delta" not in serialized
    assert "PRIVATE_QUERY_SENTINEL" not in serialized
    assert "/private/source-secret" not in serialized
    assert "WEB_BODY_SENTINEL" not in serialized
    assert "SECRET_TOKEN_SENTINEL" not in serialized


def test_learning_scope_failure_receipt_keeps_specific_reason_in_terminal() -> None:
    events = _learning_scope_failure_events(
        "run_scope",
        "learning_scope_capability_revision_mismatch",
    )

    assert [event.payload["reason_code"] for event in events] == [
        "learning_scope_capability_revision_mismatch",
        "learning_scope_capability_revision_mismatch",
    ]
    assert events[-1].payload["error_type"] == "learning_scope_conflict"


def test_learning_scope_success_timeline_contains_only_safe_capability_receipts() -> None:
    adapter = HarnessEventAdapter(
        session_id="session_scope",
        run_id="run_scope",
        learning_scope_task_id="ltask_scope",
        learning_capability_ids_by_tool_name={"knowledge_search": "local:knowledge_search"},
    )
    call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "knowledge_search",
                "args": {"query": "PRIVATE_QUERY_SENTINEL"},
                "id": "call-scope",
                "type": "tool_call",
            }
        ],
    )
    assert (
        adapter.adapt(
            HarnessStreamItem(
                sequence=1,
                mode="messages",
                payload=(call, {}),
                source_event_id="graph:messages:1:call",
            )
        )
        == ()
    )
    result = ToolMessage(
        content=(
            '{"query":"PRIVATE_QUERY_SENTINEL",'
            '"source_relative_path":"/private/source-secret",'
            '"excerpt":"WEB_BODY_SENTINEL"}'
        ),
        tool_call_id="call-scope",
        name="knowledge_search",
        status="success",
    )

    events = adapter.adapt(
        HarnessStreamItem(
            sequence=2,
            mode="messages",
            payload=(result, {}),
            source_event_id="graph:messages:2:result",
        )
    )

    assert [event.payload["type"] for event in events] == [
        "learning_tool_call",
        "learning_tool_result",
    ]
    for event in events:
        assert event.payload["task_id"] == "ltask_scope"
        assert event.payload["capability_id"] == "local:knowledge_search"
        assert event.payload["tool_call_id"] == "call-scope"
    serialized = str(events)
    assert "PRIVATE_QUERY_SENTINEL" not in serialized
    assert "/private/source-secret" not in serialized
    assert "WEB_BODY_SENTINEL" not in serialized


@pytest.mark.parametrize(
    "payload",
    [
        {
            "type": "tool_call",
            "tool": "knowledge_search",
            "tool_call_id": "call-custom",
            "args": {"query": "PRIVATE_QUERY_SENTINEL"},
        },
        {
            "type": "tool_result",
            "tool": "knowledge_search",
            "tool_call_id": "call-custom",
            "content": "WEB_BODY_SENTINEL",
        },
        {
            "type": "memory_proposal_ready",
            "reflection_id": "reflection-custom",
            "candidate": "SECRET_TOKEN_SENTINEL",
        },
        {
            "type": "unknown_learning_event",
            "query": "PRIVATE_QUERY_SENTINEL",
            "source_path": "/private/source-secret",
        },
    ],
)
def test_learning_timeline_drops_custom_payloads_without_a_safe_projection(
    payload: dict[str, object],
) -> None:
    adapter = HarnessEventAdapter(
        session_id="session_scope",
        run_id="run_scope",
        learning_scope_task_id="ltask_scope",
    )

    events = adapter.adapt(
        HarnessStreamItem(
            sequence=1,
            mode="custom",
            payload=payload,
            source_event_id="graph:custom:unsafe",
        )
    )

    assert events == ()


def test_learning_timeline_projects_only_safe_research_child_receipt() -> None:
    adapter = HarnessEventAdapter(
        session_id="session_scope",
        run_id="run_scope",
        learning_scope_task_id="ltask_scope",
    )

    events = adapter.adapt(
        HarnessStreamItem(
            sequence=1,
            mode="custom",
            payload={
                "type": "subagent_progress",
                "task_id": "ltask_forged",
                "child_run_id": "child_scope",
                "parent_run_id": "run_scope",
                "status": "completed",
                "reason_code": "evidence_collected",
                "description": "SKILL_PROMPT_SENTINEL",
                "args": {"query": "PRIVATE_QUERY_SENTINEL"},
                "content": "WEB_BODY_SENTINEL",
                "source_path": "/private/source-secret",
                "secret": "SECRET_TOKEN_SENTINEL",
            },
            source_event_id="graph:custom:research",
        )
    )

    assert len(events) == 1
    assert events[0].payload == {
        "type": "subagent_progress",
        "task_id": "ltask_scope",
        "child_run_id": "child_scope",
        "parent_run_id": "run_scope",
        "agent_run_id": "child_scope",
        "status": "completed",
        "reason_code": "evidence_collected",
        "run_id": "run_scope",
        "session_id": "session_scope",
    }
    serialized = str(events)
    assert "ltask_forged" not in serialized
    assert "SKILL_PROMPT_SENTINEL" not in serialized
    assert "PRIVATE_QUERY_SENTINEL" not in serialized
    assert "WEB_BODY_SENTINEL" not in serialized
    assert "/private/source-secret" not in serialized
    assert "SECRET_TOKEN_SENTINEL" not in serialized


def test_learning_run_api_projections_hide_trace_and_workspace_paths() -> None:
    raw_detail = {
        "run_id": "child_scope",
        "events": [
            {
                "type": "subagent_started",
                "run_id": "child_scope",
                "parent_run_id": "run_scope",
                "description": "SKILL_PROMPT_SENTINEL",
                "status": "running",
            },
            {
                "type": "tool_call",
                "run_id": "child_scope",
                "tool_call_id": "call-scope",
                "args": {"query": "PRIVATE_QUERY_SENTINEL"},
            },
            {
                "type": "tool_result",
                "run_id": "child_scope",
                "tool_call_id": "call-scope",
                "content": "WEB_BODY_SENTINEL /private/source-secret",
                "is_error": True,
                "error_code": "learning_scope_plan_mismatch",
            },
        ],
        "timeline": [{"detail": "PRIVATE_QUERY_SENTINEL"}],
        "audit": {
            "status": "error",
            "duration_ms": 12,
            "steps": [{"result_preview": "WEB_BODY_SENTINEL"}],
        },
    }
    raw_summary = {
        "run_id": "child_scope",
        "status": "error",
        "event_count": 3,
        "tool_count": 1,
        "error_count": 1,
        "last_event_type": "tool_result",
        "started_at": "2026-08-24T00:00:00Z",
        "updated_at": "2026-08-24T00:00:01Z",
        "changed_files": ["/private/source-secret"],
        "audit": raw_detail["audit"],
    }

    detail = _learning_run_detail_payload(
        raw_detail,
        task_id="ltask_scope",
    )
    summary = _learning_run_summary_payload(raw_summary)
    diff = _learning_workspace_diff_payload(
        "run_scope",
        {"changed_files": ["/private/source-secret"]},
    )

    assert detail == LearningPublicProjector.run_detail(raw_detail, task_id="ltask_scope")
    assert summary == LearningPublicProjector.run_summary(raw_summary)
    assert diff == LearningPublicProjector.workspace_diff(
        "run_scope",
        {"changed_files": ["/private/source-secret"]},
    )

    assert detail["events"][-1]["reason_code"] == "learning_scope_plan_mismatch"
    assert detail["timeline"] == []
    assert detail["audit"]["steps"] == []
    assert summary["changed_files"] == []
    assert summary["audit"]["changed_files"] == []
    assert diff == {
        "type": "workspace_diff_ready",
        "run_id": "run_scope",
        "status": "completed",
        "changed_file_count": 1,
    }
    serialized = str((detail, summary, diff))
    assert "PRIVATE_QUERY_SENTINEL" not in serialized
    assert "SKILL_PROMPT_SENTINEL" not in serialized
    assert "WEB_BODY_SENTINEL" not in serialized
    assert "/private/source-secret" not in serialized
