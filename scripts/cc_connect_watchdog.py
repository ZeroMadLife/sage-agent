#!/usr/bin/env python3
"""Independent launchd watchdog for the Sage cc-connect gateway."""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import logging.handlers
import os
import plistlib
import re
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

LABEL = "com.sage.cc-connect-watchdog"
DEFAULT_STATE_ROOT = Path.home() / ".local/state/sage-cc-connect-watchdog"
DEFAULT_LAUNCHER = Path.home() / ".local/bin/sage-cc-connect-watchdog"
DEFAULT_PLIST = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
DEFAULT_SOCKET = Path.home() / ".cc-connect/run/api.sock"
LOGGER = logging.getLogger("sage.cc_connect_watchdog")


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class WatchdogConfig:
    cc_connect_bin: str
    project: str
    session_key: str
    expected_cron_ids: tuple[str, ...]
    api_socket: Path
    restart_cooldown_seconds: int = 900
    recovery_timeout_seconds: int = 30

    @classmethod
    def load(cls, path: Path) -> WatchdogConfig:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("watchdog config must be a JSON object")
        required = {
            "cc_connect_bin",
            "project",
            "session_key",
            "expected_cron_ids",
            "api_socket",
        }
        missing = sorted(required - raw.keys())
        if missing:
            raise ValueError(f"watchdog config missing fields: {', '.join(missing)}")
        cron_values = raw["expected_cron_ids"]
        if not isinstance(cron_values, list):
            raise ValueError("expected_cron_ids must be a JSON array")
        cron_ids = tuple(str(value) for value in cron_values)
        if not cron_ids:
            raise ValueError("watchdog config requires at least one cron id")
        return cls(
            cc_connect_bin=str(raw["cc_connect_bin"]),
            project=str(raw["project"]),
            session_key=str(raw["session_key"]),
            expected_cron_ids=cron_ids,
            api_socket=Path(str(raw["api_socket"])).expanduser(),
            restart_cooldown_seconds=int(raw.get("restart_cooldown_seconds", 900)),
            recovery_timeout_seconds=int(raw.get("recovery_timeout_seconds", 30)),
        )


@dataclass(frozen=True)
class HealthReport:
    healthy: bool
    checks: dict[str, bool]
    error: str | None = None

    @property
    def restart_required(self) -> bool:
        return not self.checks.get("daemon", False) or not self.checks.get(
            "api_socket", False
        )


Runner = Callable[[Sequence[str], str | None, int], CommandResult]
SocketProbe = Callable[[Path], bool]


def run_command(
    command: Sequence[str], input_text: str | None = None, timeout: int = 15
) -> CommandResult:
    try:
        completed = subprocess.run(
            list(command),
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CommandResult(returncode=124, stderr=type(exc).__name__)
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def probe_unix_socket(path: Path) -> bool:
    try:
        if not stat.S_ISSOCK(path.stat().st_mode):
            return False
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2)
            client.connect(str(path))
    except (OSError, ValueError):
        return False
    return True


def _default_state() -> dict[str, object]:
    return {
        "state": "UNKNOWN",
        "checked_at": None,
        "last_healthy_at": None,
        "last_restart_at": None,
        "last_recovery_at": None,
        "consecutive_failures": 0,
        "restart_count": 0,
        "recovery_count": 0,
        "notification_pending": False,
        "notification_failure_count": 0,
        "last_alert_error": None,
        "last_error": None,
    }


def _load_state(path: Path) -> dict[str, object]:
    if not path.exists():
        return _default_state()
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_state()
    state = _default_state()
    if isinstance(loaded, dict):
        state.update(loaded)
    return state


def _state_int(state: dict[str, object], key: str) -> int:
    value = state.get(key, 0)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int | float | str):
        try:
            return int(value)
        except (OverflowError, ValueError):
            return 0
    return 0


def _write_private(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.unlink(missing_ok=True)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        os.chmod(path, mode)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_private_json(path: Path, value: dict[str, object]) -> None:
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _write_private(path, payload.encode("utf-8"), 0o600)


def _configure_logging(state_root: Path) -> None:
    state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(state_root, 0o700)
    log_path = state_root / "watchdog.log"
    if not log_path.exists():
        descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT, 0o600)
        os.close(descriptor)
    os.chmod(log_path, 0o600)
    handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOGGER.handlers.clear()
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False


