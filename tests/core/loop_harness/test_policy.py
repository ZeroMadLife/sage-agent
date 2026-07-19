from __future__ import annotations

from core.loop_harness.models import DiffSnapshot, WorkerResult
from core.loop_harness.policy import classify_candidate, evaluate_diff


def _candidate(*paths: str, tier: str = "A") -> WorkerResult:
    return WorkerResult(
        verdict="FRONTEND_CANDIDATE",
        summary="修复知识库空状态间距",
        evidence=("frontend/src/views/KnowledgeView.vue:20",),
        reproduction=("打开空知识库页面",),
        changed_files=paths,
        tests=("KnowledgeView.test.ts",),
        risk_reasons=(),
        suggested_tier=tier,  # type: ignore[arg-type]
        confidence=0.95,
    )


def _snapshot(
    *paths: str,
    behavior_changed: bool = False,
    additions: int = 10,
    deletions: int = 2,
) -> DiffSnapshot:
    return DiffSnapshot(
        changed_files=paths,
        additions=additions,
        deletions=deletions,
        binary_files=(),
        symlink_files=(),
        behavior_changed=behavior_changed,
    )


def test_candidate_rejects_overlap_with_human_dirty_path() -> None:
    candidate = _candidate("frontend/src/views/KnowledgeView.vue")

    decision = classify_candidate(
        candidate,
        dirty_paths=("frontend/src/views/KnowledgeView.vue",),
    )

    assert decision.allowed is False
    assert decision.tier == "C"
    assert "人工修改路径重叠" in decision.reasons


def test_candidate_cannot_escape_current_scan_scope() -> None:
    candidate = _candidate("frontend/src/views/KnowledgeView.vue")

    decision = classify_candidate(
        candidate,
        dirty_paths=(),
        scan_scope=("core/coding", "tests/core/coding"),
    )

    assert decision.allowed is False
    assert "候选不属于本轮扫描范围" in decision.reasons


def test_shared_frontend_dirty_path_blocks_unrelated_candidate() -> None:
    candidate = _candidate("frontend/src/views/KnowledgeView.vue")

    decision = classify_candidate(
        candidate,
        dirty_paths=("frontend/src/types/api.ts",),
        scan_scope=("frontend/src",),
    )

    assert decision.allowed is False
    assert "人工修改路径重叠" in decision.reasons


def test_pure_visual_diff_is_tier_a() -> None:
    candidate = _candidate(
        "frontend/src/views/KnowledgeView.vue",
        "frontend/src/views/KnowledgeView.test.ts",
    )
    snapshot = _snapshot(*candidate.changed_files)

    decision = evaluate_diff(candidate, snapshot, dirty_paths=())

    assert decision.allowed is True
    assert decision.tier == "A"


def test_vue_behavior_change_is_tier_b_and_never_tier_a() -> None:
    candidate = _candidate("frontend/src/views/KnowledgeView.vue", tier="B")
    snapshot = _snapshot(*candidate.changed_files, behavior_changed=True)

    decision = evaluate_diff(candidate, snapshot, dirty_paths=())

    assert decision.allowed is True
    assert decision.tier == "B"
    assert "包含前端行为变化" in decision.reasons


def test_small_component_behavior_fix_with_regression_test_is_tier_a() -> None:
    candidate = _candidate(
        "frontend/src/views/KnowledgeView.vue",
        "frontend/src/views/KnowledgeView.test.ts",
    )
    snapshot = _snapshot(*candidate.changed_files, behavior_changed=True)

    decision = evaluate_diff(candidate, snapshot, dirty_paths=())

    assert decision.allowed is True
    assert decision.tier == "A"


def test_diff_outside_candidate_paths_is_rejected() -> None:
    candidate = _candidate("frontend/src/views/KnowledgeView.vue")
    snapshot = _snapshot(
        "frontend/src/views/KnowledgeView.vue",
        "frontend/src/stores/coding.ts",
    )

    decision = evaluate_diff(candidate, snapshot, dirty_paths=())

    assert decision.allowed is False
    assert decision.tier == "C"
    assert any("候选范围之外" in reason for reason in decision.reasons)
