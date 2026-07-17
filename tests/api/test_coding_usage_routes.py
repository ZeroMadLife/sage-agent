"""Coding usage summary API tests."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from api.main import create_app
from core.coding.usage_store import UsageSample


class FakeModel:
    async def complete(self, prompt: str) -> str:
        del prompt
        return "<final>done</final>"


def test_usage_route_returns_real_ledger_aggregation(tmp_path: Path) -> None:
    app = create_app(
        coding_model_factory=FakeModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
    )
    app.state.coding_usage_store.record(
        request_id="run-1:1",
        session_id="session-1",
        run_id="run-1",
        provider="deepseek",
        model="deepseek:deepseek-v4-flash",
        usage=UsageSample(input_tokens=120, output_tokens=30, total_tokens=150),
    )

    response = TestClient(app).get("/api/v1/coding/usage", params={"range": "30d"})

    assert response.status_code == 200
    assert response.json()["range_days"] == 30
    assert response.json()["input_tokens"] == 120
    assert response.json()["cost"] is None


def test_usage_route_rejects_unknown_range(tmp_path: Path) -> None:
    app = create_app(
        coding_model_factory=FakeModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
    )

    response = TestClient(app).get("/api/v1/coding/usage", params={"range": "all"})

    assert response.status_code == 422
