"""Policy checks above raw coding tool permissions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from core.coding.context import WorkspaceContext
from core.coding.tools.base import RegisteredTool

SHELL_SEARCH_RE = re.compile(r"(?:^|;|&&|\|\|)\s*(?:cat|less|head|grep|rg|find|ls)(?:\s|$)")


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
            if SHELL_SEARCH_RE.search(command):
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
