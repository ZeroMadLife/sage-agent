"""Coding API route tests."""

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from api.coding import _coding_knowledge_store
from api.main import create_app


def _receive_runtime_event(websocket):
    """Unwrap the next Engine event, ignoring Harness projection facts."""
    while True:
        envelope = websocket.receive_json()
        if envelope["kind"] == "harness":
            continue
        payload = envelope["payload"]
        if "type" in payload and payload["type"] not in {"user"}:
            return payload


def _receive_until(websocket, event_type: str, *, limit: int = 20):
    """Read a bounded runtime sequence through the requested terminal event."""
    events = []
    for _ in range(limit):
        event = _receive_runtime_event(websocket)
        events.append(event)
        if event["type"] == event_type:
            return events
    raise AssertionError(f"runtime did not emit {event_type} within {limit} events")


def test_coding_runtime_does_not_bypass_production_knowledge_gate() -> None:
    store = object()
    development = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(cloud_app_env="development", knowledge_store=store)
        )
    )
    production = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(cloud_app_env="production", knowledge_store=store)
        )
    )

    assert _coding_knowledge_store(development) is store
    assert _coding_knowledge_store(production) is None


class FakeModel:
    """Deterministic model for coding route tests."""

    def __init__(self) -> None:
        self.responses = [
            '<tool>{"name":"read_file","args":{"path":"README.md"}}</tool>',
            "<final>README 里能看到项目内容。</final>",
        ]

    async def complete(self, prompt: str) -> str:
        _ = prompt
        return self.responses.pop(0)


class FakeWriteModel:
    """Model that requests one risky write and then returns a final answer."""

    def __init__(self) -> None:
        self.responses = [
            '<tool>{"name":"write_file","args":{"path":"note.txt","content":"approved"}}' "</tool>",
            "<final>写入完成。</final>",
        ]

    async def complete(self, prompt: str) -> str:
        _ = prompt
        return self.responses.pop(0)


class DeerflowFakeModel(FakeMessagesListChatModel):
    """LangChain streaming model used by the explicit DeerFlow profile smoke."""

    def __init__(self) -> None:
        super().__init__(responses=[AIMessage(content="LangGraph 回答")])

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # type: ignore[no-untyped-def]
        _ = tools, tool_choice, kwargs
        return self


class DeerflowToolFakeModel(FakeMessagesListChatModel):
    """Tool-capable fake that exercises the read-only Sage tool bridge."""

    def __init__(self) -> None:
        super().__init__(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "list_files",
                            "args": {"path": "."},
                            "id": "call-list-files",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="文件已列出"),
            ]
        )

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # type: ignore[no-untyped-def]
        _ = tools, tool_choice, kwargs
        return self


class DeerflowApprovalFakeModel(FakeMessagesListChatModel):
    """Tool-capable fake used to verify the existing approval endpoint wakes graph runs."""

    def __init__(self) -> None:
        super().__init__(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_file",
                            "args": {"path": "approved.txt", "content": "ok"},
                            "id": "call-write-file",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="写入完成"),
            ]
        )

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # type: ignore[no-untyped-def]
        _ = tools, tool_choice, kwargs
        return self


class DeerflowMultiApprovalFakeModel(FakeMessagesListChatModel):
    """Tool-capable fake that requires two graph checkpoint resumes."""

    def __init__(self) -> None:
        super().__init__(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_file",
                            "args": {"path": "first.txt", "content": "one"},
                            "id": "call-write-first",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_file",
                            "args": {"path": "second.txt", "content": "two"},
                            "id": "call-write-second",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="两次写入完成"),
            ]
        )

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # type: ignore[no-untyped-def]
        _ = tools, tool_choice, kwargs
        return self


class DeerflowAgentFakeModel(FakeMessagesListChatModel):
    """Tool-capable fake that awaits one bounded Explore child."""

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
                            "id": "call-task",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="子任务结果已合并"),
            ]
        )

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # type: ignore[no-untyped-def]
        _ = tools, tool_choice, kwargs
        return self


def test_create_coding_session(tmp_path: Path) -> None:
    """POST /api/v1/coding/session creates a coding runtime session."""
    client = TestClient(
        create_app(
            coding_model_factory=FakeModel,
            coding_workspace_root=tmp_path,
            coding_storage_root=tmp_path / ".coding",
        )
    )

    response = client.post("/api/v1/coding/session", json={})

    assert response.status_code == 200
    data = response.json()
    assert data["session_id"]
    assert data["workspace_root"] == str(tmp_path.resolve())
    assert data["permission_mode"] == "default"
    assert data["runtime_profile"] == "legacy"
    assert data["sandbox_provider"] == "local_workspace"
    assert data["sandbox_image"] == "python:3.11-slim"


