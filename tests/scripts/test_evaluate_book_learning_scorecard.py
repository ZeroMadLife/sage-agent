from __future__ import annotations

from pathlib import Path

import pytest

from scripts.evaluate_book_learning_scorecard import _strategy_paths


def test_strategy_paths_require_named_unique_receipts() -> None:
    assert _strategy_paths(["baseline=/tmp/base.json", "contextual=/tmp/context.json"]) == {
        "baseline": Path("/tmp/base.json"),
        "contextual": Path("/tmp/context.json"),
    }
    with pytest.raises(ValueError, match="NAME=PATH"):
        _strategy_paths(["missing-name"])
    with pytest.raises(ValueError, match="duplicate"):
        _strategy_paths(["same=/tmp/a.json", "same=/tmp/b.json"])
    with pytest.raises(ValueError, match="at least two"):
        _strategy_paths(["only=/tmp/a.json"])
