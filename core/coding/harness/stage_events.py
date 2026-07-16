"""Project runtime events into durable, replayable Harness stage facts."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Mapping
from typing import Any

from core.coding.run_coordinator import RunEvent

CODING_HARNESS_DEFINITION_ID = "sage.coding.practice"
CODING_HARNESS_DEFINITION_VERSION = 1


class CodingHarnessStageProjector:
    """Stateful adapter from one Coding run stream to explicit Harness events."""

    def __init__(self, run_id: str, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.run_id = run_id
        self._clock = clock
        self._active_stage: str | None = None
        self._active_detail = ""
        self._started = False
        self._finished = False
        self._retrieval_decided = False
        self._retrieval_started_at: float | None = None

    def start(self) -> tuple[RunEvent, ...]:
        if self._started:
            return ()
        self._started = True
        events = [
            self._stage("stage_started", "receive", status="running"),
            self._stage("stage_completed", "receive"),
            self._transition("receive", "context"),
            self._start_stage("context"),
        ]
        return tuple(events)

    def before(self, event: Mapping[str, Any]) -> tuple[RunEvent, ...]:
        event_type = str(event.get("type", ""))
        if event_type == "model_requested":
            return self._enter_plan()
        if event_type == "text_delta":
            return self._enter_reply()
        if event_type == "model_parsed" and str(event.get("kind", "")) not in {
            "tool",
            "tools",
            "retry",
        }:
            return self._enter_reply()
        if event_type == "tool_call":
            return self._start_tool(event)
        if event_type == "approval_required":
            return self._start_tool(event, status="blocked")
        if event_type == "approval_granted" and self._active_stage == "act":
            return (
                self._start_stage(
                    self._active_stage,
                    status="running",
                    detail=self._active_detail,
                ),
            )
        return ()

    def after(self, event: Mapping[str, Any]) -> tuple[RunEvent, ...]:
        event_type = str(event.get("type", ""))
        if event_type == "tool_result":
            return self._finish_tool(event)
        if event_type in {"final", "step_limit"}:
            output = [*self._enter_reply()]
            output.extend(self._settle_active("completed"))
            return tuple(output)
        if event_type == "memory_proposal_ready":
            output = [*self._settle_active("completed")]
            output.append(self._transition("reply", "memory"))
            output.append(self._start_stage("memory"))
            output.extend(self._settle_active("completed"))
            return tuple(output)
        if event_type == "cancelled":
            return self._settle_active("cancelled")
        if event_type == "error":
            return self._settle_active("error")
        return ()

    def finish(self, status: str) -> tuple[RunEvent, ...]:
        if self._finished:
            return ()
        self._finished = True
        output: list[RunEvent] = []
        if not self._retrieval_decided:
            output.append(self._retrieval_decision("skip"))
            self._retrieval_decided = True
        if self._active_stage is not None:
            terminal = "completed" if status == "completed" else (
                "cancelled" if status in {"cancelled", "interrupted"} else "error"
            )
            output.extend(self._settle_active(terminal))
        return tuple(output)

    def _enter_plan(self) -> tuple[RunEvent, ...]:
        if self._active_stage == "plan":
            return ()
        output: list[RunEvent] = []
        previous = self._active_stage
        if previous is not None:
            output.extend(self._settle_active("completed"))
            output.append(self._transition(previous, "plan"))
        output.append(self._start_stage("plan"))
        return tuple(output)

    def _enter_reply(self) -> tuple[RunEvent, ...]:
        if self._active_stage == "reply":
            return ()
        output: list[RunEvent] = []
        previous = self._active_stage
        if previous is not None:
            output.extend(self._settle_active("completed"))
            output.append(self._transition(previous, "reply"))
        output.append(self._start_stage("reply"))
        return tuple(output)

    def _start_tool(
        self,
        event: Mapping[str, Any],
        *,
        status: str = "running",
    ) -> tuple[RunEvent, ...]:
        tool = str(event.get("tool", ""))
        stage = "act"
        detail = _tool_detail(tool, _record(event.get("args")))
        output: list[RunEvent] = []
        previous = self._active_stage
        if previous == stage:
            output.append(
                self._start_stage(
                    stage,
                    status=status,
                    detail=detail or self._active_detail,
                )
            )
        else:
            if previous is not None:
                output.extend(self._settle_active("completed"))
                output.append(self._transition(previous, stage))
            output.append(self._start_stage(stage, status=status, detail=detail))
        if tool == "knowledge_search" and str(event.get("type", "")) == "tool_call":
            self._retrieval_started_at = self._clock()
        if tool == "knowledge_search" and not self._retrieval_decided:
            output.append(self._retrieval_decision("retrieve"))
            self._retrieval_decided = True
        return tuple(output)

    def _finish_tool(self, event: Mapping[str, Any]) -> tuple[RunEvent, ...]:
        tool = str(event.get("tool", ""))
        stage = "act"
        output: list[RunEvent] = []
        if self._active_stage != stage:
            output.append(self._start_stage(stage, detail=_tool_detail(tool, _record(event.get("args")))))
        output.extend(self._settle_active("error" if event.get("is_error") is True else "completed"))
        if tool == "knowledge_search":
            output.append(self._retrieval_completed(event))
            self._retrieval_started_at = None
        output.append(self._transition(stage, "plan"))
        output.append(self._start_stage("plan"))
        return tuple(output)

    def _settle_active(self, status: str) -> tuple[RunEvent, ...]:
        if self._active_stage is None:
            return ()
        stage = self._active_stage
        self._active_stage = None
        self._active_detail = ""
        if status == "completed":
            return (self._stage("stage_completed", stage),)
        return (
            self._stage(
                "stage_failed",
                stage,
                status="cancelled" if status == "cancelled" else "error",
            ),
        )

    def _start_stage(
        self,
        stage: str,
        *,
        status: str = "running",
        detail: str = "",
    ) -> RunEvent:
        self._active_stage = stage
        self._active_detail = detail
        return self._stage(
            "stage_started",
            stage,
            status=status,
            detail=detail,
            operation_ref=(
                {"kind": "coding_run", "id": self.run_id}
                if stage == "act"
                else None
            ),
        )

    def _stage(
        self,
        event_type: str,
        stage: str,
        *,
        status: str = "completed",
        detail: str = "",
        operation_ref: dict[str, str] | None = None,
    ) -> RunEvent:
        payload: dict[str, Any] = {
            "type": event_type,
            "definition_id": CODING_HARNESS_DEFINITION_ID,
            "definition_version": CODING_HARNESS_DEFINITION_VERSION,
            "stage_id": stage,
        }
        if detail:
            payload["detail"] = detail
        if operation_ref is not None:
            payload["operation_ref"] = operation_ref
        return RunEvent(kind="harness", status=status, payload=payload)

    def _transition(self, source: str, target: str) -> RunEvent:
        return RunEvent(
            kind="harness",
            status="completed",
            payload={
                "type": "transition_taken",
                "definition_id": CODING_HARNESS_DEFINITION_ID,
                "definition_version": CODING_HARNESS_DEFINITION_VERSION,
                "from_stage_id": source,
                "to_stage_id": target,
            },
        )

    def _retrieval_decision(self, decision: str) -> RunEvent:
        return RunEvent(
            kind="harness",
            status="completed",
            payload={
                "type": "retrieval_decision",
                "definition_id": CODING_HARNESS_DEFINITION_ID,
                "definition_version": CODING_HARNESS_DEFINITION_VERSION,
                "decision": decision,
                "gate": "model_tool_selection",
            },
        )

    def _retrieval_completed(self, event: Mapping[str, Any]) -> RunEvent:
        result = _retrieval_metrics(str(event.get("content", "")))
        started_at = self._retrieval_started_at
        duration_ms = 0 if started_at is None else max(0, round((self._clock() - started_at) * 1000))
        payload = {
            "type": "retrieval_completed",
            "definition_id": CODING_HARNESS_DEFINITION_ID,
            "definition_version": CODING_HARNESS_DEFINITION_VERSION,
            **result,
            "duration_ms": duration_ms,
        }
        return RunEvent(
            kind="harness",
            status="error" if event.get("is_error") is True else "completed",
            payload=payload,
        )


def _record(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _tool_detail(tool: str, args: Mapping[str, Any]) -> str:
    candidate = ""
    for key in ("command", "path", "query", "topic"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            candidate = value.strip()
            break
    detail = " · ".join(item for item in (tool, _redact(candidate)) if item)
    return detail[:240]


def _redact(value: str) -> str:
    redacted = re.sub(
        r"(?i)(authorization\s*:\s*)(?:bearer\s+|basic\s+)?[^\s'\"\n]+",
        r"\1[REDACTED]",
        value,
    )
    redacted = re.sub(
        r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+",
        "Bearer [REDACTED]",
        redacted,
    )
    redacted = re.sub(
        r"(?i)\b([A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD))\s*=\s*[^\s]+",
        r"\1=[REDACTED]",
        redacted,
    )
    return redacted.replace("\n", " ")


def _retrieval_metrics(content: str) -> dict[str, Any]:
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    citations = payload.get("citations")
    citation_count = len(citations) if isinstance(citations, list) else 0
    return {
        "status": str(payload.get("status") or "invalid_result"),
        "citation_count": citation_count,
        "used_tokens": _non_negative_int(payload.get("used_tokens")),
        "token_budget": _non_negative_int(payload.get("token_budget")),
    }


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)
