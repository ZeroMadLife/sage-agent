"""Awaited task tool that returns a bounded child result to the parent graph."""

from __future__ import annotations

import asyncio
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


def _result_content(result: SubagentResult) -> str:
    if result.status == "succeeded":
        body = result.result.strip()[:_RESULT_MAX] or "The child completed without a text result."
        return f"Task succeeded. Result:\n{body}"
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
    }
    if result_brief:
        entry["result_brief"] = result_brief
    metadata = {
        "child_run_id": request.child_run_id,
        "parent_run_id": request.parent_run_id,
        "status": result.status,
        "result_ref": result.result_ref,
        "error_code": result.error_code,
    }
    return Command(
        update={
            "delegations": [entry],
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

        Use this only for multi-step read-only exploration that benefits from an
        isolated context. The child cannot write files, call Memory/Knowledge,
        launch another child, or expand its own tool scope.
        """
        description = " ".join(str(description).split())[:_DESCRIPTION_MAX]
        prompt = str(prompt).strip()[:_PROMPT_MAX]
        subagent_type = str(subagent_type).strip().casefold()
        child_run_id = derive_child_run_id(
            runtime.context.thread_id,
            runtime.context.run_id,
            tool_call_id,
        )
        request = SubagentRequest(
            parent_thread_id=runtime.context.thread_id,
            parent_run_id=runtime.context.run_id,
            child_run_id=child_run_id,
            description=description or "Explore task",
            prompt=prompt,
            subagent_type=subagent_type,
            workspace_id=runtime.context.workspace_id,
            workspace_path=runtime.context.workspace_path,
            tool_scope=effective.tool_scope,
            token_budget=effective.token_budget,
            timeout_seconds=effective.timeout_seconds,
            max_steps=effective.max_steps,
        )
        writer = runtime.stream_writer
        writer(
            {
                "type": "subagent_started",
                "child_run_id": child_run_id,
                "parent_run_id": request.parent_run_id,
                "description": request.description,
                "subagent_type": request.subagent_type,
                "operation_ref": {"kind": "coding_run", "id": child_run_id},
            }
        )
        if subagent_type not in effective.allowed_types:
            result = SubagentResult(
                child_run_id=child_run_id,
                status="failed",
                error_code="subagent_type_not_allowed",
            )
        else:
            try:
                execution: asyncio.Future[SubagentResult] = asyncio.ensure_future(
                    executor.execute(request)
                )
                result = await asyncio.wait_for(
                    asyncio.shield(execution),
                    timeout=effective.timeout_seconds,
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
