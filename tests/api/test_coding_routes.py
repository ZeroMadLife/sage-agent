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
    """GET /models returns one entry per provider."""
    client = _make_bare_client(tmp_path)

    response = client.get("/api/v1/coding/models")

    assert response.status_code == 200
    models = response.json()["models"]
    assert len(models) >= 1
    assert all("id" in m and "provider" in m for m in models)


def test_list_coding_skills_returns_three_bundled(tmp_path: Path) -> None:
    """GET /skills returns the three bundled skills."""
    client = _make_bare_client(tmp_path)

    response = client.get("/api/v1/coding/skills")

    assert response.status_code == 200
    skills = response.json()["skills"]
    names = [skill["name"] for skill in skills]
    assert "review" in names
    assert "test" in names
    assert "commit" in names


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
