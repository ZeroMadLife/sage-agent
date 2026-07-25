from scripts.evaluate_memory_lifecycle import run_evaluation


def test_memory_lifecycle_evaluation_has_forty_passing_cases() -> None:
    report = run_evaluation()

    assert report["case_count"] == 40
    assert report["passed"] == 40
    assert report["failed"] == 0
    assert report["pass_rate"] == 1.0
    assert set(report["categories"]) == {
        "proposal_isolation",
        "retraction",
        "supersession",
        "consolidation",
        "restart_and_scope",
    }
