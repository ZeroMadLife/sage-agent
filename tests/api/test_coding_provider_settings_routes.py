"""Provider settings REST contract tests."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from api.main import create_app


class FakeModel:
    async def complete(self, prompt: str) -> str:
        del prompt
        return "<final>done</final>"


def _settings(model_id: str = "openai:gpt-test") -> dict[str, object]:
    provider, model = model_id.split(":", 1)
    return {
        "version": 1,
        "default_model": model_id,
        "providers": [
            {
                "id": provider,
                "label": "OpenAI",
                "api_mode": "openai_chat_completions",
                "base_url": "https://api.openai.com/v1",
                "api_key_env": "OPENAI_API_KEY",
                "models": [
                    {
                        "id": model_id,
                        "label": model,
                        "context_window_tokens": 128_000,
                        "output_reserve_tokens": 16_000,
                        "reasoning": {
                            "kind": "openai_reasoning_effort",
                            "modes": ["low", "medium", "high"],
                        },
                    }
                ],
            }
        ],
    }


def _write_settings(root: Path, value: dict[str, object]) -> None:
    target = root / ".sage" / "settings.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(value), encoding="utf-8")


def _app(root: Path):
    return create_app(
        coding_model_factory=FakeModel,
        coding_workspace_root=root,
        coding_storage_root=root / ".coding",
    )


def test_provider_settings_response_is_non_secret_and_reports_env_status(
    tmp_path: Path, monkeypatch
) -> None:
    _write_settings(tmp_path, _settings())
    monkeypatch.setenv("OPENAI_API_KEY", "never-return-this-value")
    client = TestClient(_app(tmp_path))

    response = client.get("/api/v1/coding/providers")

    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "project_json"
    assert data["editable"] is True
    assert data["default_model"] == "openai:gpt-test"
    assert data["providers"][0]["api_key_env"] == "OPENAI_API_KEY"
    assert data["providers"][0]["api_key_configured"] is True
    assert "never-return-this-value" not in response.text
    assert "api_key\"" not in response.text


def test_provider_settings_update_rebuilds_catalog_and_rejects_secret_fields(
    tmp_path: Path,
) -> None:
    _write_settings(tmp_path, _settings())
    app = _app(tmp_path)
    client = TestClient(app)
    updated = _settings("openai:gpt-next")

    response = client.put("/api/v1/coding/providers", json=updated)

    assert response.status_code == 200
    assert app.state.coding_default_model == "openai:gpt-next"
    assert app.state.coding_model_catalog[0]["id"] == "openai:gpt-next"
    assert client.get("/api/v1/coding/models").json()["models"][0]["id"] == (
        "openai:gpt-next"
    )

    provider = updated["providers"][0]
    assert isinstance(provider, dict)
    provider["api_key"] = "secret"
    rejected = client.put("/api/v1/coding/providers", json=updated)
    assert rejected.status_code == 422
    assert "secret" not in rejected.text


def test_deployment_managed_provider_settings_are_read_only(
    tmp_path: Path, monkeypatch
) -> None:
    managed = tmp_path / "managed.json"
    managed.write_text(json.dumps(_settings()), encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("SAGE_CODING_SETTINGS_FILE", str(managed))
    client = TestClient(_app(workspace))

    listed = client.get("/api/v1/coding/providers")
    updated = client.put("/api/v1/coding/providers", json=_settings())

    assert listed.status_code == 200
    assert listed.json()["source"] == "deployment_json"
    assert listed.json()["editable"] is False
    assert updated.status_code == 403