class Watchdog:
    def __init__(
        self,
        config: WatchdogConfig,
        state_root: Path,
        runner: Runner = run_command,
        socket_probe: SocketProbe = probe_unix_socket,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.state_root = state_root
        self.state_path = state_root / "status.json"
        self.runner = runner
        self.socket_probe = socket_probe
        self.clock = clock
        self.sleeper = sleeper

    def health(self) -> HealthReport:
        daemon = self.runner(
            [self.config.cc_connect_bin, "daemon", "status"], None, 10
        )
        daemon_ok = daemon.returncode == 0 and re.search(
            r"^\s*Status:\s+Running\s*$", daemon.stdout, re.MULTILINE
        ) is not None
        socket_ok = self.socket_probe(self.config.api_socket)
        cron = self.runner([self.config.cc_connect_bin, "cron", "list"], None, 10)
        cron_ok = cron.returncode == 0 and all(
            re.search(rf"(?<![0-9a-f]){re.escape(cron_id)}(?![0-9a-f])", cron.stdout)
            for cron_id in self.config.expected_cron_ids
        )
        checks = {"daemon": daemon_ok, "api_socket": socket_ok, "cron_jobs": cron_ok}
        failed = [name for name, passed in checks.items() if not passed]
        error = f"failed checks: {', '.join(failed)}" if failed else None
        return HealthReport(healthy=not failed, checks=checks, error=error)

    def _notify_recovery(self, failure_count: int) -> bool:
        message = (
            "[Sage 飞书网关] watchdog 已恢复 cc-connect。\n"
            f"恢复前连续失败：{failure_count} 次。\n"
            "daemon、管理 socket 与 Loop 定时任务均已通过检查。"
        )
        result = self.runner(
            [
                self.config.cc_connect_bin,
                "send",
                "--stdin",
                "--project",
                self.config.project,
                "--session",
                self.config.session_key,
            ],
            message,
            15,
        )
        if result.returncode != 0:
            LOGGER.warning("recovery notification failed")
            return False
        return True

    def _notify_alert(self, error: str) -> bool:
        message = (
            "[Sage 飞书网关] watchdog 检测到需人工处理的异常。\n"
            f"检查结果：{error}。\n"
            "本次未重启 cc-connect，避免无效重启影响正在进行的会话。"
        )
        result = self.runner(
            [
                self.config.cc_connect_bin,
                "send",
                "--stdin",
                "--project",
                self.config.project,
                "--session",
                self.config.session_key,
            ],
            message,
            15,
        )
        if result.returncode != 0:
            LOGGER.warning("incident notification failed")
            return False
        return True

    def _notify_non_restartable_incident(
        self, state: dict[str, object], report: HealthReport
    ) -> None:
        error = report.error or "unknown health failure"
        if state.get("last_alert_error") == error:
            return
        if not self._notify_alert(error):
            return
        state["last_alert_error"] = error
        _write_private_json(self.state_path, state)

    def _flush_recovery_notification(self, state: dict[str, object]) -> None:
        if not state.get("notification_pending"):
            return
        failure_count = _state_int(state, "notification_failure_count")
        if not self._notify_recovery(failure_count):
            return
        state["notification_pending"] = False
        state["notification_failure_count"] = 0
        _write_private_json(self.state_path, state)

    def _persist_healthy(
        self, state: dict[str, object], now: float, recovered: bool
    ) -> None:
        recovery_failure_count = _state_int(state, "consecutive_failures")
        state.update(
            {
                "state": "HEALTHY",
                "checked_at": now,
                "last_healthy_at": now,
                "consecutive_failures": 0,
                "last_alert_error": None,
                "last_error": None,
            }
        )
        if recovered:
            state["last_recovery_at"] = now
            state["recovery_count"] = _state_int(state, "recovery_count") + 1
            state["notification_pending"] = True
            state["notification_failure_count"] = recovery_failure_count
        _write_private_json(self.state_path, state)

    def run_once(self) -> bool:
        now = self.clock()
        state = _load_state(self.state_path)
        report = self.health()
        if report.healthy:
            recovered = state.get("state") == "UNHEALTHY"
            self._persist_healthy(state, now, recovered=recovered)
            if recovered:
                LOGGER.info("gateway recovered without watchdog restart")
            self._flush_recovery_notification(state)
            return True

        failure_count = _state_int(state, "consecutive_failures") + 1
        state.update(
            {
                "state": "UNHEALTHY",
                "checked_at": now,
                "consecutive_failures": failure_count,
                "last_error": report.error,
            }
        )
        if not report.restart_required:
            _write_private_json(self.state_path, state)
            self._notify_non_restartable_incident(state, report)
            LOGGER.warning("gateway degraded; restart would not repair failed checks")
            return False

        last_restart = state.get("last_restart_at")
        elapsed_since_restart = (
            now - float(last_restart)
            if isinstance(last_restart, int | float)
            else None
        )
        in_cooldown = elapsed_since_restart is not None and (
            0 <= elapsed_since_restart < self.config.restart_cooldown_seconds
        )
        if in_cooldown:
            _write_private_json(self.state_path, state)
            LOGGER.warning("gateway unhealthy; restart suppressed by cooldown")
            return False

        state["last_restart_at"] = now
        state["restart_count"] = _state_int(state, "restart_count") + 1
        _write_private_json(self.state_path, state)
        LOGGER.warning("gateway unhealthy; attempting daemon restart")
        restart = self.runner(
            [self.config.cc_connect_bin, "daemon", "restart"], None, 30
        )
        if restart.returncode != 0:
            LOGGER.error("daemon restart command failed")
            return False

        attempts = max(1, self.config.recovery_timeout_seconds // 2)
        for attempt in range(attempts):
            if attempt:
                self.sleeper(2)
            recovered_report = self.health()
            if recovered_report.healthy:
                recovered_at = self.clock()
                self._persist_healthy(state, recovered_at, recovered=True)
                self._flush_recovery_notification(state)
                LOGGER.info("gateway recovered after watchdog restart")
                return True
            if not recovered_report.restart_required:
                state["checked_at"] = self.clock()
                state["last_error"] = recovered_report.error
                _write_private_json(self.state_path, state)
                self._notify_non_restartable_incident(state, recovered_report)
                LOGGER.warning("gateway restarted but a non-restartable check still fails")
                return False

        state["checked_at"] = self.clock()
        state["last_error"] = "restart completed but health checks still fail"
        _write_private_json(self.state_path, state)
        LOGGER.error("gateway remains unhealthy after restart")
        return False


def _lock_or_skip(lock_path: Path) -> int | None:
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        return None
    return descriptor


def check_command(config_path: Path) -> int:
    state_root = config_path.parent
    _configure_logging(state_root)
    lock = _lock_or_skip(state_root / "watchdog.lock")
    if lock is None:
        LOGGER.info("another watchdog check is active")
        return 0
    try:
        config = WatchdogConfig.load(config_path)
        return 0 if Watchdog(config, state_root).run_once() else 1
    except (OSError, ValueError, json.JSONDecodeError):
        LOGGER.exception("watchdog check failed before health evaluation")
        return 2
    finally:
        os.close(lock)


def doctor_command(config_path: Path) -> int:
    config = WatchdogConfig.load(config_path)
    report = Watchdog(config, config_path.parent).health()
    print(json.dumps({"healthy": report.healthy, "checks": report.checks}, indent=2))
    return 0 if report.healthy else 1


def status_command(config_path: Path) -> int:
    state = _load_state(config_path.parent / "status.json")
    result = run_command(
        ["/bin/launchctl", "print", f"gui/{os.getuid()}/{LABEL}"], None, 5
    )
    state["launchd_loaded"] = result.returncode == 0
    print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _cron_binding(cc_connect_bin: str, cron_id: str) -> tuple[str, str]:
    result = run_command([cc_connect_bin, "cron", "info", cron_id], None, 10)
    if result.returncode != 0:
        raise RuntimeError(f"cannot read source cron: {cron_id}")
    try:
        data = json.loads(result.stdout)
        project = str(data["project"])
        session_key = str(data["session_key"])
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RuntimeError("source cron has no usable project/session binding") from exc
    if not project or not session_key:
        raise RuntimeError("source cron has an empty project/session binding")
    return project, session_key


def _launcher_text(
    python_bin: str,
    script_path: Path,
    config_path: Path,
    launcher: Path,
    plist: Path,
) -> str:
    base = " ".join(shlex.quote(part) for part in [python_bin, str(script_path)])
    quoted_config = shlex.quote(str(config_path))
    uninstall = " ".join(
        shlex.quote(part)
        for part in [
            python_bin,
            str(script_path),
            "uninstall",
            "--state-root",
            str(config_path.parent),
            "--launcher",
            str(launcher),
            "--plist",
            str(plist),
        ]
    )
    return (
        "#!/bin/sh\n"
        "set -eu\n"
        "command_name=${1:-check}\n"
        "case \"$command_name\" in\n"
        "  check|doctor|status)\n"
        "    if [ \"$#\" -gt 0 ]; then shift; fi\n"
        f"    exec {base} \"$command_name\" --config {quoted_config} \"$@\"\n"
        "    ;;\n"
        "  uninstall)\n"
        "    shift\n"
        f"    exec {uninstall} \"$@\"\n"
        "    ;;\n"
        "  *)\n"
        "    echo \"usage: sage-cc-connect-watchdog [check|doctor|status|uninstall]\" >&2\n"
        "    exit 2\n"
        "    ;;\n"
        "esac\n"
    )


def _plist_bytes(launcher: Path, interval_seconds: int) -> bytes:
    payload = {
        "Label": LABEL,
        "ProgramArguments": [str(launcher)],
        "RunAtLoad": True,
        "StartInterval": interval_seconds,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "EnvironmentVariables": {"HOME": str(Path.home())},
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True)


def install_command(args: argparse.Namespace) -> int:
    requested_bin = Path(args.cc_connect_bin).expanduser()
    if not requested_bin.is_absolute():
        discovered_bin = shutil.which(str(requested_bin))
        if discovered_bin is None:
            raise RuntimeError(f"cc-connect binary not found: {requested_bin}")
        requested_bin = Path(discovered_bin)
    cc_connect_bin = str(requested_bin.absolute())
    if not Path(cc_connect_bin).is_file() or not os.access(cc_connect_bin, os.X_OK):
        raise RuntimeError(f"cc-connect binary not found: {cc_connect_bin}")
    project, session_key = _cron_binding(cc_connect_bin, args.source_cron_id)
    expected_cron_ids = tuple(dict.fromkeys(args.expected_cron_id))
    if args.source_cron_id not in expected_cron_ids:
        expected_cron_ids += (args.source_cron_id,)

    state_root = Path(args.state_root).expanduser().resolve()
    state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(state_root, 0o700)
    config_path = state_root / "config.json"
    launcher = Path(args.launcher).expanduser().resolve()
    plist = Path(args.plist).expanduser().resolve()
    script_path = Path(__file__).resolve()
    config: dict[str, object] = {
        "cc_connect_bin": cc_connect_bin,
        "project": project,
        "session_key": session_key,
        "expected_cron_ids": list(expected_cron_ids),
        "api_socket": str(Path(args.api_socket).expanduser().resolve()),
        "restart_cooldown_seconds": args.restart_cooldown_seconds,
        "recovery_timeout_seconds": args.recovery_timeout_seconds,
    }
    _write_private_json(config_path, config)
    _write_private(
        launcher,
        _launcher_text(
            sys.executable, script_path, config_path, launcher, plist
        ).encode("utf-8"),
        0o700,
    )
    plist.parent.mkdir(parents=True, exist_ok=True)
    _write_private(plist, _plist_bytes(launcher, args.interval_seconds), 0o600)

    if not args.no_load:
        domain = f"gui/{os.getuid()}"
        run_command(["/bin/launchctl", "bootout", domain, str(plist)], None, 10)
        loaded = run_command(
            ["/bin/launchctl", "bootstrap", domain, str(plist)], None, 10
        )
        if loaded.returncode != 0:
            raise RuntimeError("launchd bootstrap failed")

    print(f"watchdog installed: {LABEL}")
    print(f"status: {launcher} status")
    return 0


def uninstall_command(args: argparse.Namespace) -> int:
    state_root = Path(args.state_root).expanduser().resolve()
    launcher = Path(args.launcher).expanduser().resolve()
    plist = Path(args.plist).expanduser().resolve()
    domain = f"gui/{os.getuid()}"
    run_command(["/bin/launchctl", "bootout", domain, str(plist)], None, 10)
    plist.unlink(missing_ok=True)
    launcher.unlink(missing_ok=True)
    if args.purge_state and state_root.exists():
        shutil.rmtree(state_root)
    print(f"watchdog uninstalled: {LABEL}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    install = subparsers.add_parser("install", help="install the LaunchAgent")
    install.add_argument("--source-cron-id", required=True)
    install.add_argument("--expected-cron-id", action="append", default=[])
    install.add_argument(
        "--cc-connect-bin", default=shutil.which("cc-connect") or "cc-connect"
    )
    install.add_argument("--state-root", default=str(DEFAULT_STATE_ROOT))
    install.add_argument("--launcher", default=str(DEFAULT_LAUNCHER))
    install.add_argument("--plist", default=str(DEFAULT_PLIST))
    install.add_argument("--api-socket", default=str(DEFAULT_SOCKET))
    install.add_argument("--interval-seconds", type=int, default=300)
    install.add_argument("--restart-cooldown-seconds", type=int, default=900)
    install.add_argument("--recovery-timeout-seconds", type=int, default=30)
    install.add_argument("--no-load", action="store_true", help=argparse.SUPPRESS)

    for name in ("check", "doctor", "status"):
        command = subparsers.add_parser(name)
        command.add_argument(
            "--config", default=str(DEFAULT_STATE_ROOT / "config.json")
        )

    uninstall = subparsers.add_parser("uninstall")
    uninstall.add_argument("--state-root", default=str(DEFAULT_STATE_ROOT))
    uninstall.add_argument("--launcher", default=str(DEFAULT_LAUNCHER))
    uninstall.add_argument("--plist", default=str(DEFAULT_PLIST))
    uninstall.add_argument("--purge-state", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            if args.interval_seconds < 60 or args.restart_cooldown_seconds < 60:
                parser.error("interval and cooldown must be at least 60 seconds")
            return install_command(args)
        if args.command == "check":
            return check_command(Path(args.config).expanduser().resolve())
        if args.command == "doctor":
            return doctor_command(Path(args.config).expanduser().resolve())
        if args.command == "status":
            return status_command(Path(args.config).expanduser().resolve())
        if args.command == "uninstall":
            return uninstall_command(args)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    parser.error(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
