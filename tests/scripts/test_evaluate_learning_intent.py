from __future__ import annotations

from pathlib import Path

from scripts.evaluate_learning_intent import build_report


def test_report_binds_source_dataset_and_seed_limitations() -> None:
    dataset = Path(__file__).parents[2] / "evals" / "book_learning_intent_v1_seed.jsonl"

    report = build_report(
        dataset,
        source_commit="a" * 40,
        source_dirty=False,
    )

    assert report["evaluation_id"] == "sage-book-learning-intent-product-seed-v1"
    assert report["source"] == {"commit": "a" * 40, "dirty": False}
    assert report["dataset"]["case_count"] == 24
    assert report["dataset"]["sha256"].startswith("sha256:")
    assert report["protocol"]["formal_model_accuracy_claim"] is False
    assert report["protocol"]["test_used_for_tuning"] is False
