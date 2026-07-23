from __future__ import annotations

import subprocess

from core.loop_harness.config import LoopConfig
from core.loop_harness.errors import LoopBlockedError
from core.loop_harness.git import RootStatus
from core.loop_harness.manifest import write_manifest
from core.loop_harness.models import (
    ArtifactReceipt,
    DiffSnapshot,
    FixerResult,
    PullRequestReceipt,
    ReviewerResult,
    ValidationResult,
    ValidationStep,
    WorkerResult,
)
from core.loop_harness.runner import _SCAN_SCOPES, LoopRunner
from core.loop_harness.state import LoopState


class FakeGit:
    def __init__(
        self,
        *,
        blocked: bool = False,
        dirty_paths: tuple[str, ...] = (),
        snapshot: DiffSnapshot | None = None,
    ) -> None:
        self.blocked = blocked
        self.dirty_paths = dirty_paths
        self.snapshot = snapshot or DiffSnapshot((), 0, 0, (), (), False)
        self.created = 0
        self.removed = 0
        self.discarded = 0
        self.snapshot_calls = 0
        self.commits = 0
        self.pushes = 0
        self.removed_candidate_branches = 0

    def require_clean_integration_root(self) -> RootStatus:
        if self.blocked:
            raise LoopBlockedError("BLOCKED_ROOT_DIRTY", "human work is present")
        return RootStatus("dev/sage-v7", "a" * 40, False)

    def require_integration_root(self, *, allow_dirty: bool) -> RootStatus:
        if self.blocked:
            raise LoopBlockedError("BLOCKED_ROOT_DIRTY", "human work is present")
        assert allow_dirty is True
        return RootStatus(
            "dev/sage-v7",
            "a" * 40,
            bool(self.dirty_paths),
            self.dirty_paths,
        )

    def fetch(self) -> None:
        return None

    def remote_sha(self) -> str:
        return "a" * 40

    def require_root_at_sha(self, status: RootStatus, remote_sha: str) -> None:
        assert status.head_sha == remote_sha

    def human_change_paths(self, status: RootStatus, remote_sha: str) -> tuple[str, ...]:
        assert remote_sha == "a" * 40
        return status.dirty_paths

    def create_detached_worktree(self, destination, base_sha: str) -> None:
        assert base_sha == "a" * 40
        destination.mkdir(parents=True)
        self.created += 1

    def remove_clean_worktree(self, destination) -> None:
        destination.rmdir()
        self.removed += 1

    def diff_snapshot(self, destination, *, base_sha: str) -> DiffSnapshot:
        assert destination.is_dir()
        assert base_sha == "a" * 40
        self.snapshot_calls += 1
        if self.snapshot_calls == 1:
            return DiffSnapshot((), 0, 0, (), (), False)
        return self.snapshot

    def remove_managed_worktree(self, destination, *, discard_changes: bool) -> None:
        assert discard_changes is True
        destination.rmdir()
        self.discarded += 1

    def diff_patch(self, destination, *, base_sha: str) -> str:
        assert destination.is_dir()
        assert base_sha == "a" * 40
        return "diff --git a/view.vue b/view.vue\n"

    def commit_candidate(self, destination, **kwargs) -> str:
        assert destination.is_dir()
        self.commits += 1
        return "b" * 40

    def push_candidate(self, destination, *, branch: str, head_sha: str) -> None:
        assert destination.is_dir()
        assert branch.startswith("codex/loop-frontend-")
        assert head_sha == "b" * 40
        self.pushes += 1

    def remove_local_candidate_branch(self, *, branch: str, head_sha: str) -> None:
        assert branch.startswith("codex/loop-frontend-")
        assert head_sha == "b" * 40
        self.removed_candidate_branches += 1


def test_scan_scopes_start_with_bounded_frontend_components() -> None:
    assert _SCAN_SCOPES[0] == ("frontend/src/components/assistant",)
    assert ("frontend/src",) not in _SCAN_SCOPES
    assert ("core/coding", "tests/core/coding") not in _SCAN_SCOPES


class FakeWorker:
    def __init__(self, result: WorkerResult) -> None:
        self.result = result
        self.calls = 0

    def probe(self) -> str:
        return "codex-cli test"

    def run(self, **kwargs) -> WorkerResult:
        self.calls += 1
        return self.result


class FakeFixer:
    def __init__(self, result: FixerResult) -> None:
        self.result = result
        self.calls = 0

    def run(self, **kwargs) -> FixerResult:
        self.calls += 1
        return self.result


