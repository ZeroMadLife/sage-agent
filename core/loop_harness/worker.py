"""Read-only, ephemeral Codex Worker with strict structured output."""

from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Literal, cast

from core.loop_harness import POLICY_VERSION
from core.loop_harness.errors import LoopBlockedError
from core.loop_harness.models import FixerResult, WorkerResult, WorkerVerdict

_LOOP_MODEL = "gpt-5.6-luna"
_LOOP_PROVIDER_URL = "https://api.honglin.asia"


class CodexWorker:
    def __init__(
        self,
        *,
        codex_bin: Path,
        controller_root: Path,
        reports_root: Path,
        timeout_seconds: int,
    ) -> None:
        self.codex_bin = codex_bin
        self.controller_root = controller_root
        self.reports_root = reports_root
        self.timeout_seconds = timeout_seconds

    def probe(self) -> str:
        try:
            result = subprocess.run(
                [str(self.codex_bin), "--version"],
                capture_output=True,
                check=False,
                text=True,
                timeout=15,
                env=_sanitized_environment(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LoopBlockedError("BLOCKED_CODEX", "Codex probe failed") from exc
        if result.returncode != 0:
            raise LoopBlockedError("BLOCKED_CODEX", "Codex executable is not healthy")
        return result.stdout.strip()

    def run(
        self,
        *,
        worktree: Path,
        run_id: str,
        base_sha: str,
        scan_scope: tuple[str, ...],
        protected_paths_digest: str,
        phase: str = "DRY_RUN_READ_ONLY",
    ) -> WorkerResult:
        prompt_path = self.controller_root / "docs/loop-harness/PROMPT.md"
        schema_path = self.controller_root / "core/loop_harness/worker_result.schema.json"
        prompt = _build_prompt(
            prompt_path=prompt_path,
            run_id=run_id,
            base_sha=base_sha,
            scan_scope=scan_scope,
            protected_paths_digest=protected_paths_digest,
            phase=phase,
        )
        self.reports_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            with tempfile.TemporaryDirectory(
                prefix=f"{run_id}-", dir=self.reports_root
            ) as temporary:
                output_path = Path(temporary) / "worker-result.json"
                command = [
                    str(self.codex_bin),
                    "exec",
                    "--sandbox",
                    "read-only",
                    "--ephemeral",
                    "--ignore-user-config",
                    "--strict-config",
                    *_controlled_codex_args(),
                    "--output-schema",
                    str(schema_path),
                    "--output-last-message",
                    str(output_path),
                    "--color",
                    "never",
                    "-C",
                    str(worktree),
                    "-",
                ]
                result = subprocess.run(
                    command,
                    input=prompt,
                    capture_output=True,
                    check=False,
                    text=True,
                    timeout=self.timeout_seconds,
                    env=_sanitized_environment(),
                )
                if result.returncode != 0:
                    raise LoopBlockedError(
                        "BLOCKED_WORKER", f"Codex Worker exited with {result.returncode}"
                    )
                try:
                    return _parse_result(output_path)
                except LoopBlockedError as exc:
                    if exc.code != "BLOCKED_WORKER_OUTPUT":
                        raise
                    return self._retry_invalid_output(
                        command=command,
                        prompt=(
                            f"{prompt}\n"
                            "上一次输出未通过严格 JSON 契约校验。请重新检查所有字段和类型，"
                            "只输出一个完整、合法且不带 Markdown 围栏的 JSON 对象。\n"
                        ),
                    )
        except subprocess.TimeoutExpired as exc:
            raise LoopBlockedError("BLOCKED_WORKER_TIMEOUT", "Codex Worker timed out") from exc

    def _retry_invalid_output(self, *, command: list[str], prompt: str) -> WorkerResult:
        """Retry only malformed structured output, using a fresh output file."""
        with tempfile.TemporaryDirectory(
            prefix="worker-retry-", dir=self.reports_root
        ) as temporary:
            output_path = Path(temporary) / "worker-result.json"
            retry_command = _replace_output_path(command, output_path)
            result = subprocess.run(
                retry_command,
                input=prompt,
                capture_output=True,
                check=False,
                text=True,
                timeout=self.timeout_seconds,
                env=_sanitized_environment(),
            )
            if result.returncode != 0:
                raise LoopBlockedError(
                    "BLOCKED_WORKER", f"Codex Worker retry exited with {result.returncode}"
                )
            return _parse_result(output_path)


class CodexFixer:
    """Workspace-write Worker constrained by a deterministic job envelope."""

    def __init__(
        self,
        *,
        codex_bin: Path,
        controller_root: Path,
        reports_root: Path,
        timeout_seconds: int,
    ) -> None:
        self.codex_bin = codex_bin
        self.controller_root = controller_root
        self.reports_root = reports_root
        self.timeout_seconds = timeout_seconds

    def run(
        self,
        *,
        worktree: Path,
        run_id: str,
        base_sha: str,
        allowed_paths: tuple[str, ...],
        dirty_paths: tuple[str, ...],
        protected_paths_digest: str,
    ) -> FixerResult:
        prompt_path = self.controller_root / "docs/loop-harness/PROMPT.md"
        schema_path = self.controller_root / "core/loop_harness/fixer_result.schema.json"
        prompt = _build_fixer_prompt(
            prompt_path=prompt_path,
            run_id=run_id,
            base_sha=base_sha,
            allowed_paths=allowed_paths,
            dirty_paths=dirty_paths,
            protected_paths_digest=protected_paths_digest,
        )
        self.reports_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            with tempfile.TemporaryDirectory(
                prefix=f"{run_id}-fix-", dir=self.reports_root
            ) as temporary:
                output_path = Path(temporary) / "fixer-result.json"
                command = [
                    str(self.codex_bin),
                    "exec",
                    "--sandbox",
                    "workspace-write",
                    "--ephemeral",
                    "--ignore-user-config",
                    "--strict-config",
                    *_controlled_codex_args(),
                    "--output-schema",
                    str(schema_path),
                    "--output-last-message",
                    str(output_path),
                    "--color",
                    "never",
                    "-C",
                    str(worktree),
                    "-",
                ]
                result = subprocess.run(
                    command,
                    input=prompt,
                    capture_output=True,
                    check=False,
                    text=True,
                    timeout=self.timeout_seconds,
                    env=_sanitized_environment(),
                )
                if result.returncode != 0:
                    raise LoopBlockedError(
                        "BLOCKED_FIXER", f"Codex Fixer exited with {result.returncode}"
                    )
                return _parse_fixer_result(output_path)
        except subprocess.TimeoutExpired as exc:
            raise LoopBlockedError("BLOCKED_FIXER_TIMEOUT", "Codex Fixer timed out") from exc


def _build_prompt(
    *,
    prompt_path: Path,
    run_id: str,
    base_sha: str,
    scan_scope: tuple[str, ...],
    protected_paths_digest: str,
    phase: str,
) -> str:
    policy_prompt = prompt_path.read_text(encoding="utf-8")
    envelope = {
        "job_id": run_id,
        "base_sha": base_sha,
        "policy_version": POLICY_VERSION,
        "scan_scope": list(scan_scope),
        "max_files_read": 12,
        "max_tool_calls": 12,
        "max_files": 0,
        "max_changed_lines": 0,
        "deadline_seconds": 10 * 60 if phase == "SHADOW_SCAN" else 40 * 60,
        "protected_paths_digest": protected_paths_digest,
        "phase": phase,
    }
    return (
        f"{policy_prompt}\n\n## Controller job envelope\n\n"
        f"```json\n{json.dumps(envelope, ensure_ascii=False, sort_keys=True)}\n```\n\n"
        "只检查 scan_scope。最多读取 12 个文件、调用 12 次工具；不要运行全量测试或构建。"
        "优先静态核对组件与同目录测试，没有高置信证据就立即 NO_OP。"
        "不要编辑文件，最终只输出 JSON。\n"
    )


def _build_fixer_prompt(
    *,
    prompt_path: Path,
    run_id: str,
    base_sha: str,
    allowed_paths: tuple[str, ...],
    dirty_paths: tuple[str, ...],
    protected_paths_digest: str,
) -> str:
    policy_prompt = prompt_path.read_text(encoding="utf-8")
    envelope = {
        "job_id": run_id,
        "base_sha": base_sha,
        "policy_version": POLICY_VERSION,
        "allowed_paths": list(allowed_paths),
        "dirty_paths": list(dirty_paths),
        "max_production_files": 2,
        "max_test_files": 1,
        "max_changed_lines": 80,
        "deadline_seconds": 15 * 60,
        "protected_paths_digest": protected_paths_digest,
        "phase": "SHADOW_WRITE",
    }
    return (
        f"{policy_prompt}\n\n## Controller fixer envelope\n\n"
        f"```json\n{json.dumps(envelope, ensure_ascii=False, sort_keys=True)}\n```\n\n"
        "只修复该候选，只能编辑 allowed_paths。禁止 commit、push、联网、安装依赖或修改配置。"
        "不要触碰 dirty_paths。完成后只输出 JSON。\n"
    )


def _parse_result(path: Path) -> WorkerResult:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LoopBlockedError("BLOCKED_WORKER_OUTPUT", "Worker output is invalid") from exc
    if not isinstance(raw, dict):
        raise LoopBlockedError("BLOCKED_WORKER_OUTPUT", "Worker output is not an object")
    required = {
        "verdict",
        "summary",
        "evidence",
        "reproduction",
        "changed_files",
        "tests",
        "risk_reasons",
        "suggested_tier",
        "confidence",
    }
    if set(raw) != required:
        raise LoopBlockedError("BLOCKED_WORKER_OUTPUT", "Worker output fields are invalid")
    verdict = raw["verdict"]
    tier = raw["suggested_tier"]
    confidence = raw["confidence"]
    if verdict not in {"NO_OP", "FIX", "FRONTEND_CANDIDATE", "REPORT", "BLOCKED"}:
        raise LoopBlockedError("BLOCKED_WORKER_OUTPUT", "Worker verdict is invalid")
    if tier not in {"A", "B", "C"}:
        raise LoopBlockedError("BLOCKED_WORKER_OUTPUT", "Worker tier is invalid")
    if not isinstance(confidence, int | float) or isinstance(confidence, bool):
        raise LoopBlockedError("BLOCKED_WORKER_OUTPUT", "Worker confidence is invalid")
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise LoopBlockedError("BLOCKED_WORKER_OUTPUT", "Worker confidence is out of range")
    summary = _string(raw["summary"], "summary", limit=500)
    return WorkerResult(
        verdict=cast(WorkerVerdict, verdict),
        summary=summary,
        evidence=_string_list(raw["evidence"], "evidence"),
        reproduction=_string_list(raw["reproduction"], "reproduction"),
        changed_files=_string_list(raw["changed_files"], "changed_files", max_items=3),
        tests=_string_list(raw["tests"], "tests"),
        risk_reasons=_string_list(raw["risk_reasons"], "risk_reasons"),
        suggested_tier=cast(Literal["A", "B", "C"], tier),
        confidence=float(confidence),
    )


def _parse_fixer_result(path: Path) -> FixerResult:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LoopBlockedError("BLOCKED_FIXER_OUTPUT", "Fixer output is invalid") from exc
    if not isinstance(raw, dict):
        raise LoopBlockedError("BLOCKED_FIXER_OUTPUT", "Fixer output is not an object")
    required = {"summary", "changed_files", "tests", "risk_reasons"}
    if set(raw) != required:
        raise LoopBlockedError("BLOCKED_FIXER_OUTPUT", "Fixer output fields are invalid")
    return FixerResult(
        summary=_string(raw["summary"], "summary", limit=500),
        changed_files=_string_list(raw["changed_files"], "changed_files", max_items=3),
        tests=_string_list(raw["tests"], "tests"),
        risk_reasons=_string_list(raw["risk_reasons"], "risk_reasons"),
    )


def _replace_output_path(command: list[str], output_path: Path) -> list[str]:
    try:
        index = command.index("--output-last-message") + 1
    except ValueError as exc:
        raise LoopBlockedError(
            "BLOCKED_WORKER_OUTPUT", "Worker output path is not configured"
        ) from exc
    if index >= len(command):
        raise LoopBlockedError(
            "BLOCKED_WORKER_OUTPUT", "Worker output path is not configured"
        )
    retry_command = list(command)
    retry_command[index] = str(output_path)
    return retry_command


def _string(value: object, field: str, *, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise LoopBlockedError("BLOCKED_WORKER_OUTPUT", f"Worker {field} is invalid")
    return " ".join(value.split())


def _string_list(value: object, field: str, *, max_items: int = 10) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > max_items:
        raise LoopBlockedError("BLOCKED_WORKER_OUTPUT", f"Worker {field} is invalid")
    return tuple(_string(item, field, limit=500) for item in value)


def _sanitized_environment() -> dict[str, str]:
    allowed = ("HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "CODEX_HOME")
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    environment["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    return environment


def _controlled_codex_args() -> list[str]:
    """Use the authenticated gateway without loading personal Codex config."""
    args = [
        "--config",
        'model_provider="loop_gateway"',
        "--config",
        'model_providers.loop_gateway.name="Loop Gateway"',
        "--config",
        f'model_providers.loop_gateway.base_url="{_LOOP_PROVIDER_URL}"',
        "--config",
        'model_providers.loop_gateway.wire_api="responses"',
        "--config",
        "model_providers.loop_gateway.requires_openai_auth=true",
        "--model",
        _LOOP_MODEL,
        "--config",
        'model_reasoning_effort="low"',
    ]
    for feature in (
        "plugins",
        "remote_plugin",
        "plugin_sharing",
        "browser_use",
        "browser_use_external",
        "browser_use_full_cdp_access",
        "in_app_browser",
        "image_generation",
    ):
        args.extend(("--disable", feature))
    return args
