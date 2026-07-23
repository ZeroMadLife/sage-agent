"""Deterministic GitHub adapter; credentials remain owned by gh/Keychain."""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

from core.loop_harness.errors import LoopBlockedError
from core.loop_harness.models import PullRequestReceipt, ValidationResult, WorkerResult

_LOOP_BRANCH = re.compile(r"codex/loop-frontend-[a-f0-9]{12}")
_PR_URL = re.compile(r"https://github\.com/[^/\s]+/[^/\s]+/pull/(\d+)")
_BLOCKING_LABELS = {
    "dependencies",
    "do-not-merge",
    "high-risk",
    "loop-pause",
    "manual-review",
    "security",
}
_SUCCESSFUL_CHECK_CONCLUSIONS = {"SUCCESS", "NEUTRAL", "SKIPPED"}


class GitHubAdapter:
    def __init__(
        self,
        *,
        gh_bin: Path,
        repository: str,
        target_branch: str,
        timeout_seconds: int = 120,
        checks_timeout_seconds: int = 15 * 60,
        poll_interval_seconds: float = 10.0,
    ) -> None:
        self.gh_bin = gh_bin
        self.repository = repository
        self.target_branch = target_branch
        self.timeout_seconds = timeout_seconds
        self.checks_timeout_seconds = checks_timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds

    def probe(self) -> None:
        if not self.gh_bin.is_file():
            raise LoopBlockedError("BLOCKED_GITHUB", "GitHub CLI is unavailable")
        result = self._run("auth", "status", "--hostname", "github.com")
        if result.returncode != 0:
            raise LoopBlockedError(
                "BLOCKED_GITHUB_AUTH", "GitHub CLI authentication is unavailable"
            )
        repository = self._run("repo", "view", self.repository, "--json", "nameWithOwner")
        if repository.returncode != 0:
            raise LoopBlockedError(
                "BLOCKED_GITHUB_AUTH", "private GitHub repository access is unavailable"
            )

    def require_pr_capacity(self) -> None:
        result = self._run(
            "pr",
            "list",
            "--repo",
            self.repository,
            "--base",
            self.target_branch,
            "--state",
            "open",
            "--json",
            "number,headRefName",
        )
        payload = _json_list(result, "BLOCKED_GITHUB")
        if any(
            isinstance(row, dict)
            and _LOOP_BRANCH.fullmatch(str(row.get("headRefName", ""))) is not None
            for row in payload
        ):
            raise LoopBlockedError("SKIPPED_PR_CAPACITY", "an open Loop PR already exists")

    def create_draft(
        self,
        *,
        branch: str,
        head_sha: str,
        candidate: WorkerResult,
        validation: ValidationResult,
        tier: str,
    ) -> PullRequestReceipt:
        _validate_branch(branch)
        _validate_sha(head_sha)
        existing = self._find_by_branch(branch)
        if existing is not None:
            if existing.head_sha != head_sha:
                raise LoopBlockedError(
                    "BLOCKED_PR_HEAD_DRIFT", "existing PR head does not match candidate"
                )
            return existing

        title = _title(candidate.summary)
        body = _body(candidate, validation, tier=tier, head_sha=head_sha)
        _require_chinese(title, body)
        result = self._run(
            "pr",
            "create",
            "--repo",
            self.repository,
            "--base",
            self.target_branch,
            "--head",
            branch,
            "--draft",
            "--title",
            title,
            "--body-file",
            "-",
            input_text=body,
        )
        if result.returncode != 0:
            raise LoopBlockedError("BLOCKED_GITHUB_PR", "GitHub Draft PR creation failed")
        match = _PR_URL.search(result.stdout)
        if match is None:
            raise LoopBlockedError("BLOCKED_GITHUB_OUTPUT", "GitHub PR URL is invalid")
        return PullRequestReceipt(int(match.group(1)), match.group(0), branch, head_sha)

    def merge_tier_a(
        self,
        *,
        receipt: PullRequestReceipt,
        base_sha: str,
        tier: str,
    ) -> str:
        if tier != "A":
            raise LoopBlockedError(
                "BLOCKED_DIFF_POLICY", "only deterministic Tier A may auto-merge"
            )
        _validate_branch(receipt.branch)
        _validate_sha(receipt.head_sha)
        _validate_sha(base_sha)
        metadata = self._pr_metadata(receipt.number)
        self._require_merge_candidate(metadata, receipt=receipt, base_sha=base_sha)
        if bool(metadata.get("isDraft")):
            ready = self._run("pr", "ready", str(receipt.number), "--repo", self.repository)
            if ready.returncode != 0:
                raise LoopBlockedError("BLOCKED_GITHUB_PR", "GitHub PR could not be marked ready")

        metadata = self._wait_for_successful_checks(
            receipt=receipt,
            base_sha=base_sha,
        )
        self._require_merge_candidate(metadata, receipt=receipt, base_sha=base_sha)
        merge = self._run(
            "pr",
            "merge",
            str(receipt.number),
            "--repo",
            self.repository,
            "--squash",
            "--delete-branch",
            "--match-head-commit",
            receipt.head_sha,
        )
        if merge.returncode != 0:
            raise LoopBlockedError("BLOCKED_GITHUB_MERGE", "GitHub rejected the Tier A merge")
        merged = self._pr_metadata(receipt.number)
        if str(merged.get("state")) != "MERGED":
            raise LoopBlockedError("BLOCKED_GITHUB_MERGE", "GitHub did not report the PR as merged")
        merge_commit = merged.get("mergeCommit")
        if not isinstance(merge_commit, dict):
            raise LoopBlockedError(
                "BLOCKED_GITHUB_OUTPUT", "GitHub merge commit metadata is missing"
            )
        merged_sha = str(merge_commit.get("oid", ""))
        _validate_sha(merged_sha)
        return merged_sha

    def _wait_for_successful_checks(
        self,
        *,
        receipt: PullRequestReceipt,
        base_sha: str,
    ) -> dict[str, object]:
        deadline = time.monotonic() + self.checks_timeout_seconds
        while True:
            metadata = self._pr_metadata(receipt.number)
            self._require_merge_candidate(metadata, receipt=receipt, base_sha=base_sha)
            checks = metadata.get("statusCheckRollup")
            if not isinstance(checks, list) or not checks:
                status = "pending"
            else:
                status = _check_rollup_status(checks)
            if status == "success":
                return metadata
            if status == "failure":
                raise LoopBlockedError("BLOCKED_GITHUB_CHECKS", "GitHub checks did not all pass")
            if time.monotonic() >= deadline:
                raise LoopBlockedError(
                    "BLOCKED_GITHUB_CHECKS_TIMEOUT", "GitHub checks did not finish in time"
                )
            time.sleep(self.poll_interval_seconds)

    def _require_merge_candidate(
        self,
        metadata: dict[str, object],
        *,
        receipt: PullRequestReceipt,
        base_sha: str,
    ) -> None:
        number = metadata.get("number")
        if not isinstance(number, int) or isinstance(number, bool) or number != receipt.number:
            raise LoopBlockedError("BLOCKED_GITHUB_OUTPUT", "GitHub PR number changed")
        is_draft = metadata.get("isDraft")
        if not isinstance(is_draft, bool):
            raise LoopBlockedError("BLOCKED_GITHUB_OUTPUT", "GitHub draft state is invalid")
        if str(metadata.get("state")) != "OPEN":
            raise LoopBlockedError("BLOCKED_GITHUB_PR", "GitHub PR is not open")
        if str(metadata.get("headRefName")) != receipt.branch:
            raise LoopBlockedError("BLOCKED_PR_HEAD_DRIFT", "GitHub PR branch changed")
        if str(metadata.get("headRefOid")) != receipt.head_sha:
            raise LoopBlockedError("BLOCKED_PR_HEAD_DRIFT", "GitHub PR head changed")
        if str(metadata.get("baseRefName")) != self.target_branch:
            raise LoopBlockedError("BLOCKED_BASE_DRIFT", "GitHub PR target branch changed")
        if str(metadata.get("baseRefOid")) != base_sha:
            raise LoopBlockedError("BLOCKED_BASE_DRIFT", "GitHub PR base changed")
        if str(metadata.get("reviewDecision", "")) not in {"", "APPROVED"}:
            raise LoopBlockedError(
                "BLOCKED_HUMAN_REVIEW", "GitHub requires manual review for the PR"
            )
        labels = metadata.get("labels")
        if not isinstance(labels, list):
            raise LoopBlockedError("BLOCKED_GITHUB_OUTPUT", "GitHub labels are invalid")
        label_names = {
            str(label.get("name", "")).strip().casefold()
            for label in labels
            if isinstance(label, dict)
        }
        if label_names & _BLOCKING_LABELS:
            raise LoopBlockedError(
                "BLOCKED_HUMAN_REVIEW", "a manual-review or high-risk label is present"
            )

    def _pr_metadata(self, number: int) -> dict[str, object]:
        result = self._run(
            "pr",
            "view",
            str(number),
            "--repo",
            self.repository,
            "--json",
            "number,state,isDraft,headRefName,headRefOid,baseRefName,baseRefOid,"
            "reviewDecision,labels,statusCheckRollup,mergeCommit",
        )
        return _json_object(result, "BLOCKED_GITHUB")

    def _find_by_branch(self, branch: str) -> PullRequestReceipt | None:
        result = self._run(
            "pr",
            "list",
            "--repo",
            self.repository,
            "--head",
            branch,
            "--state",
            "all",
            "--json",
            "number,url,headRefName,headRefOid",
        )
        rows = _json_list(result, "BLOCKED_GITHUB")
        if not rows:
            return None
        if len(rows) != 1 or not isinstance(rows[0], dict):
            raise LoopBlockedError("BLOCKED_GITHUB_OUTPUT", "GitHub PR lookup is ambiguous")
        row = rows[0]
        try:
            receipt = PullRequestReceipt(
                number=int(row["number"]),
                url=str(row["url"]),
                branch=str(row["headRefName"]),
                head_sha=str(row["headRefOid"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise LoopBlockedError(
                "BLOCKED_GITHUB_OUTPUT", "GitHub PR metadata is invalid"
            ) from exc
        _validate_branch(receipt.branch)
        _validate_sha(receipt.head_sha)
        if _PR_URL.fullmatch(receipt.url) is None:
            raise LoopBlockedError("BLOCKED_GITHUB_OUTPUT", "GitHub PR URL is invalid")
        return receipt

    def _run(self, *args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [str(self.gh_bin), *args],
                input=input_text,
                capture_output=True,
                check=False,
                text=True,
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LoopBlockedError("BLOCKED_GITHUB", "GitHub CLI request failed") from exc


def _json_list(result: subprocess.CompletedProcess[str], code: str) -> list[object]:
    if result.returncode != 0:
        raise LoopBlockedError(code, "GitHub CLI request failed")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise LoopBlockedError("BLOCKED_GITHUB_OUTPUT", "GitHub JSON is invalid") from exc
    if not isinstance(payload, list):
        raise LoopBlockedError("BLOCKED_GITHUB_OUTPUT", "GitHub JSON is not a list")
    return payload


def _json_object(result: subprocess.CompletedProcess[str], code: str) -> dict[str, object]:
    if result.returncode != 0:
        raise LoopBlockedError(code, "GitHub CLI request failed")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise LoopBlockedError("BLOCKED_GITHUB_OUTPUT", "GitHub JSON is invalid") from exc
    if not isinstance(payload, dict):
        raise LoopBlockedError("BLOCKED_GITHUB_OUTPUT", "GitHub JSON is not an object")
    return payload


def _check_rollup_status(checks: list[object]) -> str:
    pending = False
    for raw in checks:
        if not isinstance(raw, dict):
            return "failure"
        if "status" in raw:
            status = str(raw.get("status", "")).upper()
            if status != "COMPLETED":
                pending = True
                continue
            if str(raw.get("conclusion", "")).upper() not in _SUCCESSFUL_CHECK_CONCLUSIONS:
                return "failure"
            continue
        state = str(raw.get("state", "")).upper()
        if state in {"PENDING", "EXPECTED"}:
            pending = True
        elif state != "SUCCESS":
            return "failure"
    return "pending" if pending else "success"


def _title(summary: str) -> str:
    normalized = _safe_markdown(summary, limit=60)
    return f"fix(loop): {normalized[:60]}"


def _body(
    candidate: WorkerResult,
    validation: ValidationResult,
    *,
    tier: str,
    head_sha: str,
) -> str:
    evidence = (
        "\n".join(f"- {_safe_markdown(item)}" for item in candidate.evidence) or "- 无额外证据"
    )
    files = "\n".join(f"- `{path}`" for path in candidate.changed_files)
    steps = "\n".join(
        f"- `{step.name}`：退出码 {step.exit_code}，{step.duration_seconds:.2f}s"
        for step in validation.steps
    )
    risks = (
        "\n".join(f"- {_safe_markdown(item)}" for item in candidate.risk_reasons)
        or "- 局部前端修改"
    )
    return (
        "## 问题证据\n\n"
        f"{evidence}\n\n"
        "## 修改内容\n\n"
        f"{files}\n\n"
        "## 验证结果\n\n"
        f"{steps}\n\n"
        "## 风险与回滚\n\n"
        f"- Controller 分级：Tier {tier}\n"
        f"- 审查绑定 head：`{head_sha}`\n"
        f"{risks}\n"
        "- 回滚方式：关闭本 PR，不合入目标分支。\n"
    )


def _require_chinese(title: str, body: str) -> None:
    required_headings = ("问题证据", "修改内容", "验证结果", "风险与回滚")
    if not any("\u4e00" <= char <= "\u9fff" for char in title):
        raise LoopBlockedError("BLOCKED_PR_LANGUAGE", "PR title must contain Chinese")
    if any(heading not in body for heading in required_headings):
        raise LoopBlockedError("BLOCKED_PR_LANGUAGE", "PR body template is incomplete")


def _safe_markdown(value: str, *, limit: int = 300) -> str:
    normalized = " ".join(value.split())[:limit]
    return (
        normalized.replace("@", "＠")
        .replace("<", "＜")
        .replace(">", "＞")
        .replace("[", "［")
        .replace("]", "］")
    )


def _validate_branch(branch: str) -> None:
    if _LOOP_BRANCH.fullmatch(branch) is None:
        raise LoopBlockedError("BLOCKED_PR_BRANCH", "Loop PR branch is invalid")


def _validate_sha(value: str) -> None:
    if len(value) != 40 or any(char not in "0123456789abcdef" for char in value):
        raise LoopBlockedError("BLOCKED_PR_HEAD", "PR head SHA is invalid")
