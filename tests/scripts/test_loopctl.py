from __future__ import annotations

import json
import os
import subprocess
import sys

from core.loop_harness.cli import main
from core.loop_harness.state import LoopState


def _git(root, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def test_install_writes_private_launcher_without_secrets(tmp_path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Loop Test")
    _git(repo, "config", "user.email", "loop@example.com")
    (repo / "README.md").write_text("repo\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "baseline")
    _git(repo, "branch", "-M", "dev/sage-v7")
    state_root = tmp_path / "state"
    worktrees = tmp_path / "worktrees"
    home = tmp_path / "home"
    launcher = tmp_path / "bin/sage-loopctl"
    secret = "must-not-leak"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SAGE_LOOP_REPO_ROOT", str(repo))
    monkeypatch.setenv("SAGE_LOOP_STATE_ROOT", str(state_root))
    monkeypatch.setenv("SAGE_LOOP_WORKTREE_ROOT", str(worktrees))
    monkeypatch.setenv("SAGE_LOOP_CODEX_BIN", "/bin/true")
    monkeypatch.setenv("UNRELATED_SECRET", secret)

    assert main(["install", "--refresh-manifest", "--launcher", str(launcher)]) == 0
    assert main(["enable", "--dry-run"]) == 0
    assert main(["status"]) == 0

    launcher_text = launcher.read_text(encoding="utf-8")
    assert secret not in launcher_text
    assert str(repo) in launcher_text
    assert str(state_root) in launcher_text
    assert f"export HOME={home}" in launcher_text
    assert launcher.stat().st_mode & 0o777 == 0o700
    status_output = capsys.readouterr().out
    json_start = status_output.index("{")
    status = json.loads(status_output[json_start:])
    assert status["enabled"] is True
    assert status["mode"] == "DRY_RUN"
    assert sys.executable in launcher_text
    assert "UNRELATED_SECRET" not in launcher_text
    assert os.access(launcher, os.X_OK)


def test_enable_shadow_write_is_explicit(tmp_path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Loop Test")
    _git(repo, "config", "user.email", "loop@example.com")
    (repo / "README.md").write_text("repo\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "baseline")
    _git(repo, "branch", "-M", "dev/sage-v7")
    state_root = tmp_path / "state"
    worktrees = tmp_path / "worktrees"
    monkeypatch.setenv("SAGE_LOOP_REPO_ROOT", str(repo))
    monkeypatch.setenv("SAGE_LOOP_STATE_ROOT", str(state_root))
    monkeypatch.setenv("SAGE_LOOP_WORKTREE_ROOT", str(worktrees))
    monkeypatch.setenv("SAGE_LOOP_CODEX_BIN", "/bin/true")

    assert main(["install", "--refresh-manifest", "--launcher", str(tmp_path / "bin/ctl")]) == 0
    assert main(["enable", "--shadow-write"]) == 0
    output = capsys.readouterr().out
    assert "shadow-write enabled" in output
    assert LoopState(state_root / "state.sqlite3").status()["mode"] == "SHADOW_WRITE"

    assert main(["enable", "--pr-canary"]) == 0
    output = capsys.readouterr().out
    assert "pr-canary enabled" in output
    assert LoopState(state_root / "state.sqlite3").status()["mode"] == "PR_CANARY"
