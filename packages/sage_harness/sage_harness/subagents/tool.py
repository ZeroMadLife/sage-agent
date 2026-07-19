"""Awaited task tool that returns a bounded child result to the parent graph."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from contextlib import suppress
from typing import Annotated, Any

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, InjectedToolCallId, tool
from langgraph.types import Command

from sage_harness.config import HarnessRunContext
from sage_harness.state import SageThreadState
from sage_harness.subagents.contracts import (
    SubagentExecutorPort,
    SubagentRequest,
    SubagentResult,
    SubagentToolConfig,
    derive_child_run_id,
)

_DESCRIPTION_MAX = 200
_PROMPT_MAX = 12_000
_RESULT_MAX = 8_000
_RESULT_BRIEF_MAX = 2_000
_PROGRESS_PHASES = frozenset(
    {"model_requested", "tool_started", "tool_completed", "approval_required"}
)
_PROGRESS_STATUSES = frozenset({"running", "waiting", "completed", "error"})
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|token|password|secret)\s*=\s*)"
    r"(?:'[^']*'|\"[^\"]*\"|[^\s;&|]+)"
)


def _result_content(result: SubagentResult) -> str:
    if result.status == "succeeded":
        body = result.result.strip()[:_RESULT_MAX] or "The child completed without a text result."
        evidence = (
            "\nEvidence refs: " + ", ".join(result.evidence_refs) if result.evidence_refs else ""
        )
        mastery = ""
        if result.mastery_evidence:
            items = ", ".join(
                f"{item.kind}:{item.result}:{item.evidence_id}"
                for item in result.mastery_evidence
            )
            mastery = (
                "\nMastery evidence candidates (not applied to mastery): " + items
            )
        return f"Task succeeded. Result:\n{body}{evidence}{mastery}"
    if result.status == "timed_out":
        return "Task timed out before producing a usable result."
    if result.status == "cancelled":
        return "Task was cancelled with its parent run."
    return "Task failed without exposing internal error details."


def _terminal_command(
    *,
    tool_call_id: str,
    request: SubagentRequest,
    result: SubagentResult,
) -> Command[Any]:
    result_brief = result.result.strip()[:_RESULT_BRIEF_MAX]
    entry: dict[str, object] = {
        "id": request.child_run_id,
        "run_id": request.parent_run_id,
        "description": request.description,
        "subagent_type": request.subagent_type,
        "status": result.status,
        "result_ref": result.result_ref,
        "tool_scope": list(request.tool_scope),
        "token_budget": request.token_budget,
        "timeout_seconds": request.timeout_seconds,
        "reserved_tokens": request.token_budget,
        "reserved_model_calls": request.max_steps + 2,
        "reserved_tool_calls": request.max_steps,
        "token_usage": result.token_usage,
        "model_calls": result.model_calls,
        "tool_count": result.tool_count,
    }
    if result_brief:
        entry["result_brief"] = result_brief
    if result.evidence_refs:
        entry["evidence_refs"] = list(result.evidence_refs)
        entry["evidence_count"] = len(result.evidence_refs)
    if result.query_fingerprints:
        entry["query_fingerprints"] = list(result.query_fingerprints)
    if result.source_fingerprints:
        entry["source_fingerprints"] = list(result.source_fingerprints)
    if result.mastery_evidence:
        entry["mastery_evidence"] = [
            _mastery_evidence_payload(item) for item in result.mastery_evidence
        ]
    metadata = {
        "child_run_id": request.child_run_id,
        "parent_run_id": request.parent_run_id,
        "status": result.status,
        "result_ref": result.result_ref,
        "error_code": result.error_code,
        "evidence_refs": list(result.evidence_refs),
        "evidence_count": len(result.evidence_refs),
        "token_usage": result.token_usage,
        "model_calls": result.model_calls,
        "tool_count": result.tool_count,
        "mastery_evidence": [
            _mastery_evidence_payload(item) for item in result.mastery_evidence
        ],
        "mastery_evidence_count": len(result.mastery_evidence),
    }
    return Command(
        update={
            "delegations": [entry],
            "evidence_refs": list(result.evidence_refs),
            "evidence_query_fingerprints": list(result.query_fingerprints),
            "evidence_source_fingerprints": list(result.source_fingerprints),
            "messages": [
                ToolMessage(
                    content=_result_content(result),
                    tool_call_id=tool_call_id,
                    name="task",
                    status="success" if result.status == "succeeded" else "error",
                    additional_kwargs={"sage_subagent": metadata},
                )
            ],
        }
    )


def _progress_event(
    event: Mapping[str, object],
    *,
    child_run_id: str,
    parent_run_id: str,
    subagent_type: str,
) -> dict[str, object]:
    phase = str(event.get("phase", ""))
    status = str(event.get("status", ""))
    if phase == "approval_required":
        return {
            "type": "approval_required",
            "approval_id": _bounded_text(event.get("approval_id"), 256),
            "tool": _bounded_text(event.get("tool"), 128),
            "args": _bounded_arguments(event.get("args")),
            "description": _redact_text(_bounded_text(event.get("description"), 500)),
            "child_run_id": child_run_id,
            "parent_run_id": parent_run_id,
            "subagent_type": subagent_type,
            "approval_scope": "subagent",
            "resume_required": False,
            "operation_ref": {"kind": "coding_run", "id": child_run_id},
        }
    payload: dict[str, object] = {
        "type": "subagent_progress",
        "child_run_id": child_run_id,
        "parent_run_id": parent_run_id,
        "subagent_type": subagent_type,
        "phase": phase if phase in _PROGRESS_PHASES else "model_requested",
        "status": status if status in _PROGRESS_STATUSES else "running",
        "tool_count": _non_negative_int(event.get("tool_count")),
        "evidence_count": _non_negative_int(event.get("evidence_count")),
        "operation_ref": {"kind": "coding_run", "id": child_run_id},
    }
    tool_name = str(event.get("tool", "")).strip()[:128]
    if tool_name:
        payload["tool"] = tool_name
    return payload


def _bounded_text(value: object, maximum: int) -> str:
    return " ".join(str(value or "").split())[:maximum]


def _redact_text(value: str) -> str:
    return _SECRET_ASSIGNMENT.sub(r"\1[REDACTED]", value)


def _bounded_arguments(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    bounded: dict[str, object] = {}
    for key, item in list(value.items())[:20]:
        name = _bounded_text(key, 128)
        if not name:
            continue
        if isinstance(item, bool | int | float):
            bounded[name] = item
        elif isinstance(item, str):
            bounded[name] = _redact_text(item)[:4_000]
        elif isinstance(item, list | tuple):
            bounded[name] = [
                (
                    candidate
                    if isinstance(candidate, bool | int | float)
                    else _redact_text(str(candidate))[:500]
                )
                for candidate in item[:20]
            ]
    return bounded


def _mastery_evidence_payload(item: object) -> dict[str, object]:
    return {
        "evidence_id": str(getattr(item, "evidence_id", "")),
        "kind": str(getattr(item, "kind", "")),
        "result": str(getattr(item, "result", "")),
        "source_ref": str(getattr(item, "source_ref", "")),
        "summary": str(getattr(item, "summary", "")),
        "metadata": dict(getattr(item, "metadata", {})),
    }


def _non_negative_int(value: object) -> int:
    return value if type(value) is int and value >= 0 else 0


def _reserved_token_budget(
    state: Mapping[str, object],
    child_run_id: str,
    fallback: int,
) -> int:
    """Use middleware's durable reservation, never a model-authored budget."""
    delegations = state.get("delegations")
    if isinstance(delegations, list):
        for entry in delegations:
            if isinstance(entry, Mapping) and entry.get("id") == child_run_id:
                reserved = _non_negative_int(entry.get("reserved_tokens"))
                if reserved:
                    return min(fallback, reserved)
    return fallback