def test_deerflow_profile_requires_server_rollout_gate(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            coding_model_factory=FakeModel,
            coding_workspace_root=tmp_path,
            coding_storage_root=tmp_path / ".coding",
        )
    )

    response = client.post(
        "/api/v1/coding/session",
        json={"runtime_profile": "deerflow_v2"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "deerflow_v2 runtime profile is disabled"


def test_enabled_deerflow_profile_is_persisted_and_resumed(tmp_path: Path) -> None:
    app = create_app(
        coding_model_factory=FakeModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
        coding_deerflow_v2_enabled=True,
        coding_sandbox_provider="container",
        coding_sandbox_image="python:3.12-slim",
    )
    client = TestClient(app)
    created = client.post(
        "/api/v1/coding/session",
        json={"runtime_profile": "deerflow_v2"},
    )

    assert created.status_code == 200
    session_id = created.json()["session_id"]
    assert created.json()["runtime_profile"] == "deerflow_v2"
    assert created.json()["sandbox_provider"] == "container"
    assert created.json()["sandbox_image"] == "python:3.12-slim"
    app.state.coding_sessions.pop(session_id)

    resumed = client.post(f"/api/v1/coding/session/{session_id}/resume")

    assert resumed.status_code == 200
    assert resumed.json()["runtime_profile"] == "deerflow_v2"
    assert resumed.json()["sandbox_provider"] == "container"
    assert resumed.json()["sandbox_image"] == "python:3.12-slim"
    assert app.state.coding_sessions[session_id].runtime_profile == "deerflow_v2"


def test_deerflow_profile_refuses_host_local_sandbox_in_production(tmp_path: Path) -> None:
    app = create_app(
        coding_model_factory=FakeModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
        coding_deerflow_v2_enabled=True,
        cloud_app_env="production",
        cloud_repository=object(),
    )

    response = TestClient(app).post(
        "/api/v1/coding/session",
        json={"runtime_profile": "deerflow_v2"},
    )

    assert response.status_code == 422
    assert "isolated sandbox" in response.json()["detail"]


def test_container_sandbox_reconciles_terminal_resources_on_app_start(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[str] = []

    def fake_reconcile(provider: str) -> int:
        calls.append(provider)
        return 3

    monkeypatch.setattr("api.main.reconcile_coding_sandboxes", fake_reconcile)
    app = create_app(
        coding_model_factory=FakeModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
        coding_deerflow_v2_enabled=True,
        coding_sandbox_provider="container",
    )

    with TestClient(app):
        pass

    assert calls == ["container"]
    assert app.state.coding_sandbox_reconciled == 3


def test_enabled_deerflow_profile_streams_public_answer_and_replays_history(tmp_path: Path) -> None:
    app = create_app(
        coding_model_factory=DeerflowFakeModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
        coding_deerflow_v2_enabled=True,
    )
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/coding/session",
            json={"runtime_profile": "deerflow_v2"},
        )
        assert created.status_code == 200
        session_id = created.json()["session_id"]
        with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
            websocket.send_json({"content": "回答一句话"})
            events: list[dict] = []
            while True:
                event = websocket.receive_json()
                events.append(event)
                if event["kind"] == "terminal":
                    break

        payloads = [event["payload"] for event in events]
        catalog = next(payload for payload in payloads if payload.get("type") == "mcp_catalog_updated")
        assert {server["name"] for server in catalog["servers"]} == {
            "amap",
            "weather",
            "scenic",
        }
        assert "test-amap-key" not in repr(catalog)
        assert "test-weather-key" not in repr(catalog)
        assert any(payload.get("type") == "text_delta" for payload in payloads)
        assert any(payload.get("type") == "final" for payload in payloads)
        assert events[-1]["status"] == "completed"
        messages = client.get(f"/api/v1/coding/session/{session_id}/messages").json()["messages"]
        assert [item["role"] for item in messages] == ["user", "assistant"]
        assert messages[-1]["content"] == "LangGraph 回答"


def test_enabled_deerflow_profile_streams_read_tool_summary(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Sage\n", encoding="utf-8")
    app = create_app(
        coding_model_factory=DeerflowToolFakeModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
        coding_deerflow_v2_enabled=True,
    )
    with TestClient(app) as client:
        session_id = client.post(
            "/api/v1/coding/session",
            json={"runtime_profile": "deerflow_v2"},
        ).json()["session_id"]
        with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
            websocket.send_json({"content": "列出文件"})
            events: list[dict] = []
            while True:
                event = websocket.receive_json()
                events.append(event)
                if event["kind"] == "terminal":
                    break

    payloads = [event["payload"] for event in events]
    tool_calls = [payload for payload in payloads if payload.get("type") == "tool_call"]
    tool_results = [payload for payload in payloads if payload.get("type") == "tool_result"]
    assert tool_calls and tool_calls[0]["tool"] == "list_files"
    assert tool_calls[0]["args"] == {"path": "."}
    assert tool_calls[0]["tool_call_id"] == "call-list-files"
    assert tool_results and "README.md" in tool_results[0]["content"]
    assert any(payload.get("type") == "text_delta" for payload in payloads)


def test_enabled_deerflow_profile_reuses_approval_endpoint_for_write_tool(tmp_path: Path) -> None:
    app = create_app(
        coding_model_factory=DeerflowApprovalFakeModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
        coding_deerflow_v2_enabled=True,
    )
    with TestClient(app) as client:
        session_id = client.post(
            "/api/v1/coding/session",
            json={"runtime_profile": "deerflow_v2", "approval_policy": "ask"},
        ).json()["session_id"]
        with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
            websocket.send_json({"content": "写一个文件"})
            approval = None
            while approval is None:
                event = websocket.receive_json()
                if event["payload"].get("type") == "approval_required":
                    approval = event["payload"]
            response = client.post(
                f"/api/v1/coding/{session_id}/approval/respond",
                json={"approval_id": approval["approval_id"], "choice": "once"},
            )
            assert response.status_code == 200
            assert approval["tool_call_id"] == "call-write-file"
            assert len(approval["args_digest"]) == 64
            payloads: list[dict] = []
            while True:
                event = websocket.receive_json()
                payloads.append(event["payload"])
                if event["kind"] == "terminal":
                    break
        run_id = str(approval["run_id"])
        diff_response = client.get(
            f"/api/v1/coding/{session_id}/runs/{run_id}/diff"
        )
        runs_response = client.get(f"/api/v1/coding/{session_id}/runs")

    assert (tmp_path / "approved.txt").read_text(encoding="utf-8") == "ok"
    assert any(item.get("type") == "approval_granted" for item in payloads)
    assert any(item.get("type") == "final" for item in payloads)
    diff_events = [
        item for item in payloads if item.get("type") == "workspace_diff_ready"
    ]
    assert len(diff_events) == 1
    assert diff_events[0]["changed_files"] == ["approved.txt"]
    assert diff_response.status_code == 200
    assert diff_response.json()["changed_files"][0]["path"] == "approved.txt"
    assert runs_response.status_code == 200
    run = next(item for item in runs_response.json()["runs"] if item["run_id"] == run_id)
    assert run["status"] == "completed"
    assert run["changed_files"] == ["approved.txt"]


def test_deerflow_approval_survives_app_restart_and_resumes_same_run(
    tmp_path: Path,
) -> None:
    model = DeerflowApprovalFakeModel()

    def model_factory():
        return model

    first_app = create_app(
        coding_model_factory=model_factory,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
        coding_deerflow_v2_enabled=True,
    )
    with TestClient(first_app) as client:
        session_id = client.post(
            "/api/v1/coding/session",
            json={"runtime_profile": "deerflow_v2", "approval_policy": "ask"},
        ).json()["session_id"]
        with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
            websocket.send_json({"content": "重启后继续写文件"})
            approval = None
            while approval is None:
                event = websocket.receive_json()
                if event["payload"].get("type") == "approval_required":
                    approval = event["payload"]
        journal = first_app.state.coding_run_registry.get(session_id).journal
        run_id = str(approval["run_id"])

    assert journal.active_run_id() is None
    assert journal.recoverable_approval(run_id)["approval_id"] == approval["approval_id"]
    assert not any(
        item.kind == "terminal" for item in journal.replay(after=0, limit=100).items
    )

    restarted = create_app(
        coding_model_factory=model_factory,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
        coding_deerflow_v2_enabled=True,
    )
    with TestClient(restarted) as client:
        resumed = client.post(f"/api/v1/coding/session/{session_id}/resume")
        pending = client.get(f"/api/v1/coding/{session_id}/approval/pending")
        approved = client.post(
            f"/api/v1/coding/{session_id}/approval/respond",
            json={"approval_id": approval["approval_id"], "choice": "once"},
        )
        with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
            events = []
            while True:
                event = websocket.receive_json()
                events.append(event)
                if event["kind"] == "terminal":
                    break
        diff_response = client.get(
            f"/api/v1/coding/{session_id}/runs/{run_id}/diff"
        )
        runs_response = client.get(f"/api/v1/coding/{session_id}/runs")

    assert resumed.status_code == 200
    assert pending.status_code == 200
    assert pending.json()["approval_id"] == approval["approval_id"]
    assert approved.status_code == 200
    assert (tmp_path / "approved.txt").read_text(encoding="utf-8") == "ok"
    assert [item["payload"].get("event") for item in events].count("run_started") == 1
    assert any(item["payload"].get("type") == "approval_granted" for item in events)
    assert any(item["payload"].get("type") == "final" for item in events)
    diff_events = [
        item["payload"]
        for item in events
        if item["payload"].get("type") == "workspace_diff_ready"
    ]
    assert len(diff_events) == 1
    assert diff_events[0]["changed_files"] == ["approved.txt"]
    assert diff_response.status_code == 200
    assert diff_response.json()["changed_files"][0]["path"] == "approved.txt"
    assert runs_response.status_code == 200
    run = next(item for item in runs_response.json()["runs"] if item["run_id"] == run_id)
    assert run["status"] == "completed"
    assert run["changed_files"] == ["approved.txt"]


def test_enabled_deerflow_profile_resumes_graph_after_approval_denial(
    tmp_path: Path,
) -> None:
    app = create_app(
        coding_model_factory=DeerflowApprovalFakeModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
        coding_deerflow_v2_enabled=True,
    )
    with TestClient(app) as client:
        session_id = client.post(
            "/api/v1/coding/session",
            json={"runtime_profile": "deerflow_v2", "approval_policy": "ask"},
        ).json()["session_id"]
        with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
            websocket.send_json({"content": "不要写这个文件"})
            approval = None
            while approval is None:
                event = websocket.receive_json()
                if event["payload"].get("type") == "approval_required":
                    approval = event["payload"]
            response = client.post(
                f"/api/v1/coding/{session_id}/approval/respond",
                json={"approval_id": approval["approval_id"], "choice": "deny"},
            )
            assert response.status_code == 200
            payloads: list[dict] = []
            while True:
                event = websocket.receive_json()
                payloads.append(event["payload"])
                if event["kind"] == "terminal":
                    break

    assert not (tmp_path / "approved.txt").exists()
    assert any(
        item.get("type") == "tool_result"
        and item.get("content") == "approval denied"
        for item in payloads
    )
    assert any(item.get("type") == "final" for item in payloads)


def test_enabled_deerflow_profile_handles_multiple_graph_approval_resumes(
    tmp_path: Path,
) -> None:
    app = create_app(
        coding_model_factory=DeerflowMultiApprovalFakeModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
        coding_deerflow_v2_enabled=True,
    )
    with TestClient(app) as client:
        session_id = client.post(
            "/api/v1/coding/session",
            json={"runtime_profile": "deerflow_v2", "approval_policy": "ask"},
        ).json()["session_id"]
        approvals: list[str] = []
        payloads: list[dict] = []
        with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
            websocket.send_json({"content": "写两个文件"})
            while True:
                event = websocket.receive_json()
                payload = event["payload"]
                payloads.append(payload)
                if payload.get("type") == "approval_required":
                    approvals.append(str(payload["approval_id"]))
                    response = client.post(
                        f"/api/v1/coding/{session_id}/approval/respond",
                        json={"approval_id": approvals[-1], "choice": "once"},
                    )
                    assert response.status_code == 200
                if event["kind"] == "terminal":
                    break

    assert len(approvals) == 2
    assert (tmp_path / "first.txt").read_text(encoding="utf-8") == "one"
    assert (tmp_path / "second.txt").read_text(encoding="utf-8") == "two"
    assert any(item.get("type") == "final" for item in payloads)


def test_enabled_deerflow_profile_reuses_graph_session_approval(tmp_path: Path) -> None:
    app = create_app(
        coding_model_factory=DeerflowMultiApprovalFakeModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
        coding_deerflow_v2_enabled=True,
    )
    with TestClient(app) as client:
        session_id = client.post(
            "/api/v1/coding/session",
            json={"runtime_profile": "deerflow_v2", "approval_policy": "ask"},
        ).json()["session_id"]
        approvals: list[str] = []
        with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
            websocket.send_json({"content": "本会话写两个文件"})
            while True:
                event = websocket.receive_json()
                payload = event["payload"]
                if payload.get("type") == "approval_required":
                    approvals.append(str(payload["approval_id"]))
                    response = client.post(
                        f"/api/v1/coding/{session_id}/approval/respond",
                        json={"approval_id": approvals[-1], "choice": "session"},
                    )
                    assert response.status_code == 200
                if event["kind"] == "terminal":
                    break

    assert len(approvals) == 1
    assert (tmp_path / "first.txt").read_text(encoding="utf-8") == "one"
    assert (tmp_path / "second.txt").read_text(encoding="utf-8") == "two"


def test_enabled_deerflow_profile_awaits_and_projects_subagent_terminal(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Sage\n", encoding="utf-8")
    app = create_app(
        coding_model_factory=DeerflowAgentFakeModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
        coding_deerflow_v2_enabled=True,
    )
    with TestClient(app) as client:
        session_id = client.post(
            "/api/v1/coding/session",
            json={"runtime_profile": "deerflow_v2"},
        ).json()["session_id"]
        app.state.coding_sessions[session_id].worker_manager.model_factory = lambda: FakeModel()
        with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
            websocket.send_json({"content": "启动一个探索任务"})
            payloads: list[dict] = []
            while True:
                event = websocket.receive_json()
                payloads.append(event["payload"])
                if event["kind"] == "terminal":
                    break

    agent_events = [
        item for item in payloads if str(item.get("type", "")).startswith("subagent_")
    ]
    assert [item["type"] for item in agent_events] == [
        "subagent_started",
        "subagent_completed",
    ]
    assert agent_events[0]["child_run_id"].startswith("child_")
    assert agent_events[1]["child_run_id"] == agent_events[0]["child_run_id"]
    assert agent_events[1]["result_ref"].startswith("subagent://")
    assert any(item.get("type") == "final" for item in payloads)


def test_create_coding_session_accepts_approval_policy(tmp_path: Path) -> None:
    """POST /coding/session can opt into ask-mode approvals for the workbench."""
    app = create_app(
        coding_model_factory=FakeModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
    )
    client = TestClient(app)

    response = client.post("/api/v1/coding/session", json={"approval_policy": "ask"})

    assert response.status_code == 200
    session_id = response.json()["session_id"]
    assert app.state.coding_sessions[session_id].approval_policy == "ask"


def test_list_coding_sessions_returns_persisted_sessions(tmp_path: Path) -> None:
    """GET /coding/sessions exposes local Sage coding session history."""
    (tmp_path / "README.md").write_text("# Sage\n", encoding="utf-8")
    client = TestClient(
        create_app(
            coding_model_factory=FakeModel,
            coding_workspace_root=tmp_path,
            coding_storage_root=tmp_path / ".coding",
        )
    )
    created = client.post("/api/v1/coding/session", json={}).json()
    session_id = created["session_id"]
    # Run a turn so the session becomes visible in the non-empty session list.
    with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
        websocket.send_json({"content": "读 README.md"})
        while _receive_runtime_event(websocket)["type"] != "final":
            pass

    response = client.get("/api/v1/coding/sessions")

    assert response.status_code == 200
    sessions = response.json()["sessions"]
    assert sessions[0]["session_id"] == session_id
    assert sessions[0]["workspace_root"] == str(tmp_path.resolve())
    assert sessions[0]["runtime_mode"] == "default"


def test_coding_session_metadata_can_rename_pin_and_archive(tmp_path: Path) -> None:
    app = create_app(
        coding_model_factory=FakeModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
    )
    client = TestClient(app)
    session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
    runtime = app.state.coding_sessions[session_id]
    runtime.session["history"] = [{"role": "user", "content": "原始标题"}]
    runtime._save_session()

    response = client.patch(
        f"/api/v1/coding/session/{session_id}/metadata",
        json={"title": "新标题", "pinned": True},
    )
    assert response.status_code == 200
    assert response.json()["title"] == "新标题"
    assert response.json()["pinned"] is True

    archived = client.patch(
        f"/api/v1/coding/session/{session_id}/metadata",
        json={"archived": True},
    )
    assert archived.status_code == 200
    assert client.get("/api/v1/coding/sessions").json()["sessions"] == []
    assert client.get("/api/v1/coding/sessions?include_archived=true").json()["sessions"][0]["archived"] is True


def test_resume_coding_session_rehydrates_runtime(tmp_path: Path) -> None:
    """POST /coding/session/{id}/resume restores a persisted runtime into memory."""
    (tmp_path / "README.md").write_text("Sage resume\n", encoding="utf-8")
    app = create_app(
        coding_model_factory=FakeModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
    )
    client = TestClient(app)
    session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
    with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
        websocket.send_json({"content": "读 README.md"})
        while _receive_runtime_event(websocket)["type"] != "final":
            pass
    app.state.coding_sessions.clear()

    response = client.post(f"/api/v1/coding/session/{session_id}/resume")

    assert response.status_code == 200
    assert response.json()["session_id"] == session_id
    assert response.json()["permission_mode"] == "default"
    assert session_id in app.state.coding_sessions
    assert app.state.coding_sessions[session_id].session["history"][0]["content"] == "读 README.md"


def test_resume_recovers_an_interrupted_persisted_run_before_rehydrating(tmp_path: Path) -> None:
    """A restart turns an abandoned durable lease into a retryable terminal event."""
    app = create_app(
        coding_model_factory=FakeModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
    )
    client = TestClient(app)
    session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
    coordinator = app.state.coding_run_registry.get(session_id)
    coordinator.journal.begin_run("abandoned", owner_id="legacy", owner_pid=-1)
    app.state.coding_sessions.clear()

    response = client.post(f"/api/v1/coding/session/{session_id}/resume")
    timeline = client.get(f"/api/v1/coding/session/{session_id}/timeline").json()

    assert response.status_code == 200
    assert timeline["active_run"] is None
    assert timeline["items"][-1]["status"] == "interrupted"
    assert timeline["items"][-1]["payload"] == {
        "event": "run_interrupted", "retryable": True,
    }


def test_resume_unknown_session_does_not_create_timeline_evidence(tmp_path: Path) -> None:
    """A valid-looking unknown ID must not allocate durable state before its 404."""
    client = TestClient(
        create_app(
            coding_model_factory=FakeModel,
            coding_workspace_root=tmp_path,
            coding_storage_root=tmp_path / ".coding",
        )
    )

    response = client.post("/api/v1/coding/session/unknown-session/resume")

    assert response.status_code == 404
    assert not (tmp_path / ".coding" / "evidence" / "unknown-session").exists()


def test_resume_keeps_active_runtime_and_pending_approval_bound_to_it(tmp_path: Path) -> None:
    """An active coordinator run must keep its in-memory approval queue on resume."""
    app = create_app(
        coding_model_factory=FakeWriteModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
    )
    with TestClient(app) as client:
        session_id = client.post(
            "/api/v1/coding/session", json={"approval_policy": "ask"}
        ).json()["session_id"]
        original_runtime = app.state.coding_sessions[session_id]
        original_run_id = "run-active"
        original_runtime.active_run_id = original_run_id
        approval = original_runtime.approval_manager.submit(
            session_id,
            "write_file",
            {"path": "note.txt"},
            "write_file requires approval.",
            "tool:write_file",
        )
        coordinator = app.state.coding_run_registry.get(session_id)
        coordinator._active_run_id = original_run_id

        resumed = client.post(f"/api/v1/coding/session/{session_id}/resume")
        pending = client.get(f"/api/v1/coding/{session_id}/approval/pending")
        approved = client.post(
            f"/api/v1/coding/{session_id}/approval/respond",
            json={"approval_id": approval.approval_id, "choice": "once"},
        )

    assert resumed.status_code == 200
    assert resumed.json()["session_id"] == session_id
    assert app.state.coding_sessions[session_id] is original_runtime
    assert original_runtime.active_run_id == original_run_id
    assert pending.status_code == 200
    assert pending.json()["approval_id"] == approval.approval_id
    assert approved.status_code == 200
    assert approval.event.is_set()


def test_resume_keeps_runtime_while_startup_lease_is_being_published(
    tmp_path: Path, monkeypatch
) -> None:
    """The durable lease closes the gap before RunCoordinator exposes its active id."""
    app = create_app(
        coding_model_factory=FakeModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
    )
    lease_written = threading.Event()
    allow_start = threading.Event()
    with TestClient(app) as client:
        session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
        original_runtime = app.state.coding_sessions[session_id]
        coordinator = app.state.coding_run_registry.get(session_id)
        original_begin = coordinator.journal.begin_run

        def blocked_begin(run_id: str, *, owner_id: str, owner_pid: int):
            result = original_begin(run_id, owner_id=owner_id, owner_pid=owner_pid)
            lease_written.set()
            assert allow_start.wait(timeout=2)
            return result

        monkeypatch.setattr(coordinator.journal, "begin_run", blocked_begin)
        with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
            websocket.send_json({"content": "读 README.md"})
            assert lease_written.wait(timeout=2)
            assert coordinator.active_run_id is None
            assert coordinator.journal.active_run_id() is not None

            resumed = client.post(f"/api/v1/coding/session/{session_id}/resume")
            assert resumed.status_code == 200
            assert app.state.coding_sessions[session_id] is original_runtime

            allow_start.set()
            while _receive_runtime_event(websocket)["type"] != "final":
                pass


def test_get_coding_session_messages_returns_persisted_chat_history(tmp_path: Path) -> None:
    """GET /coding/session/{id}/messages replays persisted user/assistant messages."""
    (tmp_path / "README.md").write_text("Sage messages\n", encoding="utf-8")
    client = TestClient(
        create_app(
            coding_model_factory=FakeModel,
            coding_workspace_root=tmp_path,
            coding_storage_root=tmp_path / ".coding",
        )
    )
    session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
    with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
        websocket.send_json({"content": "读 README.md"})
        while _receive_runtime_event(websocket)["type"] != "final":
            pass

    response = client.get(f"/api/v1/coding/session/{session_id}/messages")

    assert response.status_code == 200
    assert response.json()["messages"] == [
        {
            "role": "user",
            "content": "读 README.md",
            "created_at": response.json()["messages"][0]["created_at"],
        },
        {
            "role": "assistant",
            "content": "README 里能看到项目内容。",
            "created_at": response.json()["messages"][1]["created_at"],
        },
    ]


def test_create_coding_session_rejects_workspace_outside_configured_root(
    tmp_path: Path,
) -> None:
    """Workspace overrides must stay within the configured coding root."""
    outside_root = tmp_path.parent / f"{tmp_path.name}-outside"
    outside_root.mkdir()
    client = TestClient(
        create_app(
            coding_model_factory=FakeModel,
            coding_workspace_root=tmp_path,
            coding_storage_root=tmp_path / ".coding",
        )
    )

    response = client.post(
        "/api/v1/coding/session",
        json={"workspace_root": str(outside_root)},
    )

    assert response.status_code == 400
    assert "configured coding workspace" in response.json()["detail"]


def test_coding_websocket_streams_engine_events(tmp_path: Path) -> None:
    """Coding WebSocket streams tool and final events from the runtime."""
    (tmp_path / "README.md").write_text("TourSwarm API coding\n", encoding="utf-8")
    client = TestClient(
        create_app(
            coding_model_factory=FakeModel,
            coding_workspace_root=tmp_path,
            coding_storage_root=tmp_path / ".coding",
        )
    )
    session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]

    with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
        websocket.send_json({"content": "读 README.md"})
        events = _receive_until(websocket, "final")

    assert any(event["type"] == "context_usage_updated" for event in events)
    business_events = [event for event in events if event["type"] != "context_usage_updated"]
    assert [event["type"] for event in business_events] == [
        "model_requested",
        "model_parsed",
        "tool_call",
        "tool_result",
        "model_requested",
        "model_parsed",
        "final",
    ]
    assert business_events[2]["tool"] == "read_file"
    assert "TourSwarm API coding" in business_events[3]["content"]
    assert business_events[-1]["content"] == "README 里能看到项目内容。"


def test_coding_approval_pending_and_respond_endpoints(tmp_path: Path) -> None:
    """Approval pending/respond endpoints expose the runtime queue."""
    app = create_app(
        coding_model_factory=FakeModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
    )
    client = TestClient(app)
    session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
    runtime = app.state.coding_sessions[session_id]
    entry = runtime.approval_manager.submit(
        session_id,
        "write_file",
        {"path": "note.txt"},
        "write_file requires approval.",
        "tool:write_file",
    )

    pending = client.get(f"/api/v1/coding/{session_id}/approval/pending")
    response = client.post(
        f"/api/v1/coding/{session_id}/approval/respond",
        json={"approval_id": entry.approval_id, "choice": "once"},
    )

    assert pending.status_code == 200
    assert pending.json()["approval_id"] == entry.approval_id
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert entry.event.is_set()


def test_coding_websocket_waits_for_approval_then_continues(tmp_path: Path) -> None:
    """ask policy emits approval_required and resumes after approval response."""
    client = TestClient(
        create_app(
            coding_model_factory=FakeWriteModel,
            coding_workspace_root=tmp_path,
            coding_storage_root=tmp_path / ".coding",
        )
    )
    session_id = client.post(
        "/api/v1/coding/session",
        json={"approval_policy": "ask"},
    ).json()["session_id"]

    with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
        websocket.send_json({"content": "写一个 note"})
        first_events = _receive_until(websocket, "approval_required")
        approval = first_events[-1]
        response = client.post(
            f"/api/v1/coding/{session_id}/approval/respond",
            json={"approval_id": approval["approval_id"], "choice": "once"},
        )
        remaining_events = _receive_until(websocket, "final")

    first_business_events = [
        event for event in first_events if event["type"] != "context_usage_updated"
    ]
    remaining_business_events = [
        event for event in remaining_events if event["type"] != "context_usage_updated"
    ]
    assert [event["type"] for event in first_business_events] == [
        "model_requested",
        "model_parsed",
        "approval_required",
    ]
    assert approval["tool"] == "write_file"
    assert response.status_code == 200
    assert [event["type"] for event in remaining_business_events] == [
        "approval_granted",
        "tool_call",
        "tool_result",
        "model_requested",
        "model_parsed",
        "final",
    ]
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "approved"


def test_stop_coding_run_rejects_uncoordinated_runtime_state(tmp_path: Path) -> None:
    """Runtime state alone cannot authorize cancellation without a server task."""
    app = create_app(
        coding_model_factory=FakeModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
    )
    client = TestClient(app)
    session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
    runtime = app.state.coding_sessions[session_id]
    runtime.active_run_id = "run_active"

    response = client.post(
        f"/api/v1/coding/{session_id}/run/stop", json={"run_id": "run_active"}
    )

    assert response.status_code == 200
    assert response.json() == {"ok": False}
    assert app.state.coding_sessions[session_id].stop_requested is False


def test_stop_coding_run_without_body_requires_run_id(tmp_path: Path) -> None:
    """POST /run/stop cannot affect a run without an explicit run id."""
    app = create_app(
        coding_model_factory=FakeModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
    )
    client = TestClient(app)
    session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
    response = client.post(
        f"/api/v1/coding/{session_id}/run/stop",
        content=b"",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422


def test_stop_coding_run_rejects_stale_run_id(tmp_path: Path) -> None:
    """POST /run/stop with a stale run_id does not affect the active run."""
    app = create_app(
        coding_model_factory=FakeModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
    )
    client = TestClient(app)
    session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
    runtime = app.state.coding_sessions[session_id]
    runtime.active_run_id = "run_new"
    assert runtime.stop_requested is False

    response = client.post(
        f"/api/v1/coding/{session_id}/run/stop", json={"run_id": "run_old"}
    )

    assert response.status_code == 200
    assert response.json() == {"ok": False}
    assert runtime.stop_requested is False
    assert runtime.active_run_id == "run_new"


def test_switch_permission_mode(tmp_path: Path) -> None:
    """PATCH /permission-mode switches the runtime permission mode."""
    app = create_app(
        coding_model_factory=FakeModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
    )
    client = TestClient(app)
    session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
    runtime = app.state.coding_sessions[session_id]
    assert runtime.permission_mode == "default"

    response = client.patch(
        f"/api/v1/coding/{session_id}/permission-mode", json={"mode": "accept_edits"}
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "mode": "accept_edits"}
    assert runtime.permission_mode == "accept_edits"


def test_plan_approve_exits_plan_mode(tmp_path: Path) -> None:
    """POST /plan/approve resolves a pending review and exits plan mode."""
    app = create_app(
        coding_model_factory=FakeModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
    )
    client = TestClient(app)
    session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
    runtime = app.state.coding_sessions[session_id]
    runtime.enter_plan_mode("Refactor API")
    # Submit a pending plan review (as exit_plan_mode would).
    runtime.request_plan_exit()

    response = client.post(f"/api/v1/coding/{session_id}/plan/approve")

    assert response.status_code == 200
    assert response.json() == {"status": "approved", "mode": "default"}
    assert runtime.runtime_mode == "default"
    assert runtime.plan_review_manager.pending is None


def test_plan_reject_keeps_plan_mode(tmp_path: Path) -> None:
    """POST /plan/reject resolves a pending review while staying in plan mode."""
    app = create_app(
        coding_model_factory=FakeModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
    )
    client = TestClient(app)
    session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
    runtime = app.state.coding_sessions[session_id]
    runtime.enter_plan_mode("Refactor API")
    # Submit a pending plan review (as exit_plan_mode would).
    runtime.request_plan_exit()

    response = client.post(f"/api/v1/coding/{session_id}/plan/reject")

    assert response.status_code == 200
    assert response.json() == {"status": "rejected", "mode": "plan"}
    assert runtime.runtime_mode == "plan"
    assert runtime.plan_review_manager.pending is None


def test_plan_approve_rejects_when_no_pending_review(tmp_path: Path) -> None:
    """POST /plan/approve returns 400 when no review is pending."""
    app = create_app(
        coding_model_factory=FakeModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
    )
    client = TestClient(app)
    session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
    runtime = app.state.coding_sessions[session_id]
    runtime.enter_plan_mode("Refactor API")
    # Submit then resolve a review so nothing is left outstanding.
    runtime.request_plan_exit()
    runtime.plan_review_manager.resolve("rejected")

    response = client.post(f"/api/v1/coding/{session_id}/plan/approve")

    assert response.status_code == 400
    assert response.json()["detail"] == "no pending plan review"


def test_plan_approve_rejects_when_not_in_plan_mode(tmp_path: Path) -> None:
    """POST /plan/approve returns 400 when the runtime is not in plan mode."""
    app = create_app(
        coding_model_factory=FakeModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
    )
    client = TestClient(app)
    session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]

    response = client.post(f"/api/v1/coding/{session_id}/plan/approve")

    assert response.status_code == 400
    assert response.json()["detail"] == "not in plan mode"


def test_coding_websocket_streams_plan_ready_then_approve_exits(tmp_path: Path) -> None:
    """exit_plan_mode yields plan_ready_for_review; approve via REST exits plan mode."""
    (tmp_path / "README.md").write_text("# Sage\n", encoding="utf-8")

    class PlanExitModel:
        """Model that activates and calls exit_plan_mode, then returns a final."""

        def __init__(self) -> None:
            self.responses = [
                '<tool>{"name":"tool_search","args":{"query":"plan"}}</tool>',
                '<tool>{"name":"exit_plan_mode","args":{}}</tool>',
                "<final>Plan is ready for your review.</final>",
            ]

        async def complete(self, prompt: str) -> str:
            _ = prompt
            return self.responses.pop(0)

    app = create_app(
        coding_model_factory=PlanExitModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
    )
    client = TestClient(app)
    session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
    runtime = app.state.coding_sessions[session_id]
    # Seed plan mode + a plan file on disk before the turn.
    plan_path = runtime.enter_plan_mode("Refactor API")
    (tmp_path / plan_path).write_text("# Refactor plan\nstep 1\n", encoding="utf-8")

    with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
        websocket.send_json({"content": "退出规划模式"})
        events: list[dict] = []
        while True:
            event = _receive_runtime_event(websocket)
            events.append(event)
            if event["type"] == "final":
                break

    types = [event["type"] for event in events]
    assert "plan_ready_for_review" in types
    review_event = events[types.index("plan_ready_for_review")]
    assert review_event["plan_path"] == ".coding/plans/refactor-api-plan.md"
    assert "# Refactor plan" in review_event["summary"]
    # Still in plan mode pending user approval.
    assert runtime.runtime_mode == "plan"

    # User approves via the REST API.
    response = client.post(f"/api/v1/coding/{session_id}/plan/approve")
    assert response.status_code == 200
    assert response.json()["mode"] == "default"
    assert runtime.runtime_mode == "default"


def test_pending_plan_review_remains_available_after_reconnect(tmp_path: Path) -> None:
    """Plan review state survives socket churn without unjournaled sync events."""
    app = create_app(
        coding_model_factory=FakeModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
    )
    client = TestClient(app)
    session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
    runtime = app.state.coding_sessions[session_id]
    runtime.enter_plan_mode("Refactor API")
    runtime.request_plan_exit()

    with client.websocket_connect(f"/api/v1/coding/{session_id}/stream"):
        pass

    pending = runtime.plan_review_manager.pending
    assert pending is not None
    assert pending.review_id
    assert pending.plan_path == ".coding/plans/refactor-api-plan.md"
    assert "# Refactor" not in pending.summary  # no plan file was written
    assert runtime.runtime_mode == "plan"


def test_coding_run_history_lists_and_reads_traces(tmp_path: Path) -> None:
    """Run history endpoints expose persisted coding run traces."""
    (tmp_path / "README.md").write_text("Sage run history\n", encoding="utf-8")
    client = TestClient(
        create_app(
            coding_model_factory=FakeModel,
            coding_workspace_root=tmp_path,
            coding_storage_root=tmp_path / ".coding",
        )
    )
    session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
    with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
        websocket.send_json({"content": "读 README.md"})
        while True:
            envelope = websocket.receive_json()
            if envelope["kind"] == "terminal":
                break

    list_response = client.get(f"/api/v1/coding/{session_id}/runs")
    run = list_response.json()["runs"][0]
    detail_response = client.get(f"/api/v1/coding/{session_id}/runs/{run['run_id']}")

    assert list_response.status_code == 200
    assert run["status"] == "completed"
    assert run["tool_count"] == 1
    # workspace_diff_ready is the last business event (run_finished/turn_finished
    # are excluded from the business event set). The run only reads README.md so
    # no files changed -> changed_files is empty.
    assert run["last_event_type"] == "workspace_diff_ready"
    assert run["changed_files"] == []
    assert run["audit"]["headline"] == "运行完成 · 1 项工具"
    assert run["audit"]["steps"][0]["tool"] == "read_file"
    assert run["audit"]["steps"][0]["result_preview"] == "已读取文件内容（摘要不展示正文）"
    assert detail_response.status_code == 200
    assert detail_response.json()["run_id"] == run["run_id"]
    assert detail_response.json()["audit"] == run["audit"]
    assert [event["type"] for event in detail_response.json()["events"]][-3:] == [
        "workspace_diff_ready",
        "run_finished",
        "turn_finished",
    ]
    assert [entry["title"] for entry in detail_response.json()["timeline"]] == [
        "Model request",
        "Parsed tool",
        "Run read_file",
        "read_file succeeded",
        "Model request",
        "Parsed final",
        "Final answer",
        "Run finished",
    ]


def _make_client(tmp_path: Path) -> TestClient:
    """Create a coding-enabled app with a README in the workspace."""
    (tmp_path / "README.md").write_text("# Sage\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    return TestClient(
        create_app(
            coding_model_factory=FakeModel,
            coding_workspace_root=tmp_path,
            coding_storage_root=tmp_path / ".coding",
        )
    )


def _make_bare_client(tmp_path: Path) -> TestClient:
    """Create a coding-enabled app without seeding workspace files."""
    return TestClient(
        create_app(
            coding_model_factory=FakeModel,
            coding_workspace_root=tmp_path,
            coding_storage_root=tmp_path / ".coding",
        )
    )


def test_list_coding_files_returns_directory_entries(tmp_path: Path) -> None:
    """GET /files returns dirs first then files, ignoring noise."""
    client = _make_client(tmp_path)
    (tmp_path / ".sage").mkdir()
    (tmp_path / ".sage" / "usage.sqlite3").write_text("private", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=private\n", encoding="utf-8")
    session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]

    response = client.get(f"/api/v1/coding/{session_id}/files", params={"path": "."})

    assert response.status_code == 200
    entries = response.json()["entries"]
    names = [entry["name"] for entry in entries]
    assert "src" in names
    assert "README.md" in names
    assert ".sage" not in names
    assert ".env" not in names
    src_entry = next(entry for entry in entries if entry["name"] == "src")
    assert src_entry["is_dir"] is True


def test_read_coding_file_returns_content(tmp_path: Path) -> None:
    """GET /file returns file content and line count."""
    client = _make_client(tmp_path)
    session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]

    response = client.get(f"/api/v1/coding/{session_id}/file", params={"path": "README.md"})

    assert response.status_code == 200
    data = response.json()
    assert "# Sage" in data["content"]
    assert data["lines"] >= 1


def test_git_status_returns_branch_info(tmp_path: Path) -> None:
    """GET /git/status returns is_git + branch + dirty_count."""
    import subprocess

    try:
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, timeout=5)
        subprocess.run(
            ["git", "config", "user.email", "t@t.com"],
            cwd=tmp_path,
            capture_output=True,
            timeout=5,
        )
        subprocess.run(
            ["git", "config", "user.name", "test"],
            cwd=tmp_path,
            capture_output=True,
            timeout=5,
        )
        subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True, timeout=5)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=tmp_path,
            capture_output=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return  # git not available, skip

    (tmp_path / "README.md").write_text("# Sage modified\n", encoding="utf-8")
    client = _make_client(tmp_path)
    session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]

    response = client.get(f"/api/v1/coding/{session_id}/git/status")

    assert response.status_code == 200
    data = response.json()
    assert data["is_git"] is True
    assert data["branch"] in {"main", "master"}
    assert data["dirty_count"] >= 1


