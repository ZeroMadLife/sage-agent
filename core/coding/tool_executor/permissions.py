"""Permission gates for coding tool execution."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from core.coding.context import WorkspaceContext
from core.coding.tools.base import RegisteredTool

ApprovalPolicy = Literal["auto", "ask", "never"]
PermissionMode = Literal["default", "accept_edits", "auto", "plan"]
ApprovalCallback = Callable[[str, dict[str, Any]], bool]

# Sentinel for "no explicit permission mode set": fall back to the legacy
# ``approval_policy`` behavior so existing callers stay backward compatible.
# The runtime always passes an explicit mode, opting into the new semantics.
_PERMISSION_MODE_INHERIT = "_inherit_policy"


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
        approval_policy: ApprovalPolicy = "auto",
        write_scope: Sequence[str] | None = None,
        plan_mode: bool = False,
        read_only: bool = False,
        approval_callback: ApprovalCallback | None = None,
        permission_mode: str = _PERMISSION_MODE_INHERIT,
    ) -> None:
        self.approval_policy = approval_policy
        self.write_scope = tuple(write_scope or ())
        self.plan_mode = plan_mode
        self.read_only = read_only
        self.approval_callback = approval_callback
        self.permission_mode = permission_mode

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
        # ``never`` is an explicit hard security deny and overrides any
        # auto-approving permission mode for backward compatibility.
        if self.approval_policy == "never":
            return PermissionDecision.deny("approval_denied", "approval_denied")

        # Mode-specific decisions for risky tools (write_file, patch_file, run_shell).
        # Only applied when an explicit permission mode was set; otherwise the
        # legacy ``approval_policy`` controls the decision via the fallback below.
        if self.permission_mode == "auto":
            return PermissionDecision.allow("approval_auto")
        if self.permission_mode == "accept_edits":
            if tool.name in {"write_file", "patch_file"}:
                return PermissionDecision.allow("accept_edits_auto")
            if self.approval_callback is None:
                return PermissionDecision.allow("approval_required")
        if self.permission_mode == "default":
            if self.approval_callback is None:
                return PermissionDecision.allow("approval_required")
        # Fallback: old approval_policy for backward compat
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
