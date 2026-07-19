from __future__ import annotations

import json
from itertools import pairwise

import pytest

from core.loop_harness.errors import LoopBlockedError
from core.loop_harness.worker import (
    CodexFixer,
    CodexWorker,
    _build_prompt,
    _controlled_codex_args,
)


def _fake_codex(tmp_path, payload: object, *, fail_once: bool = False):
    executable = tmp_path / "codex"
    encoded = json.dumps(json.dumps(payload))
    script = (
        "#!/bin/sh\n"
        'if [ "${1:-}" = "--version" ]; then echo \'codex-cli test\'; exit 0; fi\n'
        "out=''\n"
        "while [ $# -gt 0 ]; do\n"
        '  if [ "$1" = "--output-last-message" ]; then shift; out=$1; fi\n'
        "  shift\n"
        "done\n"
    )
    if fail_once:
        marker = tmp_path / "codex-failed-once"
        script += (
            f"marker={json.dumps(str(marker))}\n"
            "if [ ! -e \"$marker\" ]; then : > \"$marker\"; printf '%s' '{' > \"$out\"; exit 0; fi\n"
        )
    script += f"printf '%s' {encoded} > \"$out\"\n"
    executable.write_text(script, encoding="utf-8")
    executable.chmod(0o700)
    return executable


def _valid_payload():
    return {
        "verdict": "NO_OP",
        "summary": "no deterministic issue found",
        "evidence": [],
        "reproduction": [],
        "changed_files": [],
        "tests": [],
        "risk_reasons": [],
        "suggested_tier": "C",
        "confidence": 0.8,
    }


def test_controlled_codex_args_pin_gateway_model_and_disable_expansive_features() -> None:
    args = _controlled_codex_args()

    assert "--ignore-user-config" not in args
    assert 'model_provider="loop_gateway"' in args
    assert 'model_providers.loop_gateway.base_url="https://api.honglin.asia"' in args
    assert "gpt-5.6-luna" in args
    assert 'model_reasoning_effort="low"' in args
    pairs = set(pairwise(args))
    for feature in ("plugins", "remote_plugin", "browser_use", "image_generation"):
        assert ("--disable", feature) in pairs


def test_scanner_prompt_has_bounded_inspection_budget(tmp_path) -> None:
    prompt_path = tmp_path / "PROMPT.md"
    prompt_path.write_text("rules", encoding="utf-8")

    prompt = _build_prompt(
        prompt_path=prompt_path,
        run_id="loop-test",
        base_sha="a" * 40,
        scan_scope=("frontend/src/components/assistant",),
        protected_paths_digest="b" * 64,
        phase="SHADOW_SCAN",
    )

    assert '"max_files_read": 12' in prompt
    assert '"max_tool_calls": 12' in prompt
    assert "不要运行全量测试或构建" in prompt


def test_worker_uses_temporary_output_and_parses_schema(tmp_path) -> None:
    controller = tmp_path / "controller"
    (controller / "docs/loop-harness").mkdir(parents=True)
    (controller / "core/loop_harness").mkdir(parents=True)
    (controller / "docs/loop-harness/PROMPT.md").write_text("rules", encoding="utf-8")
    (controller / "core/loop_harness/worker_result.schema.json").write_text("{}", encoding="utf-8")
    reports = tmp_path / "reports"
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    worker = CodexWorker(
        codex_bin=_fake_codex(tmp_path, _valid_payload()),
        controller_root=controller,
        reports_root=reports,
        timeout_seconds=10,
    )

    assert worker.probe() == "codex-cli test"
    result = worker.run(
        worktree=worktree,
        run_id="loop-test",
        base_sha="a" * 40,
        scan_scope=("core",),
        protected_paths_digest="b" * 64,
    )

    assert result.verdict == "NO_OP"
    assert list(reports.iterdir()) == []


def test_worker_retries_once_when_structured_output_is_invalid(tmp_path) -> None:
    controller = tmp_path / "controller"
    (controller / "docs/loop-harness").mkdir(parents=True)
    (controller / "core/loop_harness").mkdir(parents=True)
    (controller / "docs/loop-harness/PROMPT.md").write_text("rules", encoding="utf-8")
    (controller / "core/loop_harness/worker_result.schema.json").write_text("{}", encoding="utf-8")
    reports = tmp_path / "reports"
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    worker = CodexWorker(
        codex_bin=_fake_codex(tmp_path, _valid_payload(), fail_once=True),
        controller_root=controller,
        reports_root=reports,
        timeout_seconds=10,
    )

    result = worker.run(
        worktree=worktree,
        run_id="loop-test",
        base_sha="a" * 40,
        scan_scope=("core",),
        protected_paths_digest="b" * 64,
    )

    assert result.verdict == "NO_OP"
    assert list(reports.iterdir()) == []