def test_list_coding_models_returns_providers(tmp_path: Path) -> None:
    """GET /models returns the simplified deepseek v4 flash/pro set."""
    client = _make_bare_client(tmp_path)

    response = client.get("/api/v1/coding/models")

    assert response.status_code == 200
    data = response.json()
    models = data["models"]
    assert len(models) == 2
    ids = {m["id"] for m in models}
    assert ids == {"deepseek:deepseek-v4-flash", "deepseek:deepseek-v4-pro"}
    assert all("id" in m and "provider" in m for m in models)
    assert all(m["context_configured"] is True for m in models)
    assert all(m["context_window_tokens"] == 1_000_000 for m in models)
    assert all(m["reasoning_modes"] == [] for m in models)
    assert data["current"] == "deepseek:deepseek-v4-flash"
    assert data["runtime_profiles"] == ["legacy"]


def test_list_coding_models_advertises_only_safe_runtime_profiles(tmp_path: Path) -> None:
    development = TestClient(
        create_app(
            coding_model_factory=FakeModel,
            coding_workspace_root=tmp_path,
            coding_storage_root=tmp_path / ".coding-dev",
            coding_deerflow_v2_enabled=True,
        )
    )
    production = TestClient(
        create_app(
            coding_model_factory=FakeModel,
            coding_workspace_root=tmp_path,
            coding_storage_root=tmp_path / ".coding-prod",
            coding_deerflow_v2_enabled=True,
            cloud_app_env="production",
            cloud_repository=object(),
        )
    )
    isolated_production = TestClient(
        create_app(
            coding_model_factory=FakeModel,
            coding_workspace_root=tmp_path,
            coding_storage_root=tmp_path / ".coding-prod-container",
            coding_deerflow_v2_enabled=True,
            coding_sandbox_provider="container",
            cloud_app_env="production",
            cloud_repository=object(),
        )
    )

    assert development.get("/api/v1/coding/models").json()["runtime_profiles"] == [
        "legacy",
        "deerflow_v2",
    ]
    assert production.get("/api/v1/coding/models").json()["runtime_profiles"] == [
        "legacy"
    ]
    assert isolated_production.get("/api/v1/coding/models").json()[
        "runtime_profiles"
    ] == ["legacy", "deerflow_v2"]


