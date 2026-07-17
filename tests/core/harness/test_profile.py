"""Runtime profile normalization contracts."""

from __future__ import annotations

import pytest

from core.harness import normalize_runtime_profile


def test_missing_historical_profile_defaults_to_legacy() -> None:
    assert normalize_runtime_profile(None) == "legacy"
    assert normalize_runtime_profile("") == "legacy"


def test_known_profiles_round_trip_and_unknown_values_fail() -> None:
    assert normalize_runtime_profile("legacy") == "legacy"
    assert normalize_runtime_profile("deerflow_v2") == "deerflow_v2"
    with pytest.raises(ValueError, match="unknown runtime profile"):
        normalize_runtime_profile("future")
