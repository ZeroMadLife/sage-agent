"""Validated server-owned coding model manifest."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from core.coding.context.model_capabilities import ModelCapabilityRegistry

_MAX_MANIFEST_BYTES = 256 * 1024
_MAX_MODELS = 256
_ROOT_FIELDS = {"version", "default_model", "models"}
_MODEL_FIELDS = {
    "id",
    "label",
    "provider",
    "context_window_tokens",
    "output_reserve_tokens",
    "reasoning_modes",
}
_REASONING_MODES = {"low", "medium", "high"}


@dataclass(frozen=True)
class CodingModelDefinition:
    """One model exposed by the coding runtime."""

    id: str
    label: str
    provider: str
    context_window_tokens: int | None
    output_reserve_tokens: int | None
    reasoning_modes: tuple[str, ...]

    def catalog_item(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "provider": self.provider,
            "reasoning_modes": list(self.reasoning_modes),
        }


@dataclass(frozen=True)
class CodingModelManifest:
    """Single source for the coding model catalog and capabilities."""

    default_model: str
    models: tuple[CodingModelDefinition, ...]
    registry: ModelCapabilityRegistry

    @property
    def catalog(self) -> list[dict[str, object]]:
        return [model.catalog_item() for model in self.models]

    @classmethod
    def from_file(cls, path: str | Path) -> CodingModelManifest:
        manifest_path = Path(path).expanduser().resolve()
        try:
            payload = manifest_path.read_bytes()
        except OSError as exc:
            raise ValueError(f"unable to read coding model manifest: {manifest_path}") from exc
        if len(payload) > _MAX_MANIFEST_BYTES:
            raise ValueError(f"coding model manifest exceeds size limit: {manifest_path}")
        try:
            parsed = tomllib.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise ValueError(f"invalid coding model TOML: {manifest_path}") from exc
        try:
            return cls._from_mapping(parsed)
        except ValueError as exc:
            raise ValueError(f"invalid coding model manifest {manifest_path}: {exc}") from exc

    @classmethod
    def _from_mapping(cls, value: dict[str, Any]) -> CodingModelManifest:
        unknown = set(value) - _ROOT_FIELDS
        if unknown:
            raise ValueError(f"unknown root fields: {', '.join(sorted(unknown))}")
        if value.get("version") != 1:
            raise ValueError("version must be 1")
        default_model = _non_empty_string(value.get("default_model"), "default_model")
        raw_models = value.get("models")
        if not isinstance(raw_models, list) or not raw_models:
            raise ValueError("models must be a non-empty array")
        if len(raw_models) > _MAX_MODELS:
            raise ValueError("too many configured models")

        models: list[CodingModelDefinition] = []
        policies: dict[str, object] = {}
        seen: set[str] = set()
        for index, raw_model in enumerate(raw_models):
            if not isinstance(raw_model, dict):
                raise ValueError(f"models[{index}] must be a table")
            models.append(cls._parse_model(index, raw_model, seen, policies))
        if default_model not in seen:
            raise ValueError("default_model must reference a configured model")
        return cls(default_model, tuple(models), ModelCapabilityRegistry(policies))

    @staticmethod
    def _parse_model(
        index: int,
        raw: dict[str, Any],
        seen: set[str],
        policies: dict[str, object],
    ) -> CodingModelDefinition:
        unknown = set(raw) - _MODEL_FIELDS
        if unknown:
            raise ValueError(
                f"models[{index}] has unknown fields: {', '.join(sorted(unknown))}"
            )
        model_id = _non_empty_string(raw.get("id"), f"models[{index}].id")
        if model_id in seen:
            raise ValueError(f"duplicate model id: {model_id}")
        seen.add(model_id)
        label = _non_empty_string(raw.get("label"), f"models[{index}].label")
        provider = _non_empty_string(raw.get("provider"), f"models[{index}].provider")

        has_window = "context_window_tokens" in raw
        has_reserve = "output_reserve_tokens" in raw
        if has_window != has_reserve:
            raise ValueError(
                f"models[{index}] must configure context window and output reserve together"
            )
        window: int | None = None
        reserve: int | None = None
        if has_window:
            window = _positive_integer(raw["context_window_tokens"], f"models[{index}].context_window_tokens")
            reserve = _positive_integer(raw["output_reserve_tokens"], f"models[{index}].output_reserve_tokens")
            policies[model_id] = {
                "context_window_tokens": window,
                "output_reserve_tokens": reserve,
            }

        raw_modes = raw.get("reasoning_modes", [])
        if not isinstance(raw_modes, list):
            raise ValueError(f"models[{index}].reasoning_modes must be an array")
        modes: list[str] = []
        for raw_mode in raw_modes:
            mode = _non_empty_string(raw_mode, f"models[{index}].reasoning_modes")
            if mode not in _REASONING_MODES:
                raise ValueError(f"models[{index}] has unsupported reasoning mode: {mode}")
            if mode in modes:
                raise ValueError(f"models[{index}] has duplicate reasoning mode: {mode}")
            modes.append(mode)
        return CodingModelDefinition(model_id, label, provider, window, reserve, tuple(modes))


def _non_empty_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _positive_integer(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return cast(int, value)