def test_app_rejects_unknown_or_empty_sandbox_configuration(tmp_path: Path) -> None:
    from sage_harness import SandboxPolicyError

    with pytest.raises(SandboxPolicyError, match="unknown sandbox provider"):
        create_app(
            coding_model_factory=FakeModel,
            coding_workspace_root=tmp_path,
            coding_storage_root=tmp_path / ".coding-unknown",
            coding_sandbox_provider="host",
        )
    with pytest.raises(ValueError, match="image must not be empty"):
        create_app(
            coding_model_factory=FakeModel,
            coding_workspace_root=tmp_path,
            coding_storage_root=tmp_path / ".coding-empty-image",
            coding_sandbox_image=" ",
        )


def test_list_coding_skills_returns_bundled_skills(tmp_path: Path) -> None:
    """GET /skills returns bundled coding and domain skills."""
    client = _make_bare_client(tmp_path)

    response = client.get("/api/v1/coding/skills")

    assert response.status_code == 200
    skills = response.json()["skills"]
    names = [skill["name"] for skill in skills]
    assert set(names) >= {"review", "test", "commit", "travel", "travel-planning"}


def test_get_coding_skill_returns_content(tmp_path: Path) -> None:
    """GET /skills/{name} returns the skill body."""
    client = _make_bare_client(tmp_path)

    response = client.get("/api/v1/coding/skills/review")

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "review"
    assert "git diff" in data["content"]


