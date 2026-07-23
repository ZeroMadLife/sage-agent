from __future__ import annotations

import json
import subprocess

import pytest

from core.loop_harness.errors import LoopBlockedError
from core.loop_harness.reviewer import CcConnectReviewer


def _artifact(tmp_path):
    reports = tmp_path / "reports"
    artifact = reports / "loop-test-review"
    artifact.mkdir(parents=True)
    (artifact / "shadow.patch").write_text("diff --git a/a b/a\n", encoding="utf-8")
    (artifact / "validation.json").write_text('{"passed": true}\n', encoding="utf-8")
    return reports, artifact


def _payload() -> str:
    return json.dumps(
        {
            "verdict": "PASS",
            "summary": "未发现阻断问题",
            "findings": [],
            "tests": "验证证据完整",
            "visual_evidence": "本次不要求截图",
            "clean_room": "未发现复制外部实现",
            "merge_recommendation": "保持 Draft 进入 canary",
        },
        ensure_ascii=False,
    )


class FakeReviewer(CcConnectReviewer):
    def __init__(self, tmp_path, *, mutate: bool = False):
        reports, artifact = _artifact(tmp_path)
        binary = tmp_path / "cc-connect"
        binary.touch()
        super().__init__(cc_connect_bin=binary, reports_root=reports)
        self.artifact = artifact
        self.mutate = mutate
        self.calls = []

    def _run(self, *args: str, timeout: int | None = None):
        self.calls.append(args)
        if self.mutate:
            (self.artifact / "shadow.patch").write_text("changed\n", encoding="utf-8")
        return subprocess.CompletedProcess([], 0, _payload(), "")


def test_ccconnect_reviewer_uses_unique_synthetic_session_and_parses_json(tmp_path) -> None:
    reviewer = FakeReviewer(tmp_path)

    result = reviewer.review(
        run_id="loop-test-review",
        head_sha="a" * 40,
        artifact_directory=reviewer.artifact,
        changed_files=("frontend/src/views/KnowledgeView.vue",),
    )

    assert result.verdict == "PASS"
    args = reviewer.calls[0]
    assert args[args.index("--to") + 1] == "sage-loop-review"
    assert args[args.index("--session-key") + 1] == ("relay:loop-loop-test-review:loop-review")
    message = args[args.index("--message") + 1]
    assert "shadow.patch" in message
    assert "diff --git" not in message
    assert "PASS、REQUEST_CHANGES、BLOCK" in message
    assert "不得使用 Markdown 代码块" in message


def test_ccconnect_reviewer_blocks_evidence_mutation(tmp_path) -> None:
    reviewer = FakeReviewer(tmp_path, mutate=True)

    with pytest.raises(LoopBlockedError) as exc:
        reviewer.review(
            run_id="loop-test-review",
            head_sha="a" * 40,
            artifact_directory=reviewer.artifact,
            changed_files=("frontend/src/views/KnowledgeView.vue",),
        )

    assert exc.value.code == "BLOCKED_REVIEWER_WRITE"


def test_ccconnect_reviewer_rejects_non_json_output(tmp_path) -> None:
    reviewer = FakeReviewer(tmp_path)

    def invalid(*args: str, timeout: int | None = None):
        return subprocess.CompletedProcess([], 0, "PASS", "")

    reviewer._run = invalid
    with pytest.raises(LoopBlockedError) as exc:
        reviewer.review(
            run_id="loop-test-review",
            head_sha="a" * 40,
            artifact_directory=reviewer.artifact,
            changed_files=("frontend/src/views/KnowledgeView.vue",),
        )

    assert exc.value.code == "BLOCKED_REVIEWER_OUTPUT"
