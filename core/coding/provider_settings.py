"""Strict project-local Provider and coding-model settings."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlsplit

from core.coding.context import CodingModelManifest, ModelCapabilityRegistry

ApiMode = Literal["openai_chat_completions", "anthropic_messages"]
ReasoningKind = Literal[
    "unsupported",
    "openai_reasoning_effort",
    "anthropic_thinking_budget",
]

_MAX_SETTINGS_BYTES = 256 * 1024
_MAX_PROVIDERS = 32
_MAX_MODELS = 256
_PROVIDER_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,127}")
_REASONING_MODES = ("low", "medium", "high")
_ROOT_FIELDS = {"version", "default_model", "providers"}
_PROVIDER_FIELDS = {
    "id",
    "label",
    "api_mode",
    "base_url",
    "api_key_env",
    "models",
}
_MODEL_FIELDS = {
    "id",
    "label",
    "context_window_tokens",
    "output_reserve_tokens",
    "reasoning",
}
_LEGACY_PROVIDER_PRESETS: dict[str, tuple[str, str, str]] = {
    "anthropic": ("anthropic_messages", "https://api.anthropic.com", "ANTHROPIC_API_KEY"),
    "deepseek": (
        "openai_chat_completions",
        "https://api.deepseek.com/v1",
        "DEEPSEEK_API_KEY",
    ),
    "deepseek_anthropic": (
        "anthropic_messages",
        "https://api.deepseek.com/anthropic",
        "DEEPSEEK_API_KEY",
    ),
    "doubao": (
        "openai_chat_completions",
        "https://ark.cn-beijing.volces.com/api/coding/v3",
        "DOUBAO_API_KEY",
    ),
    "openai": ("openai_chat_completions", "https://api.openai.com/v1", "OPENAI_API_KEY"),
    "openai_proxy": (
        "openai_chat_completions",
        "https://serve.wzjself.org/v1",
        "OPENAI_PROXY_API_KEY",
    ),
}


@dataclass(frozen=True, slots=True)
class ReasoningDefinition:
    """A provider-specific reasoning request contract."""

    kind: ReasoningKind = "unsupported"
    modes: tuple[str, ...] = ()
    budgets: tuple[tuple[str, int], ...] = ()

    def request_kwargs(self, mode: str) -> dict[str, object]:
        if mode == "off":
            return {}
        if mode not in self.modes:
            raise ValueError(f"unsupported reasoning mode: {mode}")
        if self.kind == "openai_reasoning_effort":
            return {"reasoning_effort": mode}
        if self.kind == "anthropic_thinking_budget":
            budget = dict(self.budgets)[mode]
            return {"thinking": {"type": "enabled", "budget_tokens": budget}}
        raise ValueError(f"unsupported reasoning mode: {mode}")

    def to_mapping(self) -> dict[str, object]:
        if self.kind == "openai_reasoning_effort":
            return {"kind": self.kind, "modes": list(self.modes)}
        if self.kind == "anthropic_thinking_budget":
            return {"kind": self.kind, "budgets": dict(self.budgets)}
        return {"kind": "unsupported"}


@dataclass(frozen=True, slots=True)
class ProviderModelDefinition:
    """One model declared under a Provider."""

    id: str
    label: str
    provider: str
    context_window_tokens: int | None
    output_reserve_tokens: int | None
    reasoning: ReasoningDefinition

    def to_mapping(self) -> dict[str, object]:
        value: dict[str, object] = {
            "id": self.id,
            "label": self.label,
            "reasoning": self.reasoning.to_mapping(),
        }
        if self.context_window_tokens is not None:
            value["context_window_tokens"] = self.context_window_tokens
            value["output_reserve_tokens"] = cast(int, self.output_reserve_tokens)
        return value


@dataclass(frozen=True, slots=True)
class ProviderDefinition:
    """One non-secret Provider declaration."""

    id: str
    label: str
    api_mode: ApiMode
    base_url: str
    api_key_env: str
    models: tuple[ProviderModelDefinition, ...]

    def to_mapping(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "api_mode": self.api_mode,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "models": [model.to_mapping() for model in self.models],
        }


@dataclass(frozen=True, slots=True)
class SageProviderSettings:
    """Validated Provider settings consumed by the coding runtime."""

    default_model: str
    providers: tuple[ProviderDefinition, ...]

    @property
    def catalog(self) -> list[dict[str, Any]]:
        return [
            {
                "id": model.id,
                "label": model.label,
                "provider": model.provider,
                "reasoning_modes": list(model.reasoning.modes),
            }
            for provider in self.providers
            for model in provider.models
        ]

    @property
    def reasoning_modes(self) -> dict[str, tuple[str, ...]]:
        return {
            model.id: model.reasoning.modes
            for provider in self.providers
            for model in provider.models
        }

    @property
    def registry(self) -> ModelCapabilityRegistry:
        policies: dict[str, object] = {}
        for provider in self.providers:
            for model in provider.models:
                if model.context_window_tokens is None:
                    continue
                policies[model.id] = {
                    "context_window_tokens": model.context_window_tokens,
                    "output_reserve_tokens": model.output_reserve_tokens,
                }
        return ModelCapabilityRegistry(policies)

    def provider(self, provider_id: str) -> ProviderDefinition:
        for provider in self.providers:
            if provider.id == provider_id:
                return provider
        raise ValueError(f"unknown provider: {provider_id}")

    def model(self, model_id: str) -> ProviderModelDefinition:
        for provider in self.providers:
            for model in provider.models:
                if model.id == model_id:
                    return model
        raise ValueError(f"unknown coding model: {model_id}")

    def provider_for_model(self, model_id: str) -> ProviderDefinition:
        return self.provider(self.model(model_id).provider)

    def to_mapping(self) -> dict[str, object]:
        return {
            "version": 1,
            "default_model": self.default_model,
            "providers": [provider.to_mapping() for provider in self.providers],
        }

    @classmethod
    def from_file(cls, path: str | Path) -> SageProviderSettings:
        resolved = Path(path).expanduser().resolve()
        try:
            payload = resolved.read_bytes()
        except OSError as exc:
            raise ValueError(f"unable to read Sage settings: {resolved}") from exc
        if len(payload) > _MAX_SETTINGS_BYTES:
            raise ValueError(f"Sage settings exceed size limit: {resolved}")
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid Sage settings JSON: {resolved}") from exc
        if not isinstance(value, dict):
            raise ValueError("Sage settings root must be an object")
        try:
            return cls.from_mapping(value)
        except ValueError as exc:
            raise ValueError(f"invalid Sage settings {resolved}: {exc}") from exc

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> SageProviderSettings:
        unknown = set(value) - _ROOT_FIELDS
        if unknown:
            raise ValueError(f"unknown root fields: {', '.join(sorted(unknown))}")
        if value.get("version") != 1:
            raise ValueError("version must be 1")
        default_model = _non_empty_string(value.get("default_model"), "default_model")
        raw_providers = value.get("providers")
        if not isinstance(raw_providers, list) or not raw_providers:
            raise ValueError("providers must be a non-empty array")
        if len(raw_providers) > _MAX_PROVIDERS:
            raise ValueError("too many configured providers")

        providers: list[ProviderDefinition] = []
        provider_ids: set[str] = set()
        model_ids: set[str] = set()
        for index, raw_provider in enumerate(raw_providers):
            providers.append(cls._parse_provider(index, raw_provider, provider_ids, model_ids))
        if len(model_ids) > _MAX_MODELS:
            raise ValueError("too many configured models")
        if default_model not in model_ids:
            raise ValueError("default_model must reference a configured model")
        return cls(default_model=default_model, providers=tuple(providers))

    @classmethod
    def from_legacy_manifest(cls, manifest: CodingModelManifest) -> SageProviderSettings:
        grouped: dict[str, list[ProviderModelDefinition]] = {}
        for model in manifest.models:
            grouped.setdefault(model.provider, []).append(
                ProviderModelDefinition(
                    id=model.id,
                    label=model.label,
                    provider=model.provider,
                    context_window_tokens=model.context_window_tokens,
                    output_reserve_tokens=model.output_reserve_tokens,
                    reasoning=ReasoningDefinition(),
                )
            )
        providers: list[ProviderDefinition] = []
        for provider_id, models in grouped.items():
            preset = _LEGACY_PROVIDER_PRESETS.get(provider_id)
            if preset is None:
                raise ValueError(
                    f"legacy provider '{provider_id}' needs .sage/settings.json configuration"
                )
            api_mode, base_url, api_key_env = preset
            providers.append(
                ProviderDefinition(
                    id=provider_id,
                    label=provider_id.replace("_", " ").title(),
                    api_mode=cast(ApiMode, api_mode),
                    base_url=base_url,
                    api_key_env=api_key_env,
                    models=tuple(models),
                )
            )
        return cls(default_model=manifest.default_model, providers=tuple(providers))

    @classmethod
    def _parse_provider(
        cls,
        index: int,
        raw: object,
        provider_ids: set[str],
        model_ids: set[str],
    ) -> ProviderDefinition:
        if not isinstance(raw, dict):
            raise ValueError(f"providers[{index}] must be an object")
        unknown = set(raw) - _PROVIDER_FIELDS
        if unknown:
            raise ValueError(f"unknown provider fields: {', '.join(sorted(unknown))}")
        provider_id = _identifier(raw.get("id"), f"providers[{index}].id")
        if provider_id in provider_ids:
            raise ValueError(f"duplicate provider id: {provider_id}")
        provider_ids.add(provider_id)
        label = _non_empty_string(raw.get("label"), f"providers[{index}].label")
        api_mode = _api_mode(raw.get("api_mode"), f"providers[{index}].api_mode")
        base_url = _base_url(raw.get("base_url"), f"providers[{index}].base_url")
        api_key_env = _api_key_env(raw.get("api_key_env"), f"providers[{index}].api_key_env")
        raw_models = raw.get("models")
        if not isinstance(raw_models, list) or not raw_models:
            raise ValueError(f"providers[{index}].models must be a non-empty array")
        models = tuple(
            cls._parse_model(provider_id, api_mode, model_index, model, model_ids)
            for model_index, model in enumerate(raw_models)
        )
        return ProviderDefinition(
            id=provider_id,
            label=label,
            api_mode=api_mode,
            base_url=base_url,
            api_key_env=api_key_env,
            models=models,
        )

    @staticmethod
    def _parse_model(
        provider_id: str,
        api_mode: ApiMode,
        index: int,
        raw: object,
        model_ids: set[str],
    ) -> ProviderModelDefinition:
        if not isinstance(raw, dict):
            raise ValueError(f"models[{index}] must be an object")
        unknown = set(raw) - _MODEL_FIELDS
        if unknown:
            raise ValueError(f"unknown model fields: {', '.join(sorted(unknown))}")
        model_id = _non_empty_string(raw.get("id"), f"models[{index}].id")
        if not model_id.startswith(f"{provider_id}:") or len(model_id) > 192:
            raise ValueError(f"models[{index}].id must use provider:model format")
        if any(ord(char) < 33 for char in model_id):
            raise ValueError(f"models[{index}].id contains invalid characters")
        if model_id in model_ids:
            raise ValueError(f"duplicate model id: {model_id}")
        model_ids.add(model_id)
        label = _non_empty_string(raw.get("label"), f"models[{index}].label")

        has_window = "context_window_tokens" in raw
        has_reserve = "output_reserve_tokens" in raw
        if has_window != has_reserve:
            raise ValueError("configure context window and output reserve together")
        window: int | None = None
        reserve: int | None = None
        if has_window:
            window = _positive_integer(raw["context_window_tokens"], "context_window_tokens")
            reserve = _positive_integer(raw["output_reserve_tokens"], "output_reserve_tokens")
            if reserve >= window:
                raise ValueError("output reserve must be less than context window")
        reasoning = _reasoning(raw.get("reasoning", {"kind": "unsupported"}), api_mode)
        return ProviderModelDefinition(
            id=model_id,
            label=label,
            provider=provider_id,
            context_window_tokens=window,
            output_reserve_tokens=reserve,
            reasoning=reasoning,
        )


class SageProviderSettingsStore:
    """Load and atomically save one workspace's non-secret settings."""

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        external_path: str | Path | None = None,
        legacy_manifest_path: str | Path,
    ) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.external_path = (
            Path(external_path).expanduser().resolve() if external_path is not None else None
        )
        self.legacy_manifest_path = Path(legacy_manifest_path).expanduser().resolve()
        self._source = ""

    @property
    def settings_path(self) -> Path:
        return self.external_path or self.workspace_root / ".sage" / "settings.json"

    @property
    def editable(self) -> bool:
        return self.external_path is None

    @property
    def source(self) -> str:
        return self._source

    def load(self) -> SageProviderSettings:
        path = self.settings_path
        self._assert_not_symlink(path)
        if self.external_path is not None:
            self._source = "deployment_json"
            return SageProviderSettings.from_file(path)
        if path.is_file():
            self._source = "project_json"
            return SageProviderSettings.from_file(path)
        self._source = "legacy_toml"
        return SageProviderSettings.from_legacy_manifest(
            CodingModelManifest.from_file(self.legacy_manifest_path)
        )

    def save(self, value: dict[str, Any]) -> SageProviderSettings:
        if not self.editable:
            raise PermissionError("Sage settings are deployment managed")
        settings = SageProviderSettings.from_mapping(value)
        target = self.settings_path
        parent = target.parent
        self._assert_not_symlink(target)
        parent.mkdir(parents=True, exist_ok=True)
        self._assert_not_symlink(target)
        resolved_parent = parent.resolve()
        try:
            resolved_parent.relative_to(self.workspace_root)
        except ValueError as exc:
            raise ValueError("settings path escapes workspace root") from exc

        rendered = json.dumps(settings.to_mapping(), ensure_ascii=False, indent=2) + "\n"
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=parent,
                prefix=".settings-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
                os.fchmod(handle.fileno(), 0o600)
            os.replace(temporary, target)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        self._source = "project_json"
        return settings

    @staticmethod
    def _assert_not_symlink(path: Path) -> None:
        if path.is_symlink():
            raise ValueError("settings.json must not be a symlink")
        if path.parent.exists() and path.parent.is_symlink():
            raise ValueError(".sage settings directory must not be a symlink")


