"""Subagent task-completion parity over the public parent timeline."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from sage_harness import SubagentToolConfig

import api.coding as coding_api
from api.main import create_app
from evals.coding.profile_parity import (
    ProfileParityReport,
    RuntimeProfile,
    project_profile_timeline,
)
from tests.core.coding.scripted_api_client import ScriptedApiClient


class LegacyBackgroundSubagentModel(ScriptedApiClient):
    """Launch a legacy background worker and finish without its result."""

    def __init__(self) -> None:
        super().__init__(
            [
                (
                    '<tool>{"name":"tool_search","args":'
                    '{"query":"Launch a bounded worker"}}</tool>'
                ),
                (
                    '<tool>{"name":"agent","args":'
                    '{"description":"读取 README","prompt":"读取 README 并返回第一行",'
                    '"subagent_type":"Explore","write_scope":[]}}</tool>'
                ),
                "<final>子代理已启动，但当前回答尚未获得它的结果。</final>",
            ]
        )


class DeerflowAwaitedSubagentModel(FakeMessagesListChatModel):
    """Wait for one bounded Explore child before composing the parent answer."""

    def __init__(self) -> None:
        super().__init__(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "task",
                            "args": {
                                "description": "读取 README",
                                "prompt": "读取 README 并返回第一行",
                                "subagent_type": "Explore",
                            },
                            "id": "call-awaited-child",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="子代理确认 README 第一行是 # Sage。"),
            ]
        )

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # type: ignore[no-untyped-def]
        _ = tools, tool_choice, kwargs
        return self


def _task_call(call_id: str) -> dict[str, object]:
    return {
        "name": "task",
        "args": {
            "description": "读取 README",
            "prompt": "读取 README 并返回第一行",
            "subagent_type": "Explore",
        },
        "id": call_id,
        "type": "tool_call",
    }


class DeerflowFailedSubagentModel(FakeMessagesListChatModel):
    def __init__(self) -> None:
        super().__init__(
            responses=[
                AIMessage(content="", tool_calls=[_task_call("call-failed-child")]),
                AIMessage(content="子代理失败，当前没有可用的 README 结论。"),
            ]
        )

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # type: ignore[no-untyped-def]
        _ = tools, tool_choice, kwargs
        return self


class DeerflowTimedOutSubagentModel(FakeMessagesListChatModel):
    def __init__(self) -> None:
        super().__init__(
            responses=[
                AIMessage(content="", tool_calls=[_task_call("call-timeout-child")]),
                AIMessage(content="子代理超时，主任务已恢复并说明未取得结果。"),
            ]
        )

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # type: ignore[no-untyped-def]
        _ = tools, tool_choice, kwargs
        return self


class DeerflowCancelledSubagentModel(FakeMessagesListChatModel):
    def __init__(self) -> None:
        super().__init__(
            responses=[
                AIMessage(content="", tool_calls=[_task_call("call-cancelled-child")]),
                AIMessage(content="这条回答不应在父取消后出现。"),
            ]
        )

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # type: ignore[no-untyped-def]
        _ = tools, tool_choice, kwargs
        return self


class FailedChildModel:
    async def complete(self, prompt: str) -> str:
        _ = prompt
        raise RuntimeError("private child failure")


class SlowChildModel:
    async def complete(self, prompt: str) -> str:
        _ = prompt
        await asyncio.sleep(30)
        return "<final>late child result</final>"


def _child_model() -> ScriptedApiClient:
    return ScriptedApiClient(
        [
            '<tool>{"name":"read_file","args":{"path":"README.md"}}</tool>',
            "<final>README 第一行是 # Sage。</final>",
        ]
    )


def _payloads(events: list[dict[str, object]]) -> list[dict[str, object]]:
    return [payload for event in events if isinstance((payload := event.get("payload")), dict)]


def _final_contains(events: list[dict[str, object]], expected: str) -> bool:
    return any(
        payload.get("type") == "final" and expected in str(payload.get("content", ""))
        for payload in _payloads(events)
    )


def _run_subagent_scenario(
    tmp_path: Path,
    profile: RuntimeProfile,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    workspace = tmp_path / f"subagent-{profile}"
    workspace.mkdir()
    (workspace / "README.md").write_text("# Sage\n", encoding="utf-8")
    app = create_app(
        coding_model_factory=(
            LegacyBackgroundSubagentModel if profile == "legacy" else DeerflowAwaitedSubagentModel
        ),
        coding_workspace_root=workspace,
        coding_storage_root=workspace / ".coding",
        coding_deerflow_v2_enabled=True,
    )

    with TestClient(app) as client:
        session_id = client.post(
            "/api/v1/coding/session",
            json={"runtime_profile": profile},
        ).json()["session_id"]
        runtime = app.state.coding_sessions[session_id]
        runtime.worker_manager.model_factory = _child_model
        with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
            websocket.send_json({"content": "委派子代理读取 README 并告诉我第一行"})
            events: list[dict[str, object]] = []
            while True:
                event = websocket.receive_json()
                events.append(event)
                if event["kind"] == "terminal":
                    break

        if profile == "legacy":
            runtime.worker_manager.wait("agent_1", timeout=5)
            notifications = runtime.worker_manager.drain_notifications()
            return events, {
                "child_notification": notifications[0] if notifications else "",
                "child_run_persisted": False,
            }

        completed = next(
            payload for payload in _payloads(events) if payload.get("type") == "subagent_completed"
        )
        child_run_id = str(completed["child_run_id"])
        child_run = runtime.run_store.get_run(child_run_id)
        child_terminal = next(
            item
            for item in reversed(child_run["events"])
            if item.get("type") == "subagent_terminal"
        )
        return events, {
            "child_run_id": child_run_id,
            "child_status": child_terminal.get("status"),
            "child_result": child_terminal.get("result_brief"),
            "child_run_persisted": True,
        }


def _run_v2_outcome_scenario(
    tmp_path: Path,
    *,
    scenario: str,
    parent_model: type[FakeMessagesListChatModel],
    child_model: object,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    workspace = tmp_path / scenario
    workspace.mkdir()
    (workspace / "README.md").write_text("# Sage\n", encoding="utf-8")
    app = create_app(
        coding_model_factory=parent_model,
        coding_workspace_root=workspace,
        coding_storage_root=workspace / ".coding",
        coding_deerflow_v2_enabled=True,
    )
    with TestClient(app) as client:
        session_id = client.post(
            "/api/v1/coding/session",
            json={"runtime_profile": "deerflow_v2"},
        ).json()["session_id"]
        runtime = app.state.coding_sessions[session_id]
        runtime.worker_manager.model_factory = lambda: child_model
        with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
            websocket.send_json({"content": "委派子代理读取 README"})
            events: list[dict[str, object]] = []
            while True:
                event = websocket.receive_json()
                events.append(event)
                if event["kind"] == "terminal":
                    break

        terminal_payload = next(
            payload
            for payload in _payloads(events)
            if payload.get("type") in {"subagent_failed", "subagent_timed_out"}
        )
        child_run_id = str(terminal_payload["child_run_id"])
        child_run = runtime.run_store.get_run(child_run_id)
        child_terminal = next(
            item
            for item in reversed(child_run["events"])
            if item.get("type") == "subagent_terminal"
        )
        return events, {
            "child_status": child_terminal.get("status"),
            "child_error_code": child_terminal.get("error_code"),
        }


def _task_results(events: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        payload
        for payload in _payloads(events)
        if payload.get("type") == "tool_result" and payload.get("tool") == "task"
    ]


def test_v2_awaits_child_result_that_legacy_only_reports_in_background(
    tmp_path: Path,
) -> None:
    legacy_events, legacy_evidence = _run_subagent_scenario(tmp_path, "legacy")
    v2_events, v2_evidence = _run_subagent_scenario(tmp_path, "deerflow_v2")
    legacy_result = project_profile_timeline(
        "awaited-subagent-result",
        "legacy",
        legacy_events,
        assertions_passed=_final_contains(legacy_events, "# Sage"),
    )
    v2_result = project_profile_timeline(
        "awaited-subagent-result",
        "deerflow_v2",
        v2_events,
        assertions_passed=(
            _final_contains(v2_events, "# Sage")
            and v2_evidence.get("child_status") == "succeeded"
            and "# Sage" in str(v2_evidence.get("child_result", ""))
        ),
    )
    report = ProfileParityReport(results=[legacy_result, v2_result])

    assert report.paired_scenarios() == ("awaited-subagent-result",)
    assert report.regressions() == ()
    assert legacy_result.contract_completed is True
    assert legacy_result.passed is False
    assert "# Sage" in str(legacy_evidence["child_notification"])
    assert legacy_evidence["child_run_persisted"] is False
    assert not any(
        payload.get("type") == "subagent_completed" for payload in _payloads(legacy_events)
    )
    assert v2_result.passed is True
    assert v2_evidence["child_run_persisted"] is True
    assert [legacy_result.tool_calls, v2_result.tool_calls] == [2, 1]
    assert [legacy_result.tool_errors, v2_result.tool_errors] == [0, 0]
    assert report.metrics("legacy").task_completion_rate == 0.0
    assert report.metrics("deerflow_v2").task_completion_rate == 1.0


def test_v2_projects_failed_child_and_parent_recovers_with_final(tmp_path: Path) -> None:
    events, evidence = _run_v2_outcome_scenario(
        tmp_path,
        scenario="subagent-failed",
        parent_model=DeerflowFailedSubagentModel,
        child_model=FailedChildModel(),
    )

    assert evidence == {
        "child_status": "failed",
        "child_error_code": "child_execution_failed",
    }
    assert _final_contains(events, "子代理失败")
    assert [payload.get("type") for payload in _payloads(events)].count("subagent_failed") == 1
    results = _task_results(events)
    assert len(results) == 1
    assert results[0].get("is_error") is True


def test_v2_projects_child_timeout_and_parent_recovers_with_final(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        coding_api,
        "SubagentToolConfig",
        lambda: SubagentToolConfig(timeout_seconds=0.1),
    )
    events, evidence = _run_v2_outcome_scenario(
        tmp_path,
        scenario="subagent-timeout",
        parent_model=DeerflowTimedOutSubagentModel,
        child_model=SlowChildModel(),
    )

    assert evidence == {
        "child_status": "timed_out",
        "child_error_code": "timeout",
    }
    assert _final_contains(events, "主任务已恢复")
    assert [payload.get("type") for payload in _payloads(events)].count("subagent_timed_out") == 1
    results = _task_results(events)
    assert len(results) == 1
    assert results[0].get("is_error") is True


def test_v2_parent_stop_cancels_running_child_without_a_false_final(tmp_path: Path) -> None:
    app = create_app(
        coding_model_factory=DeerflowCancelledSubagentModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
        coding_deerflow_v2_enabled=True,
    )
    with TestClient(app) as client:
        session_id = client.post(
            "/api/v1/coding/session",
            json={"runtime_profile": "deerflow_v2"},
        ).json()["session_id"]
        runtime = app.state.coding_sessions[session_id]
        runtime.worker_manager.model_factory = lambda: SlowChildModel()
        with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
            websocket.send_json({"content": "启动探索任务，然后等待我的取消"})
            events: list[dict[str, object]] = []
            started: dict[str, object] | None = None
            run_id = ""
            while started is None:
                event = websocket.receive_json()
                events.append(event)
                payload = event.get("payload")
                if isinstance(payload, dict) and payload.get("type") == "subagent_started":
                    started = payload
                    run_id = str(event["run_id"])

            stopped = client.post(
                f"/api/v1/coding/{session_id}/run/stop",
                json={"run_id": run_id},
            )
            assert stopped.status_code == 200
            assert stopped.json() == {"ok": True}
            while events[-1]["kind"] != "terminal":
                events.append(websocket.receive_json())

        child_run_id = str(started["child_run_id"])
        child_run = runtime.run_store.get_run(child_run_id)
        child_terminal = next(
            item
            for item in reversed(child_run["events"])
            if item.get("type") == "subagent_terminal"
        )

    assert events[-1]["status"] == "cancelled"
    assert child_terminal["status"] == "cancelled"
    assert child_terminal["error_code"] == "parent_cancelled"
    assert [payload.get("type") for payload in _payloads(events)].count("subagent_cancelled") == 1
    assert not any(payload.get("type") == "final" for payload in _payloads(events))
