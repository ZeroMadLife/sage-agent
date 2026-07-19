"""Immutable runtime-profile contract for incremental Harness migration."""

from __future__ import annotations

from typing import Literal, cast

RuntimeProfile = Literal["legacy", "deerflow_v2"]
RUNTIME_PROFILES: frozenset[str] = frozenset({"legacy", "deerflow_v2"})


def normalize_runtime_profile(value: object) -> RuntimeProfile:
    """Interpret missing historical values as legacy and reject unknown values."""
    if value is None or value == "":
        return "legacy"
    normalized = str(value).strip()
    if normalized not in RUNTIME_PROFILES:
        raise ValueError(f"unknown runtime profile: {normalized}")
    return cast(RuntimeProfile, normalized)


__all__ = ["RUNTIME_PROFILES", "RuntimeProfile", "normalize_runtime_profile"]