def _reasoning(value: object, api_mode: ApiMode) -> ReasoningDefinition:
    if not isinstance(value, dict):
        raise ValueError("reasoning must be an object")
    kind = value.get("kind")
    if kind == "unsupported":
        unknown = set(value) - {"kind"}
        if unknown:
            raise ValueError(f"unknown reasoning fields: {', '.join(sorted(unknown))}")
        return ReasoningDefinition()
    if kind == "openai_reasoning_effort":
        if api_mode != "openai_chat_completions":
            raise ValueError("openai reasoning requires openai_chat_completions")
        unknown = set(value) - {"kind", "modes"}
        if unknown:
            raise ValueError(f"unknown reasoning fields: {', '.join(sorted(unknown))}")
        modes = _modes(value.get("modes"))
        return ReasoningDefinition(kind=kind, modes=modes)
    if kind == "anthropic_thinking_budget":
        if api_mode != "anthropic_messages":
            raise ValueError("Anthropic thinking requires anthropic_messages")
        unknown = set(value) - {"kind", "budgets"}
        if unknown:
            raise ValueError(f"unknown reasoning fields: {', '.join(sorted(unknown))}")
        raw_budgets = value.get("budgets")
        if not isinstance(raw_budgets, dict) or not raw_budgets:
            raise ValueError("reasoning budgets must be a non-empty object")
        unknown_modes = set(raw_budgets) - set(_REASONING_MODES)
        if unknown_modes:
            raise ValueError(f"unsupported reasoning mode: {sorted(unknown_modes)[0]}")
        budgets: list[tuple[str, int]] = []
        for mode in _REASONING_MODES:
            if mode not in raw_budgets:
                continue
            budget = _positive_integer(raw_budgets[mode], f"reasoning.budgets.{mode}")
            if budget < 1024:
                raise ValueError("reasoning budget must be at least 1024 tokens")
            budgets.append((mode, budget))
        return ReasoningDefinition(
            kind=kind,
            modes=tuple(mode for mode, _ in budgets),
            budgets=tuple(budgets),
        )
    raise ValueError(f"unsupported reasoning kind: {kind}")