def test_get_coding_skill_404_for_unknown(tmp_path: Path) -> None:
    """GET /skills/unknown returns 404."""
    client = _make_bare_client(tmp_path)

    response = client.get("/api/v1/coding/skills/nonexistent")

    assert response.status_code == 404


def test_list_mcp_servers_returns_config(tmp_path: Path) -> None:
    """GET /mcp/servers returns configured server list."""
    client = _make_bare_client(tmp_path)

    response = client.get("/api/v1/coding/mcp/servers")

    assert response.status_code == 200
    servers = response.json()["servers"]
    names = [server["name"] for server in servers]
    assert "amap" in names
    assert "weather" in names


def test_coding_websocket_handles_slash_command(tmp_path: Path) -> None:
    """Slash command /review is expanded and triggers a skill_invoked event."""
    (tmp_path / "README.md").write_text("# Sage\n", encoding="utf-8")
    client = TestClient(
        create_app(
            coding_model_factory=FakeModel,
            coding_workspace_root=tmp_path,
            coding_storage_root=tmp_path / ".coding",
        )
    )
    session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]

    with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
        websocket.send_json({"content": "/review"})
        events = []
        while True:
            event = _receive_runtime_event(websocket)
            events.append(event)
            if event["type"] in {"final", "step_limit", "error"}:
                break

    skill_event = next(event for event in events if event["type"] == "skill_invoked")
    assert skill_event["skill"] == "review"


