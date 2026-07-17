"""Deterministic GitHub adapter; credentials remain owned by gh/Keychain."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from core.loop_harness.errors import LoopBlockedError
from core.loop_harness.models import PullRequestReceipt, ValidationResult, WorkerResult

_LOOP_BRANCH = re.compile(r"codex/loop-frontend-[a-f0-9]{12}")
_PR_URL = re.compile(r"https://github\.com/[^/\s]+/[^/\s]+/pull/(\d+)")


class GitHubAdapter:
    def __init__(
        self,
        *,
        gh_bin: Path,
        repository: str,
        target_branch: str,
        timeout_seconds: int = 120,
    ) -> None:
        self.gh_bin = gh_bin
        self.repository = repository
        self.target_branch = target_branch
        self.timeout_seconds = timeout_seconds

    def probe(self) -> None:
        if not self.gh_bin.is_file():
            raise LoopBlockedError("BLOCKED_GITHUB", "GitHub CLI is unavailable")
        result = self._run("auth", "status", "--hostname", "github.com")
        if result.returncode != 0:
            raise LoopBlockedError(
                "BLOCKED_GITHUB_AUTH", "GitHub CLI authentication is unavailable"
            )
        repository = self._run(
            "repo", "view", self.repository, "--json", "nameWithOwner"
        )
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

    def _run(
        self, *args: str, input_text: str | None = None
    ) -> subprocess.CompletedProcess[str]:
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
        "\n".join(f"- {_safe_markdown(item)}" for item in candidate.evidence)
        or "- 无额外证据"
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