def _modes(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("reasoning modes must be a non-empty array")
    modes: list[str] = []
    for raw in value:
        mode = _non_empty_string(raw, "reasoning mode")
        if mode not in _REASONING_MODES:
            raise ValueError(f"unsupported reasoning mode: {mode}")
        if mode in modes:
            raise ValueError(f"duplicate reasoning mode: {mode}")
        modes.append(mode)
    return tuple(modes)


def _identifier(value: object, field: str) -> str:
    result = _non_empty_string(value, field)
    if _PROVIDER_ID.fullmatch(result) is None:
        raise ValueError(f"{field} has invalid characters")
    return result


def _api_key_env(value: object, field: str) -> str:
    result = _non_empty_string(value, field)
    if _ENV_NAME.fullmatch(result) is None:
        raise ValueError(f"{field} must be an environment variable name")
    return result


def _base_url(value: object, field: str) -> str:
    result = _non_empty_string(value, field)
    parsed = urlsplit(result)
    host = (parsed.hostname or "").casefold()
    loopback = host in {"localhost", "127.0.0.1", "::1"}
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or (parsed.scheme == "http" and not loopback)
        or any(ord(char) < 32 for char in result)
    ):
        raise ValueError(f"{field} must be an HTTPS URL or loopback HTTP URL")
    return result.rstrip("/")


def _api_mode(value: object, field: str) -> ApiMode:
    result = _non_empty_string(value, field)
    if result not in {"openai_chat_completions", "anthropic_messages"}:
        raise ValueError(f"{field} is unsupported: {result}")
    return cast(ApiMode, result)


def _non_empty_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    result = value.strip()
    if len(result) > 512:
        raise ValueError(f"{field} is too long")
    return result


def _positive_integer(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value
