from __future__ import annotations

import json
import plistlib
from collections.abc import Sequence
from pathlib import Path

import scripts.cc_connect_watchdog as watchdog_module
from scripts.cc_connect_watchdog import (
    CommandResult,
    Watchdog,
    WatchdogConfig,
    main,
)


class FakeRunner:
    def __init__(self, *, healthy: bool = True) -> None:
        self.healthy = healthy
        self.commands: list[tuple[str, ...]] = []
        self.notifications: list[str] = []
        self.restart_count = 0

    def __call__(
        self, command: Sequence[str], input_text: str | None, timeout: int
    ) -> CommandResult:
        del timeout
        command_tuple = tuple(command)
        self.commands.append(command_tuple)
        if command_tuple[1:3] == ("daemon", "status"):
            if self.healthy:
                return CommandResult(0, "  Status:    Running\n")
            return CommandResult(1, "Status: Stopped\n")
        if command_tuple[1:3] == ("cron", "list"):
            return CommandResult(0, "✅ ae4eb665 task\n✅ 8f2f3158 digest\n")
        if command_tuple[1:3] == ("daemon", "restart"):
            self.restart_count += 1
            self.healthy = True
            return CommandResult(0)
        if command_tuple[1:3] == ("send", "--stdin"):
            self.notifications.append(input_text or "")
            return CommandResult(0)
        raise AssertionError(f"unexpected command: {command_tuple}")


def _config() -> WatchdogConfig:
    return WatchdogConfig(
        cc_connect_bin="/usr/local/bin/cc-connect",
        project="sage",
        session_key="feishu:test-session",
        expected_cron_ids=("ae4eb665", "8f2f3158"),
        api_socket=Path("/tmp/cc-connect.sock"),
        restart_cooldown_seconds=900,
        recovery_timeout_seconds=4,
    )


def test_healthy_check_is_silent_and_does_not_restart(tmp_path) -> None:
    runner = FakeRunner(healthy=True)
    watcher = Watchdog(_config(), tmp_path, runner=runner, socket_probe=lambda _: True)

    assert watcher.run_once() is True
    assert runner.restart_count == 0
    assert runner.notifications == []
    state = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert state["state"] == "HEALTHY"
    assert state["consecutive_failures"] == 0


def test_unhealthy_gateway_restarts_and_notifies_once(tmp_path) -> None:
    runner = FakeRunner(healthy=False)
    watcher = Watchdog(_config(), tmp_path, runner=runner, socket_probe=lambda _: runner.healthy)

    assert watcher.run_once() is True
    assert runner.restart_count == 1
    assert len(runner.notifications) == 1
    assert "watchdog 已恢复 cc-connect" in runner.notifications[0]
    state = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert state["state"] == "HEALTHY"
    assert state["restart_count"] == 1
    assert state["recovery_count"] == 1
    assert state["notification_pending"] is False


def test_failed_recovery_notification_is_retried(tmp_path) -> None:
    runner = FakeRunner(healthy=False)
    send_attempts = 0

    def flaky_runner(command: Sequence[str], input_text: str | None, timeout: int) -> CommandResult:
        nonlocal send_attempts
        if tuple(command)[1:3] == ("send", "--stdin"):
            send_attempts += 1
            if send_attempts == 1:
                return CommandResult(1, stderr="temporary failure")
        return runner(command, input_text, timeout)

    watcher = Watchdog(
        _config(),
        tmp_path,
        runner=flaky_runner,
        socket_probe=lambda _: runner.healthy,
    )

    assert watcher.run_once() is True
    state = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert state["notification_pending"] is True

    assert watcher.run_once() is True
    state = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert send_attempts == 2
    assert state["notification_pending"] is False


def test_restart_is_suppressed_during_cooldown(tmp_path) -> None:
    (tmp_path / "status.json").write_text(
        json.dumps(
            {
                "state": "UNHEALTHY",
                "last_restart_at": 950.0,
                "consecutive_failures": 1,
            }
        ),
        encoding="utf-8",
    )
    runner = FakeRunner(healthy=False)
    watcher = Watchdog(
        _config(),
        tmp_path,
        runner=runner,
        socket_probe=lambda _: False,
        clock=lambda: 1000.0,
    )

    assert watcher.run_once() is False
    assert runner.restart_count == 0
    assert runner.notifications == []
    state = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert state["consecutive_failures"] == 2


def test_missing_expected_cron_marks_health_unhealthy(tmp_path) -> None:
    runner = FakeRunner(healthy=True)

    def runner_without_digest(
        command: Sequence[str], input_text: str | None, timeout: int
    ) -> CommandResult:
        if tuple(command)[1:3] == ("cron", "list"):
            return CommandResult(0, "✅ ae4eb665 task\n")
        return runner(command, input_text, timeout)

    watcher = Watchdog(
        _config(),
        tmp_path,
        runner=runner_without_digest,
        socket_probe=lambda _: True,
    )

    report = watcher.health()
    assert report.healthy is False
    assert report.checks["cron_jobs"] is False
    assert report.restart_required is False

    assert watcher.run_once() is False
    assert watcher.run_once() is False
    assert runner.restart_count == 0
    assert len(runner.notifications) == 1
    assert "需人工处理的异常" in runner.notifications[0]


def test_install_keeps_session_out_of_launcher_and_plist(tmp_path, monkeypatch) -> None:
    cc_connect = tmp_path / "cc-connect"
    cc_connect.write_text("#!/bin/sh\n", encoding="utf-8")
    cc_connect.chmod(0o700)
    session = "feishu:private-session"
    commands: list[tuple[str, ...]] = []

    def fake_run(
        command: Sequence[str], input_text: str | None = None, timeout: int = 15
    ) -> CommandResult:
        del input_text, timeout
        command_tuple = tuple(command)
        commands.append(command_tuple)
        if command_tuple[1:3] == ("cron", "info"):
            return CommandResult(
                0,
                json.dumps({"project": "sage", "session_key": session}),
            )
        if command_tuple[:2] in {
            ("/bin/launchctl", "bootout"),
            ("/bin/launchctl", "bootstrap"),
        }:
            return CommandResult(0)
        raise AssertionError(f"unexpected command: {command_tuple}")

    monkeypatch.setattr(watchdog_module, "run_command", fake_run)
    state_root = tmp_path / "state"
    launcher = tmp_path / "bin/watchdog"
    plist = tmp_path / "LaunchAgents/watchdog.plist"

    assert (
        main(
            [
                "install",
                "--source-cron-id",
                "ae4eb665",
                "--expected-cron-id",
                "8f2f3158",
                "--cc-connect-bin",
                str(cc_connect),
                "--state-root",
                str(state_root),
                "--launcher",
                str(launcher),
                "--plist",
                str(plist),
            ]
        )
        == 0
    )

    config_path = state_root / "config.json"
    config = config_path.read_text(encoding="utf-8")
    launcher_text = launcher.read_text(encoding="utf-8")
    plist_text = plist.read_text(encoding="utf-8")
    plist_data = plistlib.loads(plist.read_bytes())
    assert session in config
    assert session not in launcher_text
    assert session not in plist_text
    assert "doctor|status|uninstall" in launcher_text
    assert config_path.stat().st_mode & 0o777 == 0o600
    assert launcher.stat().st_mode & 0o777 == 0o700
    assert plist_data["StartInterval"] == 300
    assert "ae4eb665" in config
    assert "8f2f3158" in config
    assert any(command[:2] == ("/bin/launchctl", "bootstrap") for command in commands)
    assert not any("kickstart" in command for command in commands)
