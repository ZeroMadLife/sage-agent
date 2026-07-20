from __future__ import annotations

import json
import subprocess

import pytest

from core.loop_harness.errors import LoopBlockedError
from core.loop_harness.github import GitHubAdapter
from core.loop_harness.models import (
    PullRequestReceipt,
    ValidationResult,
    ValidationStep,
    WorkerResult,
)


class FakeGitHub(GitHubAdapter):
    def __init__(self, tmp_path, responses):
        gh = tmp_path / "gh"
        gh.touch()
        super().__init__(
            gh_bin=gh,
            repository="ZeroMadLife/sage-agent",
            target_branch="dev/sage-v7",
        )
        self.responses = list(responses)
        self.calls = []

    def _run(self, *args: str, input_text: str | None = None):
        self.calls.append((args, input_text))
        return self.responses.pop(0)


def _completed(stdout: str = "", returncode: int = 0):
    return subprocess.CompletedProcess([], returncode, stdout, "")


def _candidate(summary: str = "修复空状态间距") -> WorkerResult:
    return WorkerResult(
        verdict="FRONTEND_CANDIDATE",
        summary=summary,
        evidence=("KnowledgeView.vue:20",),
        reproduction=("打开空知识库",),
        changed_files=("frontend/src/views/KnowledgeView.vue",),
        tests=("KnowledgeView.test.ts",),
        risk_reasons=(),
        suggested_tier="A",
        confidence=0.95,
    )


def _validation() -> ValidationResult:
    return ValidationResult(True, (ValidationStep("frontend-test", 0, 1.25),))


def _pr_metadata(
    *,
    state: str = "OPEN",
    is_draft: bool = True,
    base_sha: str = "a" * 40,
    head_sha: str = "b" * 40,
    labels: list[dict[str, str]] | None = None,
    checks: list[dict[str, str]] | None = None,
    merge_sha: str | None = None,
) -> str:
    return json.dumps(
        {
            "number": 12,
            "state": state,
            "isDraft": is_draft,
            "headRefName": "codex/loop-frontend-abcdef123456",
            "headRefOid": head_sha,
            "baseRefName": "dev/sage-v7",
            "baseRefOid": base_sha,
            "reviewDecision": "",
            "labels": labels or [],
            "statusCheckRollup": checks or [],
            "mergeCommit": {"oid": merge_sha} if merge_sha else None,
        }
    )


def test_github_adapter_creates_chinese_draft_pr_with_body_on_stdin(tmp_path) -> None:
    adapter = FakeGitHub(
        tmp_path,
        [
            _completed("[]"),
            _completed("https://github.com/ZeroMadLife/sage-agent/pull/12\n"),
        ],
    )

    receipt = adapter.create_draft(
        branch="codex/loop-frontend-abcdef123456",
        head_sha="a" * 40,
        candidate=_candidate(),
        validation=_validation(),
        tier="A",
    )

    assert receipt.number == 12
    create_args, body = adapter.calls[1]
    assert "--draft" in create_args
    assert create_args[create_args.index("--base") + 1] == "dev/sage-v7"
    assert body is not None
    for heading in ("问题证据", "修改内容", "验证结果", "风险与回滚"):
        assert heading in body
    assert "ghp_" not in body


def test_github_adapter_reuses_existing_pr_only_for_exact_head(tmp_path) -> None:
    existing = json.dumps(
        [
            {
                "number": 7,
                "url": "https://github.com/ZeroMadLife/sage-agent/pull/7",
                "headRefName": "codex/loop-frontend-abcdef123456",
                "headRefOid": "b" * 40,
            }
        ]
    )
    adapter = FakeGitHub(tmp_path, [_completed(existing)])

    receipt = adapter.create_draft(
        branch="codex/loop-frontend-abcdef123456",
        head_sha="b" * 40,
        candidate=_candidate(),
        validation=_validation(),
        tier="B",
    )

    assert receipt.number == 7
    assert len(adapter.calls) == 1


def test_github_adapter_blocks_open_loop_pr_capacity(tmp_path) -> None:
    adapter = FakeGitHub(
        tmp_path,
        [_completed('[{"number": 9, "headRefName": "codex/loop-frontend-deadbeef1234"}]')],
    )

    with pytest.raises(LoopBlockedError) as exc:
        adapter.require_pr_capacity()

    assert exc.value.code == "SKIPPED_PR_CAPACITY"


def test_github_adapter_ignores_non_loop_open_pr(tmp_path) -> None:
    adapter = FakeGitHub(
        tmp_path,
        [_completed('[{"number": 9, "headRefName": "feature/human-work"}]')],
    )

    adapter.require_pr_capacity()


def test_github_adapter_ignores_human_loop_design_pr(tmp_path) -> None:
    adapter = FakeGitHub(
        tmp_path,
        [_completed('[{"number": 6, "headRefName": "codex/feat-loop-harness"}]')],
    )

    adapter.require_pr_capacity()


