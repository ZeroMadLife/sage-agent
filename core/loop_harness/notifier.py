"""Deterministic proactive delivery through the existing cc-connect session."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from core.loop_harness.errors import LoopBlockedError

_FEISHU_SESSION = re.compile(r"feishu:[A-Za-z0-9_-]{1,160}:[A-Za-z0-9_-]{1,160}")


class CcConnectNotifier:
    def __init__(self, *, cc_connect_bin: Path, timeout_seconds: int = 30) -> None:
        self.cc_connect_bin = cc_connect_bin
        self.timeout_seconds = timeout_seconds

    def send(self, *, project: str, session_key: str, message: str) -> None:
        if project != "sage" or _FEISHU_SESSION.fullmatch(session_key) is None:
            raise LoopBlockedError("BLOCKED_NOTIFY", "notification target is invalid")
        payload = message.strip()
        if not payload or len(payload) > 3000:
            raise LoopBlockedError("BLOCKED_NOTIFY", "notification payload is invalid")
        try:
            result = subprocess.run(
                [
                    str(self.cc_connect_bin),
                    "send",
                    "--stdin",
                    "--project",
                    project,
                    "--session",
                    session_key,
                ],
                input=payload,
                capture_output=True,
                check=False,
                text=True,
                timeout=self.timeout_seconds,
                env=_sanitized_environment(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LoopBlockedError("BLOCKED_NOTIFY", "cc-connect notification failed") from exc
        if result.returncode != 0:
            raise LoopBlockedError("BLOCKED_NOTIFY", "cc-connect notification failed")


def _sanitized_environment() -> dict[str, str]:
    allowed = ("HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR")
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    environment["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    return environment