class PausingFixer(FakeFixer):
    def __init__(self, result: FixerResult, state: LoopState) -> None:
        super().__init__(result)
        self.state = state

    def run(self, **kwargs) -> FixerResult:
        result = super().run(**kwargs)
        self.state.set_enabled(False, mode="PAUSED_MANUAL")
        return result


class FakeValidator:
    def __init__(self) -> None:
        self.calls = 0

    def validate(self, worktree) -> ValidationResult:
        self.calls += 1
        return ValidationResult(
            passed=True,
            steps=(ValidationStep("git-diff-check", 0, 0.01),),
        )


class FakeArtifactStore:
    def __init__(self, root) -> None:
        self.root = root
        self.calls = 0

    def save_shadow(self, *, run_id, patch, validation) -> ArtifactReceipt:
        self.calls += 1
        directory = self.root / run_id
        directory.mkdir(parents=True)
        return ArtifactReceipt(directory, "a" * 64, len(patch.encode()))


class FakeGitHub:
    def __init__(self) -> None:
        self.probes = 0
        self.capacity_checks = 0
        self.created = 0
        self.merged = 0

    def probe(self) -> None:
        self.probes += 1

    def require_pr_capacity(self) -> None:
        self.capacity_checks += 1

    def create_draft(self, *, branch: str, head_sha: str, **kwargs) -> PullRequestReceipt:
        self.created += 1
        return PullRequestReceipt(
            12,
            "https://github.com/ZeroMadLife/sage-agent/pull/12",
            branch,
            head_sha,
        )

    def merge_tier_a(self, *, receipt: PullRequestReceipt, base_sha: str, tier: str) -> str:
        assert receipt.number == 12
        assert receipt.head_sha == "b" * 40
        assert base_sha == "a" * 40
        assert tier == "A"
        self.merged += 1
        return "c" * 40


class FakeReviewer:
    def __init__(self) -> None:
        self.probes = 0
        self.reviews = 0

    def probe(self) -> None:
        self.probes += 1

    def review(self, **kwargs) -> ReviewerResult:
        self.reviews += 1
        return ReviewerResult(
            "PASS",
            "未发现阻断问题",
            (),
            "测试通过",
            "本次不要求截图",
            "边界通过",
            "保持 Draft",
        )