def _reserved_max_steps(
    state: Mapping[str, object],
    child_run_id: str,
    fallback: int,
) -> int:
    """Project parent model/tool reservations into the child's step limit."""
    delegations = state.get("delegations")
    if isinstance(delegations, list):
        for entry in delegations:
            if not isinstance(entry, Mapping) or entry.get("id") != child_run_id:
                continue
            model_calls = _non_negative_int(entry.get("reserved_model_calls"))
            tool_calls = _non_negative_int(entry.get("reserved_tool_calls"))
            if model_calls >= 3 and tool_calls >= 1:
                return min(fallback, model_calls - 2, tool_calls)
    return fallback


def _state_strings(state: Mapping[str, object], key: str) -> tuple[str, ...]:
    values = state.get(key)
    if not isinstance(values, list | tuple):
        return ()
    return tuple(
        dict.fromkeys(
            str(item).strip() for item in values if isinstance(item, str) and item.strip()
        )
    )


def _evidence_child_run_ids(
    state: Mapping[str, object],
    parent_run_id: str,
) -> tuple[str, ...]:
    delegations = state.get("delegations")
    if not isinstance(delegations, list):
        return ()
    child_ids: list[str] = []
    for entry in delegations:
        if (
            not isinstance(entry, Mapping)
            or entry.get("run_id") != parent_run_id
            or entry.get("status") != "succeeded"
            or entry.get("subagent_type") != "research"
            or not entry.get("evidence_refs")
        ):
            continue
        child_id = str(entry.get("id", "")).strip()
        if child_id:
            child_ids.append(child_id)
    return tuple(dict.fromkeys(child_ids))


