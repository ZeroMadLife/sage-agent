from __future__ import annotations

import pytest

from scripts.benchmark_book_learning_hnsw import (
    _integer_tuple,
    _oracle_recall,
    _percentile,
    _section,
)


def test_book_hnsw_helpers_keep_passage_and_oracle_contracts() -> None:
    assert _section("book.txt", ("Book", "Chapter")) == "Book / Chapter"
    assert _section("book.md", ("Book", "Chapter")) == "Chapter"
    assert _oracle_recall(
        {"q1": ("a", "b"), "q2": ("c", "d")},
        {"q1": ("a", "b"), "q2": ("c", "noise")},
    ) == 0.75


def test_book_hnsw_helpers_validate_curve_and_interpolate_percentiles() -> None:
    assert _integer_tuple("40,80,120") == (40, 80, 120)
    assert _percentile([1.0, 2.0, 3.0, 4.0], 0.95) == pytest.approx(3.85)

    with pytest.raises(ValueError, match="cannot be empty"):
        _integer_tuple("")
