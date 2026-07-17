"""Permission gates for coding tool execution."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from core.coding.context import WorkspaceContext
from core.coding.tool_executor.approval import check_dangerous_command
from core.coding.tools.base import RegisteredTool

PermissionMode = Literal["default", "accept_edits", "auto", "plan"]
ApprovalCallback = Callable[[str, dict[str, Any]], bool]


@dataclass(frozen=True)
class PermissionDecision:
    """A permission decision for one tool request."""

    decision: Literal["allow", "deny"]
    reason: str
    security_event_type: str = ""

    @classmethod
    def allow(cls, reason: str) -> PermissionDecision:
        return cls("allow", reason)

    @classmethod
    def deny(cls, reason: str, security_event_type: str = "") -> PermissionDecision:
        return cls("deny", reason, security_event_type)

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"


class PermissionChecker:
    """Check raw tool permissions before policy-level checks."""

    def __init__(
        self,
        permission_mode: str = "default",
        write_scope: Sequence[str] | None = None,
        plan_mode: bool = False,
        read_only: bool = False,
        approval_callback: ApprovalCallback | None = None,
        approval_policy: str = "",
    ) -> None:
        self.permission_mode = permission_mode
        self.write_scope = tuple(write_scope or ())
        self.plan_mode = plan_mode
        self.read_only = read_only
        self.approval_callback = approval_callback
        self.approval_policy = approval_policy

    def check(
        self,
        tool: RegisteredTool,
        args: dict[str, Any],
        workspace: WorkspaceContext,
    ) -> PermissionDecision:
        """Return whether the tool call is permitted."""
        # Plan mode: only read-only tools
        if self.permission_mode == "plan" or self.plan_mode:
            if tool.read_only:
                return PermissionDecision.allow("plan_read_only")
            return PermissionDecision.deny(
                "plan_mode_tool_not_allowed",
                "plan_mode_write_guard",
            )

        if tool.name in {"write_file", "patch_file"} and self.write_scope:
            scope_decision = self._check_write_scope(args, workspace)
            if not scope_decision.allowed:
                return scope_decision

        if tool.read_only:
            return PermissionDecision.allow("read_only")
        if tool.requires_approval is False:
            return PermissionDecision.allow("approval_not_required")
        if self.read_only:
            return PermissionDecision.deny("approval_denied", "read_only_block")

        # ``never`` is a hard security deny that overrides everything else.
        if self.approval_policy == "never":
            return PermissionDecision.deny("approval_denied", "approval_denied")

        # Knowledge deposition changes durable user-owned state. Unlike ordinary
        # coding edits, automatic mode must never treat it as implicitly approved.
        if tool.name in {"knowledge_learn", "remember"}:
            return PermissionDecision.allow("approval_required")

        # Mode-specific decisions for risky tools (write_file, patch_file, run_shell).
        if self.permission_mode == "auto":
            if tool.name == "run_shell":
                dangerous, _, _ = check_dangerous_command(str(args.get("command", "")))
                if dangerous:
                    return PermissionDecision.allow("approval_required")
            return PermissionDecision.allow("approval_auto")
        if self.permission_mode == "accept_edits":
            # Auto-approve file edits, but ask for shell commands
            if tool.name in {"write_file", "patch_file"}:
                return PermissionDecision.allow("accept_edits_auto")
            # run_shell and other risky tools still need approval
            if self.approval_callback is None:
                return PermissionDecision.allow("approval_required")
        if self.permission_mode == "default" and self.approval_callback is None:
            return PermissionDecision.allow("approval_required")
        # Legacy fallback for old approval_policy callers (worker subagents)
        if self.approval_policy == "auto":
            return PermissionDecision.allow("approval_auto")
        if self.approval_callback is None:
            return PermissionDecision.allow("approval_required")
        return PermissionDecision.deny("approval_denied", "approval_denied")

    def _check_write_scope(
        self,
        args: dict[str, Any],
        workspace: WorkspaceContext,
    ) -> PermissionDecision:
        try:
            requested = workspace.path(str(args.get("path", "")))
        except ValueError:
            return PermissionDecision.deny("write_scope_mismatch", "write_scope_guard")

        for raw_scope in self.write_scope:
            scope = workspace.path(raw_scope)
            try:
                requested.relative_to(scope)
                return PermissionDecision.allow("write_scope")
            except ValueError:
                continue
        return PermissionDecision.deny("write_scope_mismatch", "write_scope_guard")