def build_task_tool(
    executor: SubagentExecutorPort,
    config: SubagentToolConfig | None = None,
) -> BaseTool:
    """Build the only parent-facing child delegation tool."""
    effective = config or SubagentToolConfig()

    @tool("task")
    async def task_tool(
        description: str,
        prompt: str,
        subagent_type: str,
        runtime: ToolRuntime[HarnessRunContext, SageThreadState],
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> Command[Any]:
        """Delegate a bounded task and wait for its terminal result.

        Choose only a server-registered profile: explore for bounded local
        inspection, research for Knowledge and public web evidence, practice for
        bounded workspace exercises, or synthesize for an already-authorized
        Research evidence bundle. Practice may write or run shell only through
        the parent runtime's policy, approval, and sandbox. No child may persist
        Memory/Knowledge, launch another child, or expand server-owned tools and
        budgets.
        """
        description = " ".join(str(description).split())[:_DESCRIPTION_MAX]
        prompt = str(prompt).strip()[:_PROMPT_MAX]
        subagent_type = str(subagent_type).strip().casefold()
        child_run_id = derive_child_run_id(
            runtime.context.thread_id,
            runtime.context.run_id,
            tool_call_id,
        )
        profile = effective.resolve(subagent_type)
        request = SubagentRequest(
            parent_thread_id=runtime.context.thread_id,
            parent_run_id=runtime.context.run_id,
            child_run_id=child_run_id,
            description=description or "Explore task",
            prompt=prompt,
            subagent_type=subagent_type,
            workspace_id=runtime.context.workspace_id,
            workspace_path=runtime.context.workspace_path,
            tool_scope=profile.tool_scope if profile is not None else effective.tool_scope,
            token_budget=_reserved_token_budget(
                runtime.state,
                child_run_id,
                profile.token_budget if profile is not None else effective.token_budget,
            ),
            timeout_seconds=(
                profile.timeout_seconds if profile is not None else effective.timeout_seconds
            ),
            max_steps=_reserved_max_steps(
                runtime.state,
                child_run_id,
                profile.max_steps if profile is not None else effective.max_steps,
            ),
            evidence_refs=_state_strings(runtime.state, "evidence_refs"),
            evidence_child_run_ids=_evidence_child_run_ids(
                runtime.state,
                runtime.context.run_id,
            ),
            query_fingerprints=_state_strings(
                runtime.state,
                "evidence_query_fingerprints",
            ),
            source_fingerprints=_state_strings(
                runtime.state,
                "evidence_source_fingerprints",
            ),
        )
        writer = runtime.stream_writer
        writer(
            {
                "type": "subagent_started",
                "child_run_id": child_run_id,
                "parent_run_id": request.parent_run_id,
                "description": request.description,
                "subagent_type": request.subagent_type,
                "tool_scope": list(request.tool_scope),
                "reserved_tokens": request.token_budget,
                "operation_ref": {"kind": "coding_run", "id": child_run_id},
            }
        )
        if profile is None:
            result = SubagentResult(
                child_run_id=child_run_id,
                status="failed",
                error_code="subagent_type_not_allowed",
            )
        else:
            try:
                execution: asyncio.Future[SubagentResult] = asyncio.ensure_future(
                    executor.execute(
                        request,
                        lambda event: writer(
                            _progress_event(
                                event,
                                child_run_id=child_run_id,
                                parent_run_id=request.parent_run_id,
                                subagent_type=request.subagent_type,
                            )
                        ),
                    )
                )
                result = await asyncio.wait_for(
                    asyncio.shield(execution),
                    timeout=request.timeout_seconds,
                )
            except TimeoutError:
                await executor.cancel(child_run_id, "timeout")
                execution.cancel()
                with suppress(asyncio.CancelledError):
                    await execution
                result = SubagentResult(
                    child_run_id=child_run_id,
                    status="timed_out",
                    error_code="timeout",
                    token_usage=request.token_budget,
                    model_calls=request.max_steps + 2,
                    tool_count=request.max_steps,
                )
            except asyncio.CancelledError:
                await executor.cancel(child_run_id, "parent_cancelled")
                if "execution" in locals():
                    execution.cancel()
                    with suppress(asyncio.CancelledError):
                        await execution
                writer(
                    {
                        "type": "subagent_cancelled",
                        "child_run_id": child_run_id,
                        "parent_run_id": request.parent_run_id,
                    }
                )
                raise
            except Exception:
                result = SubagentResult(
                    child_run_id=child_run_id,
                    status="failed",
                    error_code="executor_failed",
                )
        event_type = {
            "succeeded": "subagent_completed",
            "failed": "subagent_failed",
            "cancelled": "subagent_cancelled",
            "timed_out": "subagent_timed_out",
        }[result.status]
        writer(
            {
                "type": event_type,
                "child_run_id": child_run_id,
                "parent_run_id": request.parent_run_id,
                "status": result.status,
                "result_brief": result.result.strip()[:500],
                "result_ref": result.result_ref,
                "error_code": result.error_code,
                "evidence_count": len(result.evidence_refs),
                "evidence_refs": list(result.evidence_refs),
                "token_usage": result.token_usage,
                "model_calls": result.model_calls,
                "tool_count": result.tool_count,
                "mastery_evidence": [
                    _mastery_evidence_payload(item) for item in result.mastery_evidence
                ],
                "mastery_evidence_count": len(result.mastery_evidence),
                "operation_ref": {"kind": "coding_run", "id": child_run_id},
            }
        )
        return _terminal_command(
            tool_call_id=tool_call_id,
            request=request,
            result=result,
        )

    return task_tool


__all__ = ["build_task_tool"]