def test_coding_websocket_unknown_skill_returns_error(tmp_path: Path) -> None:
    """Unknown slash command returns an error event."""
    client = TestClient(
        create_app(
            coding_model_factory=FakeModel,
            coding_workspace_root=tmp_path,
            coding_storage_root=tmp_path / ".coding",
        )
    )
    session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]

    with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
        websocket.send_json({"content": "/nonexistent"})
        event = _receive_runtime_event(websocket)

    assert event["type"] == "error"
    assert "Unknown skill" in event["message"]


def test_coding_websocket_slash_command_persists_original_text(tmp_path: Path) -> None:
    """Slash command original text is persisted to history, not the expanded prompt."""
    (tmp_path / "README.md").write_text("# Sage\n", encoding="utf-8")

    class PromptRecordingModel:
        """Model that records the prompt and returns a final answer."""

        def __init__(self) -> None:
            self.prompts: list[str] = []

        async def complete(self, prompt: str) -> str:
            self.prompts.append(prompt)
            return "<final>已审查。</final>"

    app = create_app(
        coding_model_factory=PromptRecordingModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
    )
    client = TestClient(app)
    session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
    runtime = app.state.coding_sessions[session_id]

    with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
        websocket.send_json({"content": "/review"})
        events = []
        while True:
            event = _receive_runtime_event(websocket)
            events.append(event)
            if event["type"] in {"final", "step_limit", "error"}:
                break

    # The skill_invoked event still fires.
    skill_event = next(event for event in events if event["type"] == "skill_invoked")
    assert skill_event["skill"] == "review"

    # History stores the original slash command text, not the expanded prompt.
    history = runtime.session["history"]
    user_messages = [item for item in history if item.get("role") == "user"]
    assert len(user_messages) == 1
    assert user_messages[0]["content"] == "/review"
    assert user_messages[0]["message_id"]
    assert user_messages[0]["sequence"] == 1
    # The expanded review prompt (git diff instructions) is never persisted to history.
    assert all("git diff" not in str(item.get("content", "")) for item in history)

    # The expanded skill prompt IS injected into the LLM request for this turn.
    assert "git diff" in runtime.model.prompts[0]