def _git(root, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def _config(tmp_path) -> LoopConfig:
    controller = tmp_path / "controller"
    (controller / "docs/loop-harness").mkdir(parents=True)
    (controller / "AGENTS.md").write_text("rules\n", encoding="utf-8")
    (controller / "docs/loop-harness/POLICY.md").write_text("policy\n", encoding="utf-8")
    _git(controller, "init")
    _git(controller, "config", "user.name", "Loop Test")
    _git(controller, "config", "user.email", "loop@example.com")
    _git(controller, "add", ".")
    _git(controller, "commit", "-m", "controller")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    config = LoopConfig(
        repo_root=repo,
        controller_root=controller,
        state_root=tmp_path / "state",
        worktree_root=tmp_path / "worktrees",
        codex_bin=tmp_path / "codex",
        minimum_free_bytes=0,
    )
    config.codex_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    config.codex_bin.chmod(0o700)
    config.ensure_local_directories()
    write_manifest(controller, config.manifest_path)
    return config


def _result(verdict: str = "NO_OP") -> WorkerResult:
    return WorkerResult(
        verdict=verdict,
        summary="deterministic result",
        evidence=("evidence",) if verdict != "NO_OP" else (),
        reproduction=(),
        changed_files=(),
        tests=(),
        risk_reasons=(),
        suggested_tier="C",
        confidence=0.9,
    )


def test_runner_terminalizes_noop_and_cleans_worktree(tmp_path) -> None:
    config = _config(tmp_path)
    state = LoopState(config.database_path)
    state.initialize()
    state.set_enabled(True)
    git = FakeGit()
    worker = FakeWorker(_result())

    report = LoopRunner(config, state, git=git, worker=worker).run()

    assert report.state == "NO_OP"
    assert report.notification is None
    assert git.created == git.removed == 1
    assert worker.calls == 1
    assert list(config.worktree_root.iterdir()) == []
    assert list(config.reports_root.iterdir()) == []


def test_runner_downgrades_fix_to_report_without_writing(tmp_path) -> None:
    config = _config(tmp_path)
    state = LoopState(config.database_path)
    state.initialize()
    state.set_enabled(True)

    report = LoopRunner(
        config,
        state,
        git=FakeGit(),
        worker=FakeWorker(_result("FIX")),
    ).run()

    assert report.state == "REPORT"
    assert report.error_code is None
    assert report.notification is not None
    assert "dry-run candidate" in report.notification


def test_runner_pauses_after_three_root_dirty_failures(tmp_path) -> None:
    config = _config(tmp_path)
    state = LoopState(config.database_path)
    state.initialize()
    state.set_enabled(True)
    runner = LoopRunner(
        config,
        state,
        git=FakeGit(blocked=True),
        worker=FakeWorker(_result()),
    )

    first = runner.run()
    second = runner.run()
    third = runner.run()

    assert first.notification and "BLOCKED_ROOT_DIRTY" in first.notification
    assert second.notification is None
    assert third.notification and "自动暂停" in third.notification
    assert state.is_enabled() is False
    assert state.status()["consecutive_error_count"] == 3


def test_runner_shadow_writes_only_in_managed_worktree_and_discards_diff(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr("core.loop_harness.runner._SCAN_SCOPES", (("frontend/src",),))
    config = _config(tmp_path)
    state = LoopState(config.database_path)
    state.initialize()
    state.set_enabled(True, mode="SHADOW_WRITE")
    candidate_path = "frontend/src/views/KnowledgeView.vue"
    scanner_result = WorkerResult(
        verdict="FRONTEND_CANDIDATE",
        summary="修复知识库空状态间距",
        evidence=(f"{candidate_path}:20",),
        reproduction=("打开空知识库页面",),
        changed_files=(candidate_path,),
        tests=("KnowledgeView.test.ts",),
        risk_reasons=(),
        suggested_tier="A",
        confidence=0.95,
    )
    fixer = FakeFixer(
        FixerResult(
            summary="已修复知识库空状态间距",
            changed_files=(candidate_path,),
            tests=("npm run test -- --run",),
            risk_reasons=(),
        )
    )
    git = FakeGit(
        dirty_paths=("frontend/src/views/HumanView.vue",),
        snapshot=DiffSnapshot((candidate_path,), 4, 2, (), (), False),
    )

    report = LoopRunner(
        config,
        state,
        git=git,
        worker=FakeWorker(scanner_result),
        fixer=fixer,
        validator=FakeValidator(),
        artifact_store=FakeArtifactStore(config.reports_root),
    ).run()

    assert report.state == "SHADOW_VALIDATED"
    assert report.notification and "shadow" in report.notification
    assert fixer.calls == 1
    assert git.discarded == 1
    assert state.status()["shadow_validated_candidates"] == 1


def test_runner_shadow_does_not_start_fixer_for_dirty_overlap(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("core.loop_harness.runner._SCAN_SCOPES", (("frontend/src",),))
    config = _config(tmp_path)
    state = LoopState(config.database_path)
    state.initialize()
    state.set_enabled(True, mode="SHADOW_WRITE")
    candidate_path = "frontend/src/views/KnowledgeView.vue"
    scanner_result = WorkerResult(
        verdict="FRONTEND_CANDIDATE",
        summary="修复知识库空状态间距",
        evidence=(f"{candidate_path}:20",),
        reproduction=("打开空知识库页面",),
        changed_files=(candidate_path,),
        tests=(),
        risk_reasons=(),
        suggested_tier="A",
        confidence=0.95,
    )
    fixer = FakeFixer(FixerResult("不应执行", (candidate_path,), (), ()))

    report = LoopRunner(
        config,
        state,
        git=FakeGit(dirty_paths=(candidate_path,)),
        worker=FakeWorker(scanner_result),
        fixer=fixer,
        validator=FakeValidator(),
    ).run()

    assert report.state == "REPORT"
    assert fixer.calls == 0


def test_runner_shadow_invalidates_fixer_result_when_paused_mid_run(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("core.loop_harness.runner._SCAN_SCOPES", (("frontend/src",),))
    config = _config(tmp_path)
    state = LoopState(config.database_path)
    state.initialize()
    state.set_enabled(True, mode="SHADOW_WRITE")
    candidate_path = "frontend/src/views/KnowledgeView.vue"
    scanner_result = WorkerResult(
        verdict="FRONTEND_CANDIDATE",
        summary="修复知识库空状态间距",
        evidence=(f"{candidate_path}:20",),
        reproduction=("打开空知识库页面",),
        changed_files=(candidate_path,),
        tests=(),
        risk_reasons=(),
        suggested_tier="A",
        confidence=0.95,
    )
    fixer = PausingFixer(
        FixerResult("已修改但应失效", (candidate_path,), (), ()),
        state,
    )

    report = LoopRunner(
        config,
        state,
        git=FakeGit(snapshot=DiffSnapshot((candidate_path,), 2, 0, (), (), False)),
        worker=FakeWorker(scanner_result),
        fixer=fixer,
    ).run()

    assert report.state == "BLOCKED"
    assert report.error_code == "BLOCKED_MODE_CHANGED"
    assert state.is_enabled() is False


def test_runner_pr_canary_binds_draft_and_review_to_exact_head(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("core.loop_harness.runner._SCAN_SCOPES", (("frontend/src",),))
    config = _config(tmp_path)
    state = LoopState(config.database_path)
    state.initialize()
    state.set_enabled(True, mode="PR_CANARY")
    candidate_path = "frontend/src/views/KnowledgeView.vue"
    scanner_result = WorkerResult(
        verdict="FRONTEND_CANDIDATE",
        summary="修复知识库空状态间距",
        evidence=(f"{candidate_path}:20",),
        reproduction=("打开空知识库页面",),
        changed_files=(candidate_path,),
        tests=("KnowledgeView.test.ts",),
        risk_reasons=(),
        suggested_tier="A",
        confidence=0.95,
    )
    git = FakeGit(snapshot=DiffSnapshot((candidate_path,), 4, 2, (), (), False))
    github = FakeGitHub()
    reviewer = FakeReviewer()

    report = LoopRunner(
        config,
        state,
        git=git,
        worker=FakeWorker(scanner_result),
        fixer=FakeFixer(FixerResult("已修复知识库空状态间距", (candidate_path,), (), ())),
        validator=FakeValidator(),
        artifact_store=FakeArtifactStore(config.reports_root),
        github=github,
        reviewer=reviewer,
    ).run()

    assert report.state == "PR_DRAFT_REVIEWED"
    assert report.notification and "pull/12" in report.notification
    assert git.commits == git.pushes == 1
    assert git.removed_candidate_branches == 1
    assert github.created == reviewer.reviews == 1
    assert github.merged == 0
    assert state.status()["open_pull_requests"] == 1


def test_runner_auto_merges_only_tier_a_after_independent_review(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("core.loop_harness.runner._SCAN_SCOPES", (("frontend/src",),))
    config = _config(tmp_path)
    state = LoopState(config.database_path)
    state.initialize()
    state.set_enabled(True, mode="AUTO_MERGE_TIER_A")
    candidate_path = "frontend/src/views/KnowledgeView.vue"
    scanner_result = WorkerResult(
        verdict="FRONTEND_CANDIDATE",
        summary="修复知识库空状态间距",
        evidence=(f"{candidate_path}:20",),
        reproduction=("打开空知识库页面",),
        changed_files=(candidate_path,),
        tests=("KnowledgeView.test.ts",),
        risk_reasons=(),
        suggested_tier="A",
        confidence=0.95,
    )
    github = FakeGitHub()

    report = LoopRunner(
        config,
        state,
        git=FakeGit(snapshot=DiffSnapshot((candidate_path,), 4, 2, (), (), False)),
        worker=FakeWorker(scanner_result),
        fixer=FakeFixer(FixerResult("已修复知识库空状态间距", (candidate_path,), (), ())),
        validator=FakeValidator(),
        artifact_store=FakeArtifactStore(config.reports_root),
        github=github,
        reviewer=FakeReviewer(),
    ).run()

    assert report.state == "PR_AUTO_MERGED"
    assert report.notification and "已自动合并" in report.notification
    assert github.created == github.merged == 1
    assert state.status()["open_pull_requests"] == 0


def test_runner_keeps_tier_b_draft_for_manual_merge(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("core.loop_harness.runner._SCAN_SCOPES", (("frontend/src",),))
    config = _config(tmp_path)
    state = LoopState(config.database_path)
    state.initialize()
    state.set_enabled(True, mode="AUTO_MERGE_TIER_A")
    candidate_path = "frontend/src/views/KnowledgeView.vue"
    scanner_result = WorkerResult(
        verdict="FRONTEND_CANDIDATE",
        summary="修复知识库交互状态",
        evidence=(f"{candidate_path}:20",),
        reproduction=("切换知识库节点",),
        changed_files=(candidate_path,),
        tests=("KnowledgeView.test.ts",),
        risk_reasons=("包含行为变化",),
        suggested_tier="B",
        confidence=0.95,
    )
    github = FakeGitHub()

    report = LoopRunner(
        config,
        state,
        git=FakeGit(snapshot=DiffSnapshot((candidate_path,), 4, 2, (), (), True)),
        worker=FakeWorker(scanner_result),
        fixer=FakeFixer(FixerResult("已修复知识库交互状态", (candidate_path,), (), ())),
        validator=FakeValidator(),
        artifact_store=FakeArtifactStore(config.reports_root),
        github=github,
        reviewer=FakeReviewer(),
    ).run()

    assert report.state == "PR_DRAFT_REVIEWED"
    assert github.created == 1
    assert github.merged == 0
    assert state.status()["open_pull_requests"] == 1
