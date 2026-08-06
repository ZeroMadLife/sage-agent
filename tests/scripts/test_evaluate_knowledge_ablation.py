from __future__ import annotations

import pytest

from scripts.evaluate_knowledge_ablation import _assert_selection

_STRATEGIES = {
    "contextual_chunk",
    "parent_child",
    "described_parent_child",
    "semantic_boundary",
    "cross_encoder",
}


def _selection(*, schema_version: int = 2, strategies: set[str] = _STRATEGIES) -> dict:
    return {
        "schema_version": schema_version,
        "stage": "selection",
        "protocol": {"test_used_for_strategy_or_gate_selection": False},
        "candidates": {strategy: {} for strategy in strategies},
    }


def test_ablation_v2_selection_requires_all_isolated_strategies() -> None:
    _assert_selection(_selection())

    missing = _STRATEGIES - {"described_parent_child"}
    with pytest.raises(ValueError, match="all isolated strategies"):
        _assert_selection(_selection(strategies=missing))


def test_ablation_v2_rejects_historical_v1_selection() -> None:
    with pytest.raises(ValueError, match="schema version"):
        _assert_selection(_selection(schema_version=1))