def test_worker_rejects_non_object_output(tmp_path) -> None:
    controller = tmp_path / "controller"
    (controller / "docs/loop-harness").mkdir(parents=True)
    (controller / "core/loop_harness").mkdir(parents=True)
    (controller / "docs/loop-harness/PROMPT.md").write_text("rules", encoding="utf-8")
    (controller / "core/loop_harness/worker_result.schema.json").write_text("{}", encoding="utf-8")
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    worker = CodexWorker(
        codex_bin=_fake_codex(tmp_path, ["bad"]),
        controller_root=controller,
        reports_root=tmp_path / "reports",
        timeout_seconds=10,
    )

    with pytest.raises(LoopBlockedError) as exc:
        worker.run(
            worktree=worktree,
            run_id="loop-test",
            base_sha="a" * 40,
            scan_scope=("core",),
            protected_paths_digest="b" * 64,
        )
    assert exc.value.code == "BLOCKED_WORKER_OUTPUT"


def test_worker_rejects_out_of_range_confidence(tmp_path) -> None:
    payload = _valid_payload()
    payload["confidence"] = 2
    controller = tmp_path / "controller"
    (controller / "docs/loop-harness").mkdir(parents=True)
    (controller / "core/loop_harness").mkdir(parents=True)
    (controller / "docs/loop-harness/PROMPT.md").write_text("rules", encoding="utf-8")
    (controller / "core/loop_harness/worker_result.schema.json").write_text("{}", encoding="utf-8")
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    worker = CodexWorker(
        codex_bin=_fake_codex(tmp_path, payload),
        controller_root=controller,
        reports_root=tmp_path / "reports",
        timeout_seconds=10,
    )

    with pytest.raises(LoopBlockedError, match="out of range"):
        worker.run(
            worktree=worktree,
            run_id="loop-test",
            base_sha="a" * 40,
            scan_scope=("core",),
            protected_paths_digest="b" * 64,
        )


def test_scanner_accepts_frontend_candidate(tmp_path) -> None:
    payload = _valid_payload()
    payload.update(
        verdict="FRONTEND_CANDIDATE",
        changed_files=["frontend/src/views/KnowledgeView.vue"],
        suggested_tier="A",
    )
    controller = tmp_path / "controller"
    (controller / "docs/loop-harness").mkdir(parents=True)
    (controller / "core/loop_harness").mkdir(parents=True)
    (controller / "docs/loop-harness/PROMPT.md").write_text("rules", encoding="utf-8")
    (controller / "core/loop_harness/worker_result.schema.json").write_text(
        "{}", encoding="utf-8"
    )
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    worker = CodexWorker(
        codex_bin=_fake_codex(tmp_path, payload),
        controller_root=controller,
        reports_root=tmp_path / "reports",
        timeout_seconds=10,
    )

    result = worker.run(
        worktree=worktree,
        run_id="loop-test",
        base_sha="a" * 40,
        scan_scope=("frontend/src",),
        protected_paths_digest="b" * 64,
        phase="SHADOW_SCAN",
    )

    assert result.verdict == "FRONTEND_CANDIDATE"


def test_fixer_uses_workspace_write_and_parses_separate_schema(tmp_path) -> None:
    payload = {
        "summary": "修复知识库空状态间距",
        "changed_files": ["frontend/src/views/KnowledgeView.vue"],
        "tests": ["npm run test -- --run"],
        "risk_reasons": [],
    }
    controller = tmp_path / "controller"
    (controller / "docs/loop-harness").mkdir(parents=True)
    (controller / "core/loop_harness").mkdir(parents=True)
    (controller / "docs/loop-harness/PROMPT.md").write_text("rules", encoding="utf-8")
    (controller / "core/loop_harness/fixer_result.schema.json").write_text(
        "{}", encoding="utf-8"
    )
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    fixer = CodexFixer(
        codex_bin=_fake_codex(tmp_path, payload),
        controller_root=controller,
        reports_root=tmp_path / "reports",
        timeout_seconds=10,
    )

    result = fixer.run(
        worktree=worktree,
        run_id="loop-test",
        base_sha="a" * 40,
        allowed_paths=("frontend/src/views/KnowledgeView.vue",),
        dirty_paths=("frontend/src/views/HumanView.vue",),
        protected_paths_digest="b" * 64,
    )

    assert result.changed_files == ("frontend/src/views/KnowledgeView.vue",)
    assert result.summary == "修复知识库空状态间距"
