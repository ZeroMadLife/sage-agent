"""Approval queue and dangerous command detection for Sage coding tools."""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

ApprovalChoice = Literal["once", "session", "always", "deny"]

DANGEROUS_PATTERNS: tuple[tuple[str, str, str], ...] = (
    (r"\brm\s+-[^\n;|&]*r", "Recursive delete command requires approval.", "rm_recursive"),
    (r"\bgit\s+reset\s+--hard\b", "Hard git reset can discard work.", "git_reset_hard"),
    (
        r"\bgit\s+push\b[^\n;|&]*--force",
        "Force push can overwrite remote history.",
        "git_force_push",
    ),
    (r"\bchmod\s+777\b", "World-writable permission change requires approval.", "chmod_777"),
    (
        r"\bcurl\b.*\|\s*(sh|bash)\b",
        "Piping remote curl output to shell requires approval.",
        "curl_pipe_shell",
    ),
    (
        r"\bwget\b.*\|\s*(sh|bash)\b",
        "Piping remote wget output to shell requires approval.",
        "wget_pipe_shell",
    ),
    (r"(^|\s)sudo(\s|$)", "sudo command requires approval.", "sudo"),
    (r"(^|\s)>+\s*/etc/", "Writing into /etc requires approval.", "write_etc"),
    (r"(^|\s)>+\s*~/.ssh/", "Writing into ~/.ssh requires approval.", "write_ssh"),
    (
        r"\bdocker\s+compose\s+down\b",
        "Stopping compose services requires approval.",
        "docker_compose_down",
    ),
    (r"\bkill\s+-9\b", "Force-killing processes requires approval.", "kill_9"),
)


@dataclass
class ApprovalEntry:
    """One pending approval request."""

    approval_id: str
    session_id: str
    tool: str
    args: dict[str, Any]
    description: str
    pattern_key: str
    event: threading.Event = field(default_factory=threading.Event)
    result: ApprovalChoice | None = None
    created_at: float = field(default_factory=time.monotonic)

    def to_dict(self) -> dict[str, Any]:
        """Return API-safe representation of this approval."""
        return {
            "approval_id": self.approval_id,
            "session_id": self.session_id,
            "tool": self.tool,
            "args": self.args,
            "description": self.description,
            "pattern_key": self.pattern_key,
        }


class ApprovalManager:
    """Session-level approval queue and blocking coordination."""

    def __init__(self) -> None:
        self._queues: dict[str, list[ApprovalEntry]] = {}
        self._session_approved: dict[str, set[str]] = {}
        self._lock = threading.Lock()

    def submit(
        self,
        session_id: str,
        tool: str,
        args: dict[str, Any],
        description: str,
        pattern_key: str,
    ) -> ApprovalEntry:
        """Create and enqueue a pending approval."""
        entry = ApprovalEntry(
            approval_id=f"appr_{uuid4().hex[:12]}",
            session_id=session_id,
            tool=tool,
            args=dict(args),
            description=description,
            pattern_key=pattern_key,
        )
        with self._lock:
            self._queues.setdefault(session_id, []).append(entry)
        return entry

    def resolve(self, session_id: str, approval_id: str, choice: ApprovalChoice) -> bool:
        """Resolve a pending approval and wake the waiting tool execution."""
        with self._lock:
            entry = self._find_entry_locked(session_id, approval_id)
            if entry is None:
                return False
            entry.result = choice
            if choice in {"session", "always"}:
                self._session_approved.setdefault(session_id, set()).add(entry.pattern_key)
            self._queues[session_id] = [
                item for item in self._queues.get(session_id, []) if item.approval_id != approval_id
            ]
        entry.event.set()
        return True

    def pending(self, session_id: str) -> dict[str, Any] | None:
        """Return the oldest pending approval for a session."""
        with self._lock:
            queue = self._queues.get(session_id, [])
            return queue[0].to_dict() if queue else None

    def cancel_session(self, session_id: str) -> None:
        """Wake and deny every pending approval for a stopped session."""
        with self._lock:
            entries = list(self._queues.get(session_id, []))
            self._queues[session_id] = []
            for entry in entries:
                entry.result = "deny"
        for entry in entries:
            entry.event.set()

    def is_session_approved(self, session_id: str, pattern_key: str) -> bool:
        """Return whether this risky pattern is approved for the current session."""
        with self._lock:
            return pattern_key in self._session_approved.get(session_id, set())

    def _find_entry_locked(self, session_id: str, approval_id: str) -> ApprovalEntry | None:
        for entry in self._queues.get(session_id, []):
            if entry.approval_id == approval_id:
                return entry
        return None


def check_dangerous_command(command: str) -> tuple[bool, str, str]:
    """Return whether a shell command matches a dangerous pattern."""
    for pattern, description, pattern_key in DANGEROUS_PATTERNS:
        if re.search(pattern, command, flags=re.IGNORECASE | re.DOTALL):
            return True, description, pattern_key
    return False, "", ""
