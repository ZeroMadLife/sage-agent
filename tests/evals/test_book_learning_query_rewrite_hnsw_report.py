from __future__ import annotations

import json
from pathlib import Path


def test_committed_book_query_rewrite_hnsw_report_is_fail_closed() -> None:
    root = Path(__file__).parents[2]
    report = json.loads(
        (root / "evals/reports/book_learning_query_rewrite_hnsw_v1_2026-08-10.json").read_text(
            encoding="utf-8"
        )
    )

    assert len(report["source_commit"]) == 40
    assert report["query_rewrite"]["rewrite_status"] == "oracle_manual"
    assert report["query_rewrite"]["model_rewrite_evidence"] is False
    assert report["hnsw"]["decision"] == "keep_exact_hnsw_not_eligible"
    assert report["hnsw"]["runtime_index_enabled"] is False
    assert report["evidence_boundaries"]["production_sla_claim"] is False
