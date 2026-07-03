"""FastAPI route tests."""


import pytest
from fastapi.testclient import TestClient

import api.main as api_main
from api.main import create_app


def test_health_endpoint() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_start_chat_returns_session_id() -> None:
    client = TestClient(create_app())

    response = client.post("/api/v1/chat", json={"content": "周末去杭州"})

    assert response.status_code == 200
    assert response.json()["session_id"]


def test_create_runtime_agent_returns_none_when_configuration_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配置失败时 create_runtime_agent 返回 None。"""

    def raise_settings_error() -> object:
        raise RuntimeError("missing config")

    monkeypatch.setattr(api_main, "get_settings", raise_settings_error)

    assert api_main.create_runtime_agent() is None