def test_coding_websocket_slash_command_with_args_keeps_original(tmp_path: Path) -> None:
    """Slash command with arguments keeps the raw /command args text in history."""
    (tmp_path / "README.md").write_text("# Sage\n", encoding="utf-8")

    class FinalModel:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        async def complete(self, prompt: str) -> str:
            self.prompts.append(prompt)
            return "<final>我来帮你规划。</final>"

    app = create_app(
        coding_model_factory=FinalModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
    )
    client = TestClient(app)
    session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
    runtime = app.state.coding_sessions[session_id]

    with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
        websocket.send_json({"content": "/travel-planning 我要去莆田"})
        while _receive_runtime_event(websocket)["type"] not in {"final", "step_limit", "error"}:
            pass

    # History keeps the original slash command including the user's arguments.
    user_messages = [item for item in runtime.session["history"] if item.get("role") == "user"]
    assert user_messages[0]["content"] == "/travel-planning 我要去莆田"
    # The expanded prompt body is injected into the LLM request but not history.
    assert "你正在使用 Sage 的 travel-planning domain skill" in runtime.model.prompts[0]
    assert all(
        "你正在使用 Sage 的 travel-planning domain skill" not in str(item.get("content", ""))
        for item in runtime.session["history"]
    )

    # Replayed messages also expose only the original command text.
    messages = client.get(f"/api/v1/coding/session/{session_id}/messages").json()["messages"]
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "/travel-planning 我要去莆田"


