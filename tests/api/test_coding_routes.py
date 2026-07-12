"""Coding API route tests."""

from pathlib import Path

from fastapi.testclient import TestClient

from api.main import create_app


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
    # Run a turn so the session is persisted (save_on_init=False) and non-empty.
    with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
        websocket.send_json({"content": "读 README.md"})
        while websocket.receive_json()["type"] != "final":
            pass

    response = client.get("/api/v1/coding/sessions")

    assert response.status_code == 200
    sessions = response.json()["sessions"]
    assert sessions[0]["session_id"] == session_id
    assert sessions[0]["workspace_root"] == str(tmp_path.resolve())
    assert sessions[0]["runtime_mode"] == "default"


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
        while websocket.receive_json()["type"] != "final":
            pass
    app.state.coding_sessions.clear()

    response = client.post(f"/api/v1/coding/session/{session_id}/resume")

    assert response.status_code == 200
    assert response.json()["session_id"] == session_id
    assert response.json()["permission_mode"] == "default"
    assert session_id in app.state.coding_sessions
    assert app.state.coding_sessions[session_id].session["history"][0]["content"] == "读 README.md"


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
        while websocket.receive_json()["type"] != "final":
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
        events = [websocket.receive_json() for _ in range(7)]

    assert [event["type"] for event in events] == [
        "model_requested",
        "model_parsed",
        "tool_call",
        "tool_result",
        "model_requested",
        "model_parsed",
        "final",
    ]
    assert events[2]["tool"] == "read_file"
    assert "TourSwarm API coding" in events[3]["content"]
    assert events[-1]["content"] == "README 里能看到项目内容。"


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
        first_events = [websocket.receive_json() for _ in range(3)]
        approval = first_events[-1]
        response = client.post(
            f"/api/v1/coding/{session_id}/approval/respond",
            json={"approval_id": approval["approval_id"], "choice": "once"},
        )
        remaining_events = [websocket.receive_json() for _ in range(6)]

    assert [event["type"] for event in first_events] == [
        "model_requested",
        "model_parsed",
        "approval_required",
    ]
    assert approval["tool"] == "write_file"
    assert response.status_code == 200
    assert [event["type"] for event in remaining_events] == [
        "approval_granted",
        "tool_call",
        "tool_result",
        "model_requested",
        "model_parsed",
        "final",
    ]
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "approved"


def test_stop_coding_run_marks_runtime_stop_requested(tmp_path: Path) -> None:
    """POST /run/stop requests cancellation for the active coding run."""
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
    assert response.json() == {"ok": True}
    assert app.state.coding_sessions[session_id].stop_requested is True


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


def test_stop_coding_run_with_matching_run_id(tmp_path: Path) -> None:
    """POST /run/stop with a run_id matching the active run is accepted."""
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
    assert response.json() == {"ok": True}
    assert runtime.stop_requested is True


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
            event = websocket.receive_json()
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


def test_coding_websocket_resurfaces_pending_plan_review_on_reconnect(tmp_path: Path) -> None:
    """A reconnecting client re-receives plan_ready_for_review + plan mode."""
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

    with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
        mode_sync = websocket.receive_json()
        review_sync = websocket.receive_json()

    assert mode_sync["type"] == "runtime_mode_changed"
    assert mode_sync["mode"] == "plan"
    assert mode_sync["plan_path"] == ".coding/plans/refactor-api-plan.md"
    assert review_sync["type"] == "plan_ready_for_review"
    assert review_sync["review_id"]
    assert "# Refactor" not in review_sync["summary"]  # no plan file was written
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
            event = websocket.receive_json()
            if event["type"] == "final":
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
    assert detail_response.status_code == 200
    assert detail_response.json()["run_id"] == run["run_id"]
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
    session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]

    response = client.get(f"/api/v1/coding/{session_id}/files", params={"path": "."})

    assert response.status_code == 200
    entries = response.json()["entries"]
    names = [entry["name"] for entry in entries]
    assert "src" in names
    assert "README.md" in names
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
    assert data["current"] == "deepseek:deepseek-v4-flash"


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
            event = websocket.receive_json()
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
        event = websocket.receive_json()

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
            event = websocket.receive_json()
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
        while websocket.receive_json()["type"] not in {"final", "step_limit", "error"}:
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


def test_create_coding_session_does_not_persist_empty_session(tmp_path: Path) -> None:
    """A freshly created session is not persisted until the first turn runs."""
    client = TestClient(
        create_app(
            coding_model_factory=FakeModel,
            coding_workspace_root=tmp_path,
            coding_storage_root=tmp_path / ".coding",
        )
    )
    session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]

    # No empty session shows up in the session list.
    response = client.get("/api/v1/coding/sessions")
    assert response.status_code == 200
    session_ids = [item["session_id"] for item in response.json()["sessions"]]
    assert session_id not in session_ids


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
            event = websocket.receive_json()
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
