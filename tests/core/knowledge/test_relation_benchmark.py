from __future__ import annotations

from pathlib import Path

import pytest

from core.knowledge.relation_benchmark import load_relation_benchmark


def test_frozen_relation_benchmark_has_dev_test_paths_and_negatives() -> None:
    root = Path(__file__).resolve().parents[3]
    queries = load_relation_benchmark(root / "evals" / "knowledge_relation_benchmark_v1.jsonl")

    assert len(queries) == 14
    assert {query.split for query in queries} == {"dev", "test"}
    assert sum(query.answerable for query in queries) == 12
    assert sum(not query.answerable for query in queries) == 2
    assert all(query.gold_paths for query in queries if query.answerable)
    assert all(not query.gold_paths for query in queries if not query.answerable)


def test_relation_benchmark_rejects_unanswerable_gold_path(tmp_path: Path) -> None:
    path = tmp_path / "invalid.jsonl"
    path.write_text(
        '{"id":"bad","query":"bad","split":"dev","relation_kind":"unanswerable",'
        '"answerable":false,"seed_sources":["a.md"],"required_sources":[],'
        '"gold_paths":[]}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid relation benchmark record"):
        load_relation_benchmark(path)
