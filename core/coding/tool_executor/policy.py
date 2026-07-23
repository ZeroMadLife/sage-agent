"""Policy checks above raw coding tool permissions."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Any, Literal

from core.coding.context import WorkspaceContext
from core.coding.tools.base import RegisteredTool

SHELL_OPERATOR_RE = re.compile(r"^[;&|]+$")
SHELL_READ_COMMANDS = frozenset({"cat", "find", "grep", "head", "less", "ls", "rg", "tail"})


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

    def __init__(
        self,
        workspace: WorkspaceContext,
        *,
        allow_network_retrieval: bool = True,
    ) -> None:
        self.workspace = workspace
        self.allow_network_retrieval = allow_network_retrieval

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
            if not self.allow_network_retrieval and _contains_network_access(command):
                return ToolPolicyDecision.deny(
                    "retrieval_gate_web_not_selected",
                    "error: this turn did not select Web retrieval; use a new turn that "
                    "explicitly requests Web evidence instead of accessing the network "
                    "through run_shell",
                )
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
                    "use search, read_file, or list_files first. For external artifacts, "
                    "copy or clone them under workspace tmp/ before using structured "
                    "read tools; never use the operating-system /tmp directory",
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

    return bool(command_names) and all(name in SHELL_READ_COMMANDS for name in command_names)


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
        return not bool(re.search(r"--timeout(?:=|\s+)\d", command, flags=re.IGNORECASE))
    return False


_NETWORK_PROGRAMS = frozenset(
    {
        "curl",
        "wget",
        "ssh",
        "scp",
        "sftp",
        "ftp",
        "telnet",
        "nc",
        "netcat",
    }
)
_PACKAGE_NETWORK_SUBCOMMANDS = frozenset(
    {
        "add",
        "audit",
        "download",
        "fetch",
        "info",
        "install",
        "publish",
        "search",
        "update",
        "upgrade",
        "view",
    }
)


def _contains_network_access(command: str) -> bool:
    """Conservatively detect network-capable shell fallbacks for a gated turn.

    Production container sandboxes also run with ``--network none``. This
    check keeps trusted local-development runs on the same model-visible
    retrieval route without disabling ordinary tests and local build commands.
    """
    lowered = command.casefold()
    if re.search(
        r"\b(?:curl|wget|ssh|scp|sftp|ftp|telnet|netcat)\b|(?:^|[\s;&|])nc(?:\s|$)",
        lowered,
    ):
        return True
    if re.search(
        r"\b(?:urllib(?:\.request)?|requests|aiohttp|httpx|socket|http\.client)\b",
        lowered,
    ):
        return True
    if re.search(r"\b(?:fetch\s*\(|https?\.(?:get|request)\s*\(|net\.)", lowered):
        return True
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return True

    command_tokens: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if SHELL_OPERATOR_RE.fullmatch(token):
            if current:
                command_tokens.append(current)
                current = []
            continue
        current.append(token)
    if current:
        command_tokens.append(current)

    for segment in command_tokens:
        program = segment[0].rsplit("/", 1)[-1].casefold()
        if program in _NETWORK_PROGRAMS:
            return True
        if program in {
            "pip",
            "pip3",
            "npm",
            "pnpm",
            "yarn",
            "brew",
            "apt",
            "apt-get",
        } and any(token.casefold() in _PACKAGE_NETWORK_SUBCOMMANDS for token in segment[1:]):
            return True
        if program == "cargo" and any(
            token.casefold() in {"add", "fetch", "install", "publish", "search", "update"}
            for token in segment[1:]
        ):
            return True
        if program == "go" and any(token.casefold() in {"get", "install"} for token in segment[1:]):
            return True
        if program == "git" and any(
            token.casefold() in {"clone", "fetch", "pull", "push", "ls-remote"}
            for token in segment[1:]
        ):
            return True
    return False
