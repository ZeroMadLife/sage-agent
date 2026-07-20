"""Policy checks above raw coding tool permissions."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Any, Literal

from core.coding.context import WorkspaceContext
from core.coding.tools.base import RegisteredTool

SHELL_OPERATOR_RE = re.compile(r"^[;&|]+$")
SHELL_READ_COMMANDS = frozenset(
    {"cat", "find", "grep", "head", "less", "ls", "rg", "tail"}
)


@dataclass(frozen=True)
class ToolPolicyDecision:
    """A policy decision for one tool request."""

    decision: Literal["allow", "deny"]
    reason: str
    message: str = ""

    @classmethod
    def allow(cls, reason: str = "policy_ok") -> ToolPolicyDecision:
        return cls("allow", reason)

    @classmethod
    def deny(cls, reason: str, message: str) -> ToolPolicyDecision:
        return cls("deny", reason, message)

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"


class ToolPolicyChecker:
    """Check higher-level usage policies such as prior reads."""

    def __init__(self, workspace: WorkspaceContext) -> None:
        self.workspace = workspace

    def check(self, tool: RegisteredTool, args: dict[str, Any]) -> ToolPolicyDecision:
        """Return whether this tool call follows coding-agent policy."""
        args = args or {}
        if tool.name == "patch_file" and not self._has_fresh_read(args.get("path", "")):
            return self._prior_read_required(tool.name, str(args.get("path", "")))
        if tool.name == "write_file":
            try:
                path = self.workspace.path(str(args.get("path", "")))
            except ValueError as exc:
                return ToolPolicyDecision.deny("path_invalid", str(exc))
            if path.exists() and path.is_file() and not self._has_fresh_read(path):
                return self._prior_read_required(tool.name, str(args.get("path", "")))
        if tool.name == "run_shell":
            command = str(args.get("command", "")).strip()
            if _contains_root_filesystem_scan(command):
                return ToolPolicyDecision.deny(
                    "shell_root_scan_forbidden",
                    "error: run_shell cannot scan the filesystem root; scope the command "
                    "to the current workspace or a known subdirectory",
                )
            if _network_command_lacks_timeout(command):
                return ToolPolicyDecision.deny(
                    "shell_network_timeout_required",
                    "error: curl/wget requires explicit connect and total timeouts; use "
                    "curl --connect-timeout N --max-time N or wget --timeout=N",
                )
            if _is_ordinary_shell_read(command):
                return ToolPolicyDecision.deny(
                    "shell_search_should_use_tool",
                    "error: run_shell is not for ordinary workspace search/read; "
                    "use search, read_file, or list_files first",
                )
        return ToolPolicyDecision.allow()

    def _has_fresh_read(self, path: Any) -> bool:
        try:
            return self.workspace.has_fresh_read(
                path
            ) or self.workspace.has_self_authored_freshness(path)
        except ValueError:
            return False

    @staticmethod
    def _prior_read_required(tool_name: str, path: str) -> ToolPolicyDecision:
        return ToolPolicyDecision.deny(
            "prior_read_required",
            f"error: {tool_name} requires a fresh read_file of {path} before modifying it",
        )


def _is_ordinary_shell_read(command: str) -> bool:
    """Return true only when every shell command is a direct workspace read."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return False

    command_names: list[str] = []
    expecting_command = True
    for token in tokens:
        if SHELL_OPERATOR_RE.fullmatch(token):
            expecting_command = True
            continue
        if expecting_command:
            command_names.append(token.rsplit("/", 1)[-1])
            expecting_command = False

    return bool(command_names) and all(
        name in SHELL_READ_COMMANDS for name in command_names
    )


def _contains_root_filesystem_scan(command: str) -> bool:
    return bool(
        re.search(
            r"(?:^|[;&|]\s*)find\s+/(?:\s|$)",
            command,
            flags=re.IGNORECASE,
        )
    )


def _network_command_lacks_timeout(command: str) -> bool:
    if re.search(r"(?:^|[;&|]\s*)curl(?:\s|$)", command, flags=re.IGNORECASE):
        has_connect_timeout = bool(
            re.search(r"--connect-timeout(?:=|\s+)\d", command, flags=re.IGNORECASE)
        )
        has_total_timeout = bool(
            re.search(r"(?:--max-time(?:=|\s+)|(?:^|\s)-m\s*)\d", command, flags=re.IGNORECASE)
        )
        if not has_connect_timeout or not has_total_timeout:
            return True
    if re.search(r"(?:^|[;&|]\s*)wget(?:\s|$)", command, flags=re.IGNORECASE):
        return not bool(
            re.search(r"--timeout(?:=|\s+)\d", command, flags=re.IGNORECASE)
        )
    return False
