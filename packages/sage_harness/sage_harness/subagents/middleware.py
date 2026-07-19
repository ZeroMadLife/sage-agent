"""Delegation ledger and server-side task-call limits."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, override

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from sage_harness.config import HarnessRunContext
from sage_harness.state import SageThreadState, delegation_budget_usage
from sage_harness.subagents.contracts import (
    SubagentLimits,
    SubagentToolConfig,
    derive_child_run_id,
)

_DESCRIPTION_MAX = 200
_DEFAULT_CHILD_TOKEN_BUDGET = 24_000
_MIN_CHILD_RESERVATION = 256


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


def _non_negative_int(value: object) -> int:
    return value if type(value) is int and value >= 0 else 0


def _child_usage(state: SageThreadState, run_id: str) -> tuple[int, int, int]:
    """Return durable child token, model, and tool usage for one parent run."""
    token_usage = 0
    model_calls = 0
    tool_count = 0
    for entry in state.get("delegations", []) or []:
        if entry.get("run_id") != run_id:
            continue
        entry_tokens, entry_models, entry_tools = delegation_budget_usage(entry)
        token_usage += entry_tokens
        model_calls += entry_models
        tool_count += entry_tools
    return token_usage, model_calls, tool_count


def _needs_reservation(entry: Mapping[str, object]) -> bool:
    return str(entry.get("status") or "") in {"pending", "running"} and (
        _non_negative_int(entry.get("reserved_tokens")) < _MIN_CHILD_RESERVATION
        or _non_negative_int(entry.get("reserved_model_calls")) < 3
        or _non_negative_int(entry.get("reserved_tool_calls")) < 1
    )


class SubagentLifecycleMiddleware(AgentMiddleware[SageThreadState, HarnessRunContext]):
    """Record task calls, cap delegation volume, and close stale running entries."""

    state_schema = SageThreadState

    def __init__(
        self,
        limits: SubagentLimits | None = None,
        tool_config: SubagentToolConfig | None = None,
    ) -> None:
        super().__init__()
        self.limits = limits or SubagentLimits()
        self.tool_config = tool_config or SubagentToolConfig()

    def _close_stale(
        self,
        state: SageThreadState,
        runtime: Runtime[HarnessRunContext],
    ) -> dict[str, object] | None:
        current_run_id = runtime.context.run_id
        stale = [
            {
                **entry,
                "status": "cancelled",
                "result_brief": "Parent run ended before the child returned.",
            }
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
        prior_by_id = {str(entry["id"]): entry for entry in prior if entry.get("id")}
        prior_ids = set(prior_by_id)
        remaining_total = max(0, self.limits.max_total_per_run - len(prior_ids))
        parent_token_usage = _non_negative_int(state.get("run_token_usage"))
        parent_token_limit = _non_negative_int(state.get("run_token_limit"))
        parent_model_usage = _non_negative_int(state.get("run_model_calls"))
        parent_model_limit = _non_negative_int(state.get("run_model_call_limit"))
        parent_tool_usage = _non_negative_int(state.get("run_tool_calls"))
        parent_tool_limit = _non_negative_int(state.get("run_tool_call_limit"))
        child_token_usage, child_model_calls, child_tool_calls = _child_usage(
            state,
            current_run_id,
        )
        remaining_tokens = max(
            0,
            parent_token_limit - parent_token_usage - child_token_usage,
        )
        remaining_model_calls = max(
            0,
            parent_model_limit - parent_model_usage - child_model_calls - 1,
        )
        remaining_tool_calls = max(
            0,
            parent_tool_limit - parent_tool_usage - child_tool_calls,
        )
        reservation_candidate_ids: set[str] = set()
        for call in calls:
            if _tool_call_name(call) != "task":
                continue
            call_id = str(call.get("id") or "")
            if not call_id:
                continue
            child_run_id = derive_child_run_id(
                runtime.context.thread_id,
                current_run_id,
                call_id,
            )
            if child_run_id not in prior_ids or _needs_reservation(
                prior_by_id[child_run_id]
            ):
                reservation_candidate_ids.add(child_run_id)
        reservation_slots = min(
            self.limits.max_concurrent,
            remaining_total,
            len(reservation_candidate_ids),
        )
        if parent_token_limit > 0:
            reservation_slots = min(
                reservation_slots,
                remaining_tokens // _MIN_CHILD_RESERVATION,
            )
        if parent_model_limit > 0 and parent_tool_limit > 0:
            reservation_slots = min(
                reservation_slots,
                remaining_model_calls // 3,
                remaining_tool_calls,
            )
        kept: list[Any] = []
        entries: list[dict[str, object]] = []
        kept_task_count = 0
        dropped = 0
        reserved_count = 0
        seen_batch_ids: set[str] = set()

        for call in calls:
            if _tool_call_name(call) != "task":
                kept.append(call)
                continue
            call_id = str(call.get("id") or "")
            child_run_id = derive_child_run_id(
                runtime.context.thread_id,
                current_run_id,
                call_id,
            )
            known = child_run_id in prior_ids
            if child_run_id in seen_batch_ids:
                dropped += 1
                continue
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
            seen_batch_ids.add(child_run_id)
            previous = prior_by_id.get(child_run_id)
            if known and previous is not None and not _needs_reservation(previous):
                continue
            if not known:
                remaining_total -= 1
            args = _tool_call_args(call)
            requested_profile = self.tool_config.resolve(str(args.get("subagent_type") or ""))
            if parent_token_limit > 0:
                reserved_count += 1
                remaining_slots = max(1, reservation_slots - reserved_count + 1)
                profile_token_budget = (
                    requested_profile.token_budget
                    if requested_profile is not None
                    else _DEFAULT_CHILD_TOKEN_BUDGET
                )
                reserved_tokens = min(
                    profile_token_budget,
                    remaining_tokens // remaining_slots,
                )
                if reserved_tokens < _MIN_CHILD_RESERVATION:
                    kept.pop()
                    kept_task_count -= 1
                    dropped += 1
                    if not known:
                        remaining_total += 1
                    continue
                remaining_tokens -= reserved_tokens
            else:
                reserved_tokens = 0
            profile_max_steps = (
                requested_profile.max_steps
                if requested_profile is not None
                else self.tool_config.max_steps
            )
            if parent_model_limit > 0 and parent_tool_limit > 0:
                remaining_slots = max(1, reservation_slots - reserved_count + 1)
                available_model_calls = remaining_model_calls // remaining_slots
                available_tool_calls = remaining_tool_calls // remaining_slots
                reserved_steps = min(
                    profile_max_steps,
                    available_tool_calls,
                    max(0, available_model_calls - 2),
                )
                if reserved_steps < 1:
                    kept.pop()
                    kept_task_count -= 1
                    dropped += 1
                    if not known:
                        remaining_total += 1
                    remaining_tokens += reserved_tokens
                    continue
                reserved_model_calls = reserved_steps + 2
                reserved_tool_calls = reserved_steps
                remaining_model_calls -= reserved_model_calls
                remaining_tool_calls -= reserved_tool_calls
            else:
                reserved_model_calls = 0
                reserved_tool_calls = 0
            entries.append(
                {
                    **(previous or {}),
                    "id": child_run_id,
                    "run_id": current_run_id,
                    "description": " ".join(
                        str(args.get("description") or args.get("prompt") or "").split()
                    )[:_DESCRIPTION_MAX],
                    "subagent_type": str(args.get("subagent_type") or ""),
                    "status": "running",
                    "reserved_tokens": reserved_tokens,
                    "reserved_model_calls": reserved_model_calls,
                    "reserved_tool_calls": reserved_tool_calls,
                    "token_usage": 0,
                    "model_calls": 0,
                    "tool_count": 0,
                    "evidence_refs": [],
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
