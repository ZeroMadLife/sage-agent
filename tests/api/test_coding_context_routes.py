"""Context-control REST API contract tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from api.main import create_app
from core.coding.context import CompactionResult


class Model:
    def __init__(self, model_id: str = "model-a") -> None:
        self.model_id = model_id

    async def complete(self, prompt: str) -> str:
        del prompt
        return "<final>done</final>"

    def get_num_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)


CATALOG = [
    {"id": "model-a", "label": "Model A", "provider": "test"},
    {"id": "model-b", "label": "Model B", "provider": "test"},
]
CAPABILITIES = {
    "model-a": {"context_window_tokens": 100_000, "output_reserve_tokens": 10_000},
}


def _app(tmp_path: Path, *, configured: bool = True) -> Any:
    def factory(model_id: str = "model-a") -> Model:
        return Model(model_id)

    return create_app(
        coding_model_factory=factory,
        coding_model_catalog=CATALOG,
        coding_model_capabilities=CAPABILITIES if configured else {},
        coding_default_model="model-a",
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
        coding_checkpoint_anchor_key=b"k" * 32,
    )


def _session(client: TestClient) -> str:
    response = client.post("/api/v1/coding/session", json={})
    assert response.status_code == 200
    return str(response.json()["session_id"])


def test_context_snapshot_reports_explicit_configured_budget(tmp_path: Path) -> None:
    app = _app(tmp_path)
    client = TestClient(app)
    session_id = _session(client)

    response = client.get(f"/api/v1/coding/{session_id}/context")

    assert response.status_code == 200
    data = response.json()
    assert data["configured"] is True
    assert data["model_id"] == "model-a"
    assert data["model_limit_tokens"] == 100_000
    assert data["output_reserve_tokens"] == 10_000
    assert data["effective_limit_tokens"] == 90_000
    assert data["used_tokens"] >= 1
    assert data["estimated"] is False
    assert data["compactable"] is True
    assert data["active_run_id"] is None
    assert data["latest_attempt"] is None
    assert data["stale_started"] is False


def test_context_snapshot_does_not_guess_unconfigured_window(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path, configured=False))
    session_id = _session(client)

    response = client.get(f"/api/v1/coding/{session_id}/context")

    assert response.status_code == 200
    data = response.json()
    assert data["configured"] is False
    assert data["model_limit_tokens"] is None
    assert data["effective_limit_tokens"] is None
    assert data["used_tokens"] is None
    assert data["usage_ratio"] is None
    assert data["level"] == "unconfigured"
    assert data["compactable"] is False


def test_manual_compact_rejects_unconfigured_and_active_context(tmp_path: Path) -> None:
    unconfigured_app = _app(tmp_path / "u", configured=False)
    (tmp_path / "u").mkdir()
    unconfigured = TestClient(unconfigured_app)
    session_id = _session(unconfigured)
    response = unconfigured.post(
        f"/api/v1/coding/{session_id}/context/compact", json={"focus": "x"}
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "context window is not configured"

    configured_root = tmp_path / "c"
    configured_root.mkdir()
    app = _app(configured_root)
    client = TestClient(app)
    configured_id = _session(client)
    app.state.coding_sessions[configured_id].active_run_id = "run-active"
    response = client.post(
        f"/api/v1/coding/{configured_id}/context/compact", json={"focus": "x"}
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "context operation is busy"


def test_manual_compact_rejects_busy_lock_and_oversized_focus(tmp_path: Path) -> None:
    app = _app(tmp_path)
    client = TestClient(app)
    session_id = _session(client)
    runtime = app.state.coding_sessions[session_id]

    class BusyLock:
        def locked(self) -> bool:
            return True

    runtime._context_operation_lock = BusyLock()
    busy = client.post(
        f"/api/v1/coding/{session_id}/context/compact", json={"focus": "x"}
    )
    assert busy.status_code == 409
    oversized = client.post(
        f"/api/v1/coding/{session_id}/context/compact",
        json={"focus": "x" * 4001},
    )
    assert oversized.status_code == 422


def test_manual_compact_success_returns_snapshot_after_persistence(
    tmp_path: Path, monkeypatch: Any
) -> None:
    app = _app(tmp_path)
    client = TestClient(app)
    session_id = _session(client)
    runtime = app.state.coding_sessions[session_id]

    async def compact(focus: str = "") -> CompactionResult:
        assert focus == "keep decisions"
        runtime.compaction_store.begin(session_id, "compact-test", {"trigger": "manual"})
        result = CompactionResult(
            applied=False,
            projected_history=[],
            checkpoint=None,
            before_tokens=10,
            after_tokens=10,
            archived_items=0,
            reason="nothing_to_compact",
            compaction_id="compact-test",
            trigger="manual",
            retryable=False,
        )
        runtime.compaction_store.fail(session_id, "compact-test", result)
        return result

    monkeypatch.setattr(runtime, "manual_compact", compact)
    response = client.post(
        f"/api/v1/coding/{session_id}/context/compact",
        json={"focus": "keep decisions"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["compaction_id"] == "compact-test"
    assert data["applied"] is False
    assert data["reason"] == "nothing_to_compact"
    assert runtime.compaction_store.load(session_id, "compact-test")["status"] == "failed"


def test_manual_compact_hides_internal_failure_detail(tmp_path: Path, monkeypatch: Any) -> None:
    app = _app(tmp_path)
    client = TestClient(app)
    session_id = _session(client)
    runtime = app.state.coding_sessions[session_id]

    async def fail(focus: str = "") -> CompactionResult:
        del focus
        raise RuntimeError("secret-provider-payload")

    monkeypatch.setattr(runtime, "manual_compact", fail)
    response = client.post(
        f"/api/v1/coding/{session_id}/context/compact", json={"focus": "x"}
    )
    assert response.status_code == 500
    assert response.json()["detail"] == "context compaction failed"
    assert "secret-provider-payload" not in response.text


def test_context_snapshot_marks_old_started_attempt_stale(
    tmp_path: Path, monkeypatch: Any
) -> None:
    app = _app(tmp_path)
    client = TestClient(app)
    session_id = _session(client)
    runtime = app.state.coding_sessions[session_id]
    runtime.compaction_store.begin(session_id, "compact-stale", {"trigger": "manual"})

    import core.coding.runtime as runtime_module

    real_datetime = datetime

    class FutureDateTime:
        @staticmethod
        def fromisoformat(value: str) -> datetime:
            return real_datetime.fromisoformat(value)

        @staticmethod
        def now(tz: Any = None) -> datetime:
            return real_datetime.now(UTC) + timedelta(minutes=6)

    monkeypatch.setattr(runtime_module, "datetime", FutureDateTime)
    data = client.get(f"/api/v1/coding/{session_id}/context").json()
    assert data["stale_started"] is True
    assert data["latest_attempt"]["status"] == "started"
    assert data["latest_attempt"]["stale"] is True


def test_model_catalog_exposes_capabilities_and_enforces_switch_whitelist(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)
    client = TestClient(app)
    session_id = _session(client)

    listed = client.get("/api/v1/coding/models")
    assert listed.status_code == 200
    assert listed.json()["models"] == [
        {
            "id": "model-a",
            "label": "Model A",
            "provider": "test",
            "context_configured": True,
            "context_window_tokens": 100_000,
            "output_reserve_tokens": 10_000,
            "reasoning_modes": [],
        },
        {
            "id": "model-b",
            "label": "Model B",
            "provider": "test",
            "context_configured": False,
            "context_window_tokens": None,
            "output_reserve_tokens": None,
            "reasoning_modes": [],
        },
    ]
    unknown = client.patch(
        f"/api/v1/coding/{session_id}/model", json={"model_id": "unknown"}
    )
    assert unknown.status_code == 422
    assert unknown.json()["detail"] == "unknown coding model"

    switched = client.patch(
        f"/api/v1/coding/{session_id}/model", json={"model_id": "model-b"}
    )
    assert switched.status_code == 200
    snapshot = client.get(f"/api/v1/coding/{session_id}/context").json()
    assert snapshot["model_id"] == "model-b"
    assert snapshot["configured"] is False


def test_model_switch_rejects_active_run(tmp_path: Path) -> None:
    app = _app(tmp_path)
    client = TestClient(app)
    session_id = _session(client)
    app.state.coding_sessions[session_id].active_run_id = "run-active"

    response = client.patch(
        f"/api/v1/coding/{session_id}/model", json={"model_id": "model-b"}
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "context operation is busy"


def test_models_current_reflects_session_model(tmp_path: Path) -> None:
    app = _app(tmp_path)
    client = TestClient(app)
    session_id = _session(client)
    assert client.patch(
        f"/api/v1/coding/{session_id}/model", json={"model_id": "model-b"}
    ).status_code == 200

    response = client.get("/api/v1/coding/models")
    assert response.status_code == 200
    assert response.json()["current"] == "model-b"
    explicit = client.get("/api/v1/coding/models", params={"session_id": session_id})
    assert explicit.json()["current"] == "model-b"


def test_resume_rejects_external_workspace_before_runtime_construction(
    tmp_path: Path, monkeypatch: Any
) -> None:
    app = _app(tmp_path)
    client = TestClient(app)
    session_id = _session(client)
    outside = tmp_path.parent / "outside-workspace"
    outside.mkdir()
    # Persist a path that would make SkillRegistry inspect an out-of-scope tree.
    runtime = app.state.coding_sessions[session_id]
    runtime._save_session()
    store = runtime.session_store
    state = store.load(session_id)
    state["workspace_root"] = str(outside)
    store.save(state)
    constructed = False

    class ExplodingRuntime:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            nonlocal constructed
            constructed = True
            raise AssertionError("runtime must not be constructed")

    monkeypatch.setattr("api.coding.CodingRuntime", ExplodingRuntime)
    response = client.post(f"/api/v1/coding/session/{session_id}/resume")
    assert response.status_code == 400
    assert constructed is False


def test_create_rejects_workspace_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-create"
    outside.mkdir()
    (tmp_path / "escape").symlink_to(outside, target_is_directory=True)
    client = TestClient(_app(tmp_path))

    response = client.post(
        "/api/v1/coding/session", json={"workspace_root": str(tmp_path / "escape")}
    )
    assert response.status_code == 400
    assert "configured coding workspace" in response.json()["detail"]


def test_create_and_resume_reject_models_outside_catalog(
    tmp_path: Path, monkeypatch: Any
) -> None:
    app = _app(tmp_path)
    client = TestClient(app)
    app.state.coding_default_model = "unknown"
    created = client.post("/api/v1/coding/session", json={})
    assert created.status_code == 422
    assert created.json()["detail"] == "unknown coding model"

    app.state.coding_default_model = "model-a"
    session_id = _session(client)
    runtime = app.state.coding_sessions[session_id]
    runtime._save_session()
    state = runtime.session_store.load(session_id)
    state["model_spec"] = "unknown"
    runtime.session_store.save(state)
    constructed = False

    class ExplodingRuntime:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            nonlocal constructed
            constructed = True
            raise AssertionError("runtime must not be constructed")

    monkeypatch.setattr("api.coding.CodingRuntime", ExplodingRuntime)
    resumed = client.post(f"/api/v1/coding/session/{session_id}/resume")
    assert resumed.status_code == 422
    assert resumed.json()["detail"] == "unknown coding model"
    assert constructed is False


def test_explicit_empty_model_catalog_stays_empty_and_blocks_session(
    tmp_path: Path,
) -> None:
    app = create_app(
        coding_model_factory=Model,
        coding_model_catalog=[],
        coding_default_model="model-a",
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
    )
    client = TestClient(app)

    listed = client.get("/api/v1/coding/models")
    created = client.post("/api/v1/coding/session", json={})

    assert listed.status_code == 200
    assert listed.json()["models"] == []
    assert created.status_code == 422
    assert created.json()["detail"] == "unknown coding model"
