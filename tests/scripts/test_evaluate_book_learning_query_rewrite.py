from __future__ import annotations

from scripts.evaluate_book_learning_query_rewrite import _observations


def test_query_rewrite_receipt_projects_original_and_bounded_rewrite_routes() -> None:
    receipt = {
        "stage": "bounded_recovery",
        "protocol": {"rewrite_status": "oracle_manual"},
        "retrieval_report": {
            "cases": [
                {
                    "query_id": "q1",
                    "latency_ms": 10,
                    "hits": [{"passage_id": "book#original"}],
                },
                {
                    "query_id": "q1__recovery_1",
                    "latency_ms": 20,
                    "hits": [{"passage_id": "book#rewrite-a"}],
                },
                {
                    "query_id": "q1__recovery_2",
                    "latency_ms": 30,
                    "hits": [{"passage_id": "book#rewrite-b"}],
                },
            ]
        },
        "plans": [
            {
                "case_id": "q1",
                "rewrite_queries": ["a", "b"],
                "rewrite_preserved_intent": True,
            }
        ],
    }

    observations, latency = _observations(receipt, top_k=10)

    assert observations[0].original_passage_ids == ("book#original",)
    assert set(observations[0].rewrite_passage_ids) == {"book#rewrite-a", "book#rewrite-b"}
    assert latency["original_only"] == {"p50": 10.0, "p95": 10.0}
    assert latency["original_plus_rewrite_serial_upper_bound"] == {
        "p50": 40.0,
        "p95": 40.0,
    }
