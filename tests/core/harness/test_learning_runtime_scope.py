"""Learning 只读范围在 Runtime、Provider 与 ToolBundle 边界的合同。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from tests.core.harness.learning_scope_support import make_scope

from api.coding import _deerflow_timeline_events, _runtime_timeline_events
from core.coding.run_coordinator import RunEvent
from core.coding.runtime import CodingRuntime
from core.harness.learning_public import LearningPublicProjector
from core.harness.learning_scope import LearningScopeConflict
from core.harness.tools_adapter import build_deerflow_coding_tool_bundle


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
    scope = make_scope(
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
    scope = make_scope(
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
    scope = make_scope(
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
    scope = make_scope(
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
    scope = make_scope(
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