def test_github_adapter_sanitizes_mentions_and_html_from_worker_text(tmp_path) -> None:
    adapter = FakeGitHub(
        tmp_path,
        [
            _completed("[]"),
            _completed("https://github.com/ZeroMadLife/sage-agent/pull/12\n"),
        ],
    )
    candidate = _candidate("修复 @all <script> 间距")

    adapter.create_draft(
        branch="codex/loop-frontend-abcdef123456",
        head_sha="a" * 40,
        candidate=candidate,
        validation=_validation(),
        tier="A",
    )

    create_args, body = adapter.calls[1]
    title = create_args[create_args.index("--title") + 1]
    assert "@" not in title and "<" not in title
    assert body is not None and "<script>" not in body


def test_github_adapter_rejects_non_chinese_title(tmp_path) -> None:
    adapter = FakeGitHub(tmp_path, [_completed("[]")])

    with pytest.raises(LoopBlockedError) as exc:
        adapter.create_draft(
            branch="codex/loop-frontend-abcdef123456",
            head_sha="a" * 40,
            candidate=_candidate("fix spacing"),
            validation=_validation(),
            tier="A",
        )

    assert exc.value.code == "BLOCKED_PR_LANGUAGE"


def test_github_adapter_auto_merges_exact_tier_a_after_all_checks_pass(tmp_path) -> None:
    successful_checks = [
        {"status": "COMPLETED", "conclusion": "SUCCESS"},
        {"status": "COMPLETED", "conclusion": "NEUTRAL"},
        {"state": "SUCCESS"},
    ]
    adapter = FakeGitHub(
        tmp_path,
        [
            _completed(_pr_metadata()),
            _completed(),
            _completed(_pr_metadata(is_draft=False, checks=successful_checks)),
            _completed(),
            _completed(
                _pr_metadata(
                    state="MERGED",
                    is_draft=False,
                    checks=successful_checks,
                    merge_sha="c" * 40,
                )
            ),
        ],
    )
    receipt = PullRequestReceipt(
        12,
        "https://github.com/ZeroMadLife/sage-agent/pull/12",
        "codex/loop-frontend-abcdef123456",
        "b" * 40,
    )

    merged_sha = adapter.merge_tier_a(
        receipt=receipt,
        base_sha="a" * 40,
        tier="A",
    )

    assert merged_sha == "c" * 40
    assert adapter.calls[1][0][:2] == ("pr", "ready")
    merge_args = adapter.calls[3][0]
    assert merge_args[:2] == ("pr", "merge")
    assert "--squash" in merge_args
    assert merge_args[merge_args.index("--match-head-commit") + 1] == "b" * 40


def test_github_adapter_blocks_manual_review_label_before_ready(tmp_path) -> None:
    adapter = FakeGitHub(
        tmp_path,
        [_completed(_pr_metadata(labels=[{"name": "high-risk"}]))],
    )
    receipt = PullRequestReceipt(
        12,
        "https://github.com/ZeroMadLife/sage-agent/pull/12",
        "codex/loop-frontend-abcdef123456",
        "b" * 40,
    )

    with pytest.raises(LoopBlockedError) as exc:
        adapter.merge_tier_a(receipt=receipt, base_sha="a" * 40, tier="A")

    assert exc.value.code == "BLOCKED_HUMAN_REVIEW"
    assert len(adapter.calls) == 1


def test_github_adapter_blocks_failed_check_without_merging(tmp_path) -> None:
    failed_checks = [{"status": "COMPLETED", "conclusion": "FAILURE"}]
    adapter = FakeGitHub(
        tmp_path,
        [
            _completed(_pr_metadata(is_draft=False)),
            _completed(_pr_metadata(is_draft=False, checks=failed_checks)),
        ],
    )
    receipt = PullRequestReceipt(
        12,
        "https://github.com/ZeroMadLife/sage-agent/pull/12",
        "codex/loop-frontend-abcdef123456",
        "b" * 40,
    )

    with pytest.raises(LoopBlockedError) as exc:
        adapter.merge_tier_a(receipt=receipt, base_sha="a" * 40, tier="A")

    assert exc.value.code == "BLOCKED_GITHUB_CHECKS"
    assert all(call[0][:2] != ("pr", "merge") for call in adapter.calls)


def test_github_adapter_rejects_non_tier_a_without_api_calls(tmp_path) -> None:
    adapter = FakeGitHub(tmp_path, [])
    receipt = PullRequestReceipt(
        12,
        "https://github.com/ZeroMadLife/sage-agent/pull/12",
        "codex/loop-frontend-abcdef123456",
        "b" * 40,
    )

    with pytest.raises(LoopBlockedError) as exc:
        adapter.merge_tier_a(receipt=receipt, base_sha="a" * 40, tier="B")

    assert exc.value.code == "BLOCKED_DIFF_POLICY"
    assert adapter.calls == []
