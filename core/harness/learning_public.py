"""Learning 运行态面向浏览器的单一公开投影。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class LearningPublicProjector:
    """只公开身份、状态与稳定原因码，不公开模型或工具正文。"""

    @staticmethod
    def model_output_receipt(*, task_id: str, run_id: str) -> dict[str, Any]:
        return {
            "type": "learning_model_output",
            "task_id": _string(task_id, 256),
            "run_id": _string(run_id, 256),
            "status": "completed",
            "reason_code": "model_output_withheld",
        }

    @staticmethod
    def user_turn_receipt(*, task_id: str, run_id: str) -> dict[str, Any]:
        return {
            "type": "learning_user_turn",
            "task_id": _string(task_id, 256),
            "run_id": _string(run_id, 256),
            "status": "completed",
            "reason_code": "user_content_withheld",
        }

    @staticmethod
    def mcp_blocked_receipt(*, task_id: str, run_id: str) -> dict[str, Any]:
        return {
            "type": "learning_mcp_blocked",
            "task_id": _string(task_id, 256),
            "run_id": _string(run_id, 256),
            "status": "blocked",
            "reason_code": "learning_scope_mcp_forbidden",
            "server_count": 0,
            "tool_count": 0,
        }

    @staticmethod
    def scope_rejection(
        *,
        run_id: str,
        reason_code: str,
        task_id: str = "",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": "learning_scope_rejected",
            "version": 1,
            "run_id": _string(run_id, 256),
            "status": "denied",
            "reason_code": _string(reason_code, 128),
        }
        if task_id.strip():
            payload["task_id"] = _string(task_id, 256)
        return payload

    @staticmethod
    def terminal_failure(
        *,
        reason_code: str,
        runtime_profile: str = "deerflow_v2",
    ) -> dict[str, Any]:
        return {
            "event": "run_error",
            "runtime_profile": _string(runtime_profile, 128),
            "error_type": "learning_scope_conflict",
            "reason_code": _string(reason_code, 128),
        }

    @staticmethod
    def event(payload: Mapping[str, Any], *, task_id: str) -> dict[str, Any]:
        public: dict[str, Any] = {
            "type": _string(payload.get("type") or "custom", 128),
            "task_id": _string(task_id, 256),
        }
        for key in (
            "run_id",
            "parent_run_id",
            "child_run_id",
            "agent_run_id",
            "tool_call_id",
            "capability_id",
            "approval_id",
            "interrupt_id",
        ):
            value = _string(payload.get(key), 256)
            if value:
                public[key] = value
        status = _string(payload.get("status"), 64)
        public["status"] = status or ("error" if payload.get("is_error") is True else "completed")
        reason = _string(payload.get("reason_code") or payload.get("error_code"), 128)
        if reason:
            public["reason_code"] = reason
        return public

    @classmethod
    def workspace_diff(
        cls,
        run_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        del cls
        changed_files = payload.get("changed_files")
        return {
            "type": "workspace_diff_ready",
            "run_id": _string(run_id, 256),
            "status": "completed",
            "changed_file_count": len(changed_files) if isinstance(changed_files, list) else 0,
        }

    @classmethod
    def run_detail(
        cls,
        payload: Mapping[str, Any],
        *,
        task_id: str,
    ) -> dict[str, Any]:
        run_id = _string(payload.get("run_id"), 256)
        raw_events = payload.get("events")
        events = (
            [
                cls.event(event, task_id=task_id)
                for event in raw_events
                if isinstance(event, Mapping)
            ]
            if isinstance(raw_events, list)
            else []
        )
        raw_audit = payload.get("audit")
        audit = raw_audit if isinstance(raw_audit, Mapping) else {}
        status = _string(audit.get("status"), 64) or "running"
        tool_count = sum(event.get("type") == "tool_call" for event in events)
        completed_tool_count = sum(
            event.get("type") == "tool_result" and event.get("status") != "error"
            for event in events
        )
        failed_tool_count = sum(
            event.get("type") == "tool_result" and event.get("status") == "error"
            for event in events
        )
        return {
            "run_id": run_id,
            "events": events,
            "timeline": [],
            "audit": {
                "run_id": run_id,
                "status": status,
                "headline": f"Learning run: {status}",
                "tool_count": tool_count,
                "completed_tool_count": completed_tool_count,
                "failed_tool_count": failed_tool_count,
                "approval_count": 0,
                "duration_ms": _non_negative_int(audit.get("duration_ms")),
                "changed_files": [],
                "steps": [],
            },
        }

    @staticmethod
    def run_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
        run_id = _string(payload.get("run_id"), 256)
        status = _string(payload.get("status"), 64) or "running"
        raw_audit = payload.get("audit")
        audit = raw_audit if isinstance(raw_audit, Mapping) else {}
        tool_count = _non_negative_int(payload.get("tool_count"))
        error_count = _non_negative_int(payload.get("error_count"))
        return {
            "run_id": run_id,
            "status": status,
            "event_count": _non_negative_int(payload.get("event_count")),
            "tool_count": tool_count,
            "error_count": error_count,
            "last_event_type": _string(payload.get("last_event_type"), 128),
            "started_at": _string(payload.get("started_at"), 80),
            "updated_at": _string(payload.get("updated_at"), 80),
            "changed_files": [],
            "audit": {
                "run_id": run_id,
                "status": status,
                "headline": f"Learning run: {status}",
                "tool_count": tool_count,
                "completed_tool_count": max(0, tool_count - error_count),
                "failed_tool_count": min(tool_count, error_count),
                "approval_count": 0,
                "duration_ms": _non_negative_int(audit.get("duration_ms")),
                "changed_files": [],
                "steps": [],
            },
        }


def _string(value: object, limit: int) -> str:
    return str(value).strip()[:limit] if isinstance(value, str) else ""


def _non_negative_int(value: object) -> int:
    return value if type(value) is int and value >= 0 else 0


__all__ = ["LearningPublicProjector"]