def test_create_coding_session_persists_recoverable_empty_session(tmp_path: Path) -> None:
    """A fresh session is discoverable before its first run can crash."""
    options = {
        "coding_model_factory": FakeModel,
        "coding_workspace_root": tmp_path,
        "coding_storage_root": tmp_path / ".coding",
    }
    with TestClient(create_app(**options)) as client:
        session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]

    with TestClient(create_app(**options)) as restarted:
        response = restarted.post(f"/api/v1/coding/session/{session_id}/resume")

    assert response.status_code == 200
    assert response.json()["session_id"] == session_id


def test_get_run_diff_returns_artifact(tmp_path: Path) -> None:
    """GET /runs/{run_id}/diff returns the workspace diff artifact after a run."""
    (tmp_path / "README.md").write_text("# Sage\n", encoding="utf-8")
    client = TestClient(
        create_app(
            coding_model_factory=FakeWriteModel,
            coding_workspace_root=tmp_path,
            coding_storage_root=tmp_path / ".coding",
        )
    )
    session_id = client.post(
        "/api/v1/coding/session", json={"approval_policy": "auto"}
    ).json()["session_id"]
    # accept_edits auto-approves write_file so the turn completes without a
    # manual approval round-trip.
    client.patch(
        f"/api/v1/coding/{session_id}/permission-mode", json={"mode": "accept_edits"}
    )

    # Run a turn that writes note.txt via FakeWriteModel.
    with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
        websocket.send_json({"content": "写一个 note"})
        while True:
            event = _receive_runtime_event(websocket)
            if event["type"] == "turn_finished":
                break

    run_id = event["run_id"]
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "approved"

    response = client.get(f"/api/v1/coding/{session_id}/runs/{run_id}/diff")

    assert response.status_code == 200
    data = response.json()
    assert data["run_id"] == run_id
    assert data["file_count"] >= 1
    paths = {change["path"] for change in data["changed_files"]}
    assert "note.txt" in paths
    note_change = next(c for c in data["changed_files"] if c["path"] == "note.txt")
    assert note_change["status"] == "added"
    assert note_change["after_hash"]
    assert "approved" in note_change["diff"]


def test_get_run_diff_404_for_unknown(tmp_path: Path) -> None:
    """GET /runs/{run_id}/diff returns 404 for a run with no diff artifact."""
    client = TestClient(
        create_app(
            coding_model_factory=FakeModel,
            coding_workspace_root=tmp_path,
            coding_storage_root=tmp_path / ".coding",
        )
    )
    session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]

    response = client.get(
        f"/api/v1/coding/{session_id}/runs/run_does_not_exist/diff"
    )

    assert response.status_code == 404
    assert "diff not found" in response.json()["detail"]
