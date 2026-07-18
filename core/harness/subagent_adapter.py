"""Sage application adapter for awaited read-only Harness children."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, cast

from sage_harness import (
    SubagentCancelReason,
    SubagentRequest,
    SubagentResult,
    SubagentTerminalStatus,
)

from core.coding.memory import workspace_id_from_path
from core.coding.multiagent.execution import WorkerTask
from core.coding.multiagent.runtime import (
    WorkerTaskBudgetExceeded,
    WorkerTaskCancelled,
    run_worker_task,
)
from core.coding.runtime import CodingRuntime

logger = logging.getLogger(__name__)
_READ_ONLY_CHILD_TOOLS = frozenset({"list_files", "read_file", "search"})
_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled", "timed_out"})


class CodingSubagentExecutor:
    """Run one child to a terminal state using Sage's existing Engine and RunStore."""

    def __init__(self, runtime: CodingRuntime) -> None:
        self.runtime = runtime
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._cancel_reasons: dict[str, SubagentCancelReason] = {}

    async def execute(self, request: SubagentRequest) -> SubagentResult:
        self._validate(request)
        cached = self._cached_result(request.child_run_id)
        if cached is not None:
            return cached

        cancel_event = asyncio.Event()
        self._cancel_events[request.child_run_id] = cancel_event
        result_ref = self._result_ref(request.child_run_id)
        self.runtime.run_store.start_run(request.child_run_id)
        self.runtime.run_store.append_trace(
            request.child_run_id,
            {
                "type": "subagent_started",
                "run_id": request.child_run_id,
                "parent_run_id": request.parent_run_id,
                "description": request.description,
                "subagent_type": request.subagent_type,
                "status": "running",
            },
        )
        task = WorkerTask(
            id=request.child_run_id,
            description=request.description,
            # The legacy worker runtime still uses the display spelling for
            # plan-mode selection; the public Harness contract is lowercase.
            subagent_type="Explore",
            write_scope=(),
            prompt=request.prompt,
            status="running",
        )

        def emit_child_event(event: dict[str, Any]) -> None:
            self.runtime.run_store.append_trace(
                request.child_run_id,
                {
                    **event,
                    "run_id": request.child_run_id,
                    "parent_run_id": request.parent_run_id,
                },
            )

        try:
            final = await run_worker_task(
                task,
                self.runtime.workspace,
                self.runtime.worker_manager.model_factory,
                tool_scope=request.tool_scope,
                should_stop=lambda: cancel_event.is_set() or self.runtime.stop_requested,
                token_budget=request.token_budget,
                max_steps=request.max_steps,
                event_sink=emit_child_event,
            )
            result = SubagentResult(
                child_run_id=request.child_run_id,
                status="succeeded" if final.strip() else "failed",
                result=final,
                result_ref=result_ref,
                error_code="" if final.strip() else "empty_result",
            )
        except WorkerTaskBudgetExceeded:
            result = SubagentResult(
                child_run_id=request.child_run_id,
                status="failed",
                result_ref=result_ref,
                error_code="token_budget",
            )
        except WorkerTaskCancelled:
            result = self._cancelled_result(request.child_run_id, result_ref)
        except asyncio.CancelledError:
            result = self._cancelled_result(request.child_run_id, result_ref)
            self._append_terminal(
                request,
                result,
            )
            self._cancel_reasons.pop(request.child_run_id, None)
            raise
        except Exception as exc:
            logger.warning("Child execution failed (%s)", type(exc).__name__)
            result = SubagentResult(
                child_run_id=request.child_run_id,
                status="failed",
                result_ref=result_ref,
                error_code="child_execution_failed",
            )
        finally:
            self._cancel_events.pop(request.child_run_id, None)

        self._append_terminal(request, result)
        self._cancel_reasons.pop(request.child_run_id, None)
        return result

    async def cancel(
        self,
        child_run_id: str,
        reason: SubagentCancelReason = "parent_cancelled",
    ) -> None:
        self._cancel_reasons[child_run_id] = reason
        event = self._cancel_events.get(child_run_id)
        if event is not None:
            event.set()

    def _validate(self, request: SubagentRequest) -> None:
        if request.parent_thread_id != self.runtime.session_id:
            raise ValueError("subagent thread does not match runtime")
        if request.parent_run_id != self.runtime.active_run_id:
            raise ValueError("subagent parent run is not active")
        expected_workspace_id = workspace_id_from_path(self.runtime.workspace.root)
        if request.workspace_id != expected_workspace_id:
            raise ValueError("subagent workspace identity does not match runtime")
        if Path(request.workspace_path).resolve() != self.runtime.workspace.root.resolve():
            raise ValueError("subagent workspace path does not match runtime")
        if request.subagent_type.casefold() != "explore":
            raise ValueError("only read-only Explore children are enabled")
        if not set(request.tool_scope).issubset(_READ_ONLY_CHILD_TOOLS):
            raise ValueError("subagent requested tools outside the read-only scope")
        if request.depth != 1:
            raise ValueError("nested subagents are disabled")

    def _cancelled_result(self, child_run_id: str, result_ref: str) -> SubagentResult:
        reason = self._cancel_reasons.get(child_run_id, "parent_cancelled")
        return SubagentResult(
            child_run_id=child_run_id,
            status="timed_out" if reason == "timeout" else "cancelled",
            result_ref=result_ref,
            error_code="timeout" if reason == "timeout" else "parent_cancelled",
        )

    def _result_ref(self, child_run_id: str) -> str:
        return f"subagent://{self.runtime.session_id}/{child_run_id}"

    def _append_terminal(
        self,
        request: SubagentRequest,
        result: SubagentResult,
    ) -> None:
        self.runtime.run_store.append_trace(
            request.child_run_id,
            {
                "type": "subagent_terminal",
                "run_id": request.child_run_id,
                "parent_run_id": request.parent_run_id,
                "status": result.status,
                "result_brief": result.result.strip()[:2000],
                "result_ref": result.result_ref,
                "error_code": result.error_code,
            },
        )
        self.runtime.run_store.append_trace(
            request.child_run_id,
            {
                "type": "run_finished",
                "run_id": request.child_run_id,
                "status": {
                    "succeeded": "completed",
                    "failed": "error",
                    "cancelled": "cancelled",
                    "timed_out": "error",
                }[result.status],
            },
        )

    def _cached_result(self, child_run_id: str) -> SubagentResult | None:
        try:
            run = self.runtime.run_store.get_run(child_run_id)
        except FileNotFoundError:
            return None
        for event in reversed(run["events"]):
            if event.get("type") != "subagent_terminal":
                continue
            status = str(event.get("status") or "")
            if status not in _TERMINAL_STATUSES:
                return None
            return SubagentResult(
                child_run_id=child_run_id,
                status=cast(SubagentTerminalStatus, status),
                result=str(event.get("result_brief") or ""),
                result_ref=str(event.get("result_ref") or self._result_ref(child_run_id)),
                error_code=str(event.get("error_code") or ""),
            )
        return None


__all__ = ["CodingSubagentExecutor"]
