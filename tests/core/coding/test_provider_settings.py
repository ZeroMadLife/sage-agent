"""Project-local Sage provider settings contract tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.coding.provider_settings import SageProviderSettings, SageProviderSettingsStore


def _valid_settings() -> dict[str, object]:
    return {
        "version": 1,
        "default_model": "openai:gpt-test",
        "providers": [
            {
                "id": "openai",
                "label": "OpenAI",
                "api_mode": "openai_chat_completions",
                "base_url": "https://api.openai.com/v1",
                "api_key_env": "OPENAI_API_KEY",
                "models": [
                    {
                        "id": "openai:gpt-test",
                        "label": "GPT Test",
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


def test_settings_build_catalog_capabilities_and_reasoning() -> None:
    settings = SageProviderSettings.from_mapping(_valid_settings())

    assert settings.default_model == "openai:gpt-test"
    assert settings.catalog == [
        {
            "id": "openai:gpt-test",
            "label": "GPT Test",
            "provider": "openai",
            "reasoning_modes": ["low", "medium", "high"],
        }
    ]
    policy = settings.registry.resolve("openai:gpt-test")
    assert policy is not None
    assert policy.context_window_tokens == 128_000
    model = settings.model("openai:gpt-test")
    assert model.reasoning.request_kwargs("medium") == {"reasoning_effort": "medium"}
    assert model.reasoning.request_kwargs("off") == {}
    with pytest.raises(ValueError, match="unsupported reasoning mode"):
        model.reasoning.request_kwargs("max")


def test_anthropic_budget_descriptor_maps_to_thinking_request() -> None:
    payload = _valid_settings()
    provider = payload["providers"][0]  # type: ignore[index]
    provider["id"] = "anthropic"  # type: ignore[index]
    provider["label"] = "Anthropic"  # type: ignore[index]
    provider["api_mode"] = "anthropic_messages"  # type: ignore[index]
    provider["base_url"] = "https://api.anthropic.com"  # type: ignore[index]
    provider["api_key_env"] = "ANTHROPIC_API_KEY"  # type: ignore[index]
    model = provider["models"][0]  # type: ignore[index]
    model["id"] = "anthropic:claude-test"  # type: ignore[index]
    model["reasoning"] = {  # type: ignore[index]
        "kind": "anthropic_thinking_budget",
        "budgets": {"low": 1024, "medium": 4096, "high": 8192},
    }
    payload["default_model"] = "anthropic:claude-test"

    settings = SageProviderSettings.from_mapping(payload)

    assert settings.model("anthropic:claude-test").reasoning.request_kwargs("high") == {
        "thinking": {"type": "enabled", "budget_tokens": 8192}
    }


@pytest.mark.parametrize(
    ("mutate", "detail"),
    [
        (lambda value: value.update({"unknown": True}), "unknown root fields"),
        (
            lambda value: value["providers"][0].update({"api_key": "secret"}),
            "unknown provider fields",
        ),
        (
            lambda value: value["providers"][0].update({"api_key_env": "sk-secret"}),
            "api_key_env",
        ),
        (
            lambda value: value["providers"][0].update({"base_url": "file:///tmp/model"}),
            "base_url",
        ),
        (
            lambda value: value["providers"][0]["models"][0].update(
                {"output_reserve_tokens": 128_000}
            ),
            "output reserve must be less",
        ),
        (
            lambda value: value["providers"][0]["models"][0].update(
                {"reasoning": {"kind": "openai_reasoning_effort", "modes": ["raw"]}}
            ),
            "unsupported reasoning mode",
        ),
    ],
)
def test_settings_reject_unsafe_or_ambiguous_fields(mutate, detail: str) -> None:
    payload = _valid_settings()
    mutate(payload)
    with pytest.raises(ValueError, match=detail):
        SageProviderSettings.from_mapping(payload)


def test_store_falls_back_to_toml_then_writes_project_json(tmp_path: Path) -> None:
    manifest = tmp_path / "coding_models.toml"
    manifest.write_text(
        """
version = 1
default_model = "deepseek:deepseek-v4-flash"

[[models]]
id = "deepseek:deepseek-v4-flash"
label = "DeepSeek V4 Flash"
provider = "deepseek"
context_window_tokens = 1000000
output_reserve_tokens = 64000
reasoning_modes = []
""",
        encoding="utf-8",
    )
    store = SageProviderSettingsStore(tmp_path, legacy_manifest_path=manifest)

    fallback = store.load()
    assert store.source == "legacy_toml"
    assert fallback.default_model == "deepseek:deepseek-v4-flash"
    assert fallback.providers[0].api_key_env == "DEEPSEEK_API_KEY"

    saved = store.save(_valid_settings())

    assert store.source == "project_json"
    assert saved.default_model == "openai:gpt-test"
    assert (tmp_path / ".sage" / "settings.json").is_file()
    assert "secret" not in (tmp_path / ".sage" / "settings.json").read_text(encoding="utf-8")


def test_external_settings_are_read_only(tmp_path: Path) -> None:
    external = tmp_path / "managed.json"
    external.write_text("{}", encoding="utf-8")
    store = SageProviderSettingsStore(
        tmp_path,
        external_path=external,
        legacy_manifest_path=tmp_path / "missing.toml",
    )

    assert store.editable is False
    with pytest.raises(PermissionError, match="deployment managed"):
        store.save(_valid_settings())


def test_project_settings_load_rejects_a_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text('{"version": 1}', encoding="utf-8")
    sage_dir = tmp_path / ".sage"
    sage_dir.mkdir()
    (sage_dir / "settings.json").symlink_to(outside)
    store = SageProviderSettingsStore(
        tmp_path,
        legacy_manifest_path=tmp_path / "missing.toml",
    )

    with pytest.raises(ValueError, match="settings.json must not be a symlink"):
        store.load()
