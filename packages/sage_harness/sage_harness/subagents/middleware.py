"""Delegation ledger and server-side task-call limits."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, override

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from sage_harness.config import HarnessRunContext
from sage_harness.state import SageThreadState
from sage_harness.subagents.contracts import SubagentLimits, derive_child_run_id

_DESCRIPTION_MAX = 200


def _tool_call_name(call: Mapping[str, Any]) -> str:
    name = call.get("name")
    if isinstance(name, str):
        return name
    function = call.get("function")
    return str(function.get("name") or "") if isinstance(function, Mapping) else ""


def _tool_call_args(call: Mapping[str, Any]) -> Mapping[str, Any]:
    args = call.get("args")
    return args if isinstance(args, Mapping) else {}


def _append_limit_notice(content: object) -> object:
    notice = (
        "[SUBAGENT LIMIT REACHED] Reuse completed child results or finish the "
        "remaining work directly; do not launch another child in this run."
    )
    if isinstance(content, str):
        return f"{content}\n\n{notice}".strip()
    if isinstance(content, list):
        return [*content, {"type": "text", "text": notice}]
    return notice


class SubagentLifecycleMiddleware(AgentMiddleware[SageThreadState, HarnessRunContext]):
    """Record task calls, cap delegation volume, and close stale running entries."""

    state_schema = SageThreadState

    def __init__(self, limits: SubagentLimits | None = None) -> None:
        super().__init__()
        self.limits = limits or SubagentLimits()

    def _close_stale(
        self,
        state: SageThreadState,
        runtime: Runtime[HarnessRunContext],
    ) -> dict[str, object] | None:
        current_run_id = runtime.context.run_id
        stale = [
            {**entry, "status": "cancelled", "result_brief": "Parent run ended before the child returned."}
            for entry in state.get("delegations", []) or []
            if entry.get("status") == "running" and entry.get("run_id") != current_run_id
        ]
        return {"delegations": stale} if stale else None

    @override
    def before_agent(
        self,
        state: SageThreadState,
        runtime: Runtime[HarnessRunContext],
    ) -> dict[str, object] | None:
        return self._close_stale(state, runtime)

    @override
    async def abefore_agent(
        self,
        state: SageThreadState,
        runtime: Runtime[HarnessRunContext],
    ) -> dict[str, object] | None:
        return self._close_stale(state, runtime)

    def _after_model(
        self,
        state: SageThreadState,
        runtime: Runtime[HarnessRunContext],
    ) -> dict[str, object] | None:
        messages = state.get("messages", [])
        if not messages or not isinstance(messages[-1], AIMessage):
            return None
        message = messages[-1]
        calls = list(message.tool_calls or [])
        if not any(_tool_call_name(call) == "task" for call in calls):
            return None

        current_run_id = runtime.context.run_id
        prior = [
            entry
            for entry in state.get("delegations", []) or []
            if entry.get("run_id") == current_run_id
        ]
        prior_ids = {str(entry.get("id")) for entry in prior if entry.get("id")}
        remaining_total = max(0, self.limits.max_total_per_run - len(prior_ids))
        kept: list[Any] = []
        entries: list[dict[str, object]] = []
        kept_task_count = 0
        dropped = 0

        for call in calls:
            if _tool_call_name(call) != "task":
                kept.append(call)
                continue
            call_id = str(call.get("id") or "")
            known = call_id in prior_ids
            allowed = (
                bool(call_id)
                and kept_task_count < self.limits.max_concurrent
                and (known or remaining_total > 0)
            )
            if not allowed:
                dropped += 1
                continue
            kept.append(call)
            kept_task_count += 1
            if known:
                continue
            remaining_total -= 1
            args = _tool_call_args(call)
            entries.append(
                {
                    "id": derive_child_run_id(
                        runtime.context.thread_id,
                        current_run_id,
                        call_id,
                    ),
                    "run_id": current_run_id,
                    "description": " ".join(str(args.get("description") or args.get("prompt") or "").split())[:_DESCRIPTION_MAX],
                    "subagent_type": str(args.get("subagent_type") or ""),
                    "status": "running",
                }
            )

        update: dict[str, object] = {}
        if entries:
            update["delegations"] = entries
        if dropped:
            update["messages"] = [
                message.model_copy(
                    update={
                        "content": _append_limit_notice(message.content),
                        "tool_calls": kept,
                    }
                )
            ]
        return update or None

    @override
    def after_model(
        self,
        state: SageThreadState,
        runtime: Runtime[HarnessRunContext],
    ) -> dict[str, object] | None:
        return self._after_model(state, runtime)

    @override
    async def aafter_model(
        self,
        state: SageThreadState,
        runtime: Runtime[HarnessRunContext],
    ) -> dict[str, object] | None:
        return self._after_model(state, runtime)


__all__ = ["SubagentLifecycleMiddleware"]
