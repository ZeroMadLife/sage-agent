"""Server-owned model context-window configuration."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any, cast

from core.coding.context.budget import ContextPolicy

_ENV_NAME = "SAGE_MODEL_CONTEXT_WINDOWS"
_MAX_ENV_BYTES = 64 * 1024
_MAX_MODELS = 256
_MAX_CONTEXT_WINDOW_TOKENS = 10_000_000
_MAX_INTEGER_DIGITS = len(str(_MAX_CONTEXT_WINDOW_TOKENS))


class ModelCapabilityRegistry:
    """Resolve explicit model capabilities without guessing vendor limits."""

    def __init__(self, capabilities: Mapping[str, object] | None = None) -> None:
        self._policies: dict[str, ContextPolicy] = {}
        if len(capabilities or {}) > _MAX_MODELS:
            raise ValueError("too many configured model capabilities")
        for model_id, value in (capabilities or {}).items():
            if not isinstance(model_id, str) or not model_id.strip():
                raise ValueError("model identifiers must be non-empty strings")
            self._policies[model_id] = self._coerce_policy(value)

    @classmethod
    def from_env(cls, value: str | None = None) -> ModelCapabilityRegistry:
        raw = os.getenv(_ENV_NAME, "") if value is None else value
        if not raw.strip():
            return cls()
        if len(raw.encode("utf-8")) > _MAX_ENV_BYTES:
            raise ValueError(f"{_ENV_NAME} exceeds the size limit")
        try:
            parsed = json.loads(
                raw,
                object_pairs_hook=_unique_object,
                parse_int=_bounded_json_int,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"{_ENV_NAME} must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError(f"{_ENV_NAME} must be a JSON object")
        return cls(parsed)

    @classmethod
    def from_model(cls, model: object) -> ContextPolicy | None:
        window = getattr(model, "context_window_tokens", None)
        reserve = getattr(model, "output_reserve_tokens", None)
        if window is None and reserve is None:
            return None
        if not _strict_positive_int(window) or not _strict_positive_int(reserve):
            raise ValueError("model context attributes must be positive integers")
        _validate_limits(cast(int, window), cast(int, reserve))
        return ContextPolicy(
            context_window_tokens=cast(int, window),
            output_reserve_tokens=cast(int, reserve),
        )

    def resolve(self, model_spec: object) -> ContextPolicy | None:
        if isinstance(model_spec, str):
            return self._policies.get(model_spec)
        explicit = self.from_model(model_spec)
        if explicit is not None:
            return explicit
        for attribute in ("model", "model_id", "model_name"):
            model_id = getattr(model_spec, attribute, None)
            if isinstance(model_id, str) and model_id in self._policies:
                return self._policies[model_id]
        return None

    @staticmethod
    def _coerce_policy(value: object) -> ContextPolicy:
        if _strict_positive_int(value):
            window = cast(int, value)
            _validate_limits(window, 20_000)
            return ContextPolicy(context_window_tokens=window)
        if not isinstance(value, Mapping):
            raise ValueError("model capability must be an integer or object")
        allowed = {"context_window_tokens", "output_reserve_tokens"}
        if set(value) - allowed or "context_window_tokens" not in value:
            raise ValueError("model capability has unknown or missing fields")
        window = value["context_window_tokens"]
        reserve = value.get("output_reserve_tokens", 20_000)
        if not _strict_positive_int(window) or not _strict_positive_int(reserve):
            raise ValueError("model capability values must be positive integers")
        _validate_limits(window, reserve)
        return ContextPolicy(
            context_window_tokens=window,
            output_reserve_tokens=reserve,
        )


def _strict_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _bounded_json_int(value: str) -> int:
    digits = value.lstrip("-")
    if len(digits) > _MAX_INTEGER_DIGITS:
        raise ValueError("JSON integer exceeds digit limit")
    return int(value)


def _validate_limits(window: int, reserve: int) -> None:
    if window > _MAX_CONTEXT_WINDOW_TOKENS:
        raise ValueError("context window exceeds configured maximum")
    if reserve >= window:
        raise ValueError("output reserve must be less than context window")
