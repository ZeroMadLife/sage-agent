"""Per-run trace persistence."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

_PREVIEW_LIMIT_BYTES = 4096
_PREVIEW_HEAD_BYTES = 3000
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "passwd",
    "password",
    "secret",
    "token",
)
_OMITTED_ARGUMENT_KEYS = {"content", "diff", "env", "input", "patch", "text"}


class RunStore:
    """Persist trace files for individual coding runs.

    When ``session_id`` is provided, run traces are partitioned under
    ``root / "evidence" / session_id / "runs"`` so that different sessions
    never share the same flat directory. When ``session_id`` is empty the
    store falls back to the legacy global ``root`` layout for backward
    compatibility.
    """

    def __init__(self, root: Path, session_id: str = "") -> None:
        self.root = root
        self.session_id = session_id
        if session_id:
            self.evidence_root = root / "evidence" / session_id / "runs"
        else:
            self.evidence_root = root / "runs"  # backward compat: global runs/
        self.evidence_root.mkdir(parents=True, exist_ok=True)

    def start_run(self, run_id: str, session_id: str = "") -> Path:
        """Create and return a run directory.

        ``session_id`` is accepted for API symmetry; the active partition is
        already fixed at construction time. A non-empty override is ignored
        when the store already has a session_id bound.
        """
        run_dir = self.evidence_root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    def append_trace(self, run_id: str, event: dict[str, Any]) -> Path:
        """Append one trace event."""
        path = self.evidence_root / run_id / "trace.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        return path

    def list_runs(self, limit: int = 30, session_id: str = "") -> list[dict[str, Any]]:
        """Return run summaries ordered by most recently updated.

        If ``session_id`` is provided and this store has no bound session_id,
        the summaries are read from that session's partition instead of the
        default evidence root. This lets a session-less (global) store still
        inspect a specific session's runs.
        """
        evidence_root = self._resolve_evidence_root(session_id)
        summaries: list[dict[str, Any]] = []
        if not evidence_root.is_dir():
            return summaries
        for path in evidence_root.iterdir():
            if not path.is_dir():
                continue
            summary = self._summarize_run(path.name, session_id=session_id)
            if summary is not None:
                summaries.append(summary)
        return sorted(summaries, key=lambda item: str(item["updated_at"]), reverse=True)[:limit]

    def get_run(self, run_id: str, session_id: str = "") -> dict[str, Any]:
        """Return one run trace."""
        events = self._read_events(run_id, session_id=session_id)
        if not events:
            raise FileNotFoundError(run_id)
        return {
            "run_id": run_id,
            "events": events,
            "timeline": _timeline_from_events(events),
            "audit": _audit_summary(run_id, events),
        }

    def run_status(self, run_id: str, session_id: str = "") -> str:
        """Return the terminal status of a run from its trace."""
        evidence_root = self._resolve_evidence_root(session_id)
        run_dir = evidence_root / run_id
        if not run_dir.is_dir():
            return "unknown"
        events = self._read_events(run_id, session_id=session_id)
        return _status_from_events(events)

    def run_tool_count(self, run_id: str, session_id: str = "") -> int:
        """Count tool_result events in a run trace."""
        evidence_root = self._resolve_evidence_root(session_id)
        run_dir = evidence_root / run_id
        if not run_dir.is_dir():
            return 0
        events = self._read_events(run_id, session_id=session_id)
        return sum(1 for event in events if event.get("type") == "tool_result")

    def _resolve_evidence_root(self, session_id: str) -> Path:
        """Pick the partition root honoring a per-call session override.

        The per-call ``session_id`` only matters for a session-less (global)
        store: it lets the global store inspect a specific session partition.
        A store already bound to a session always uses its own partition.
        """
        if session_id and not self.session_id:
            return self.root / "evidence" / session_id / "runs"
        return self.evidence_root

    def _summarize_run(self, run_id: str, session_id: str = "") -> dict[str, Any] | None:
        events = self._read_events(run_id, session_id=session_id)
        if not events:
            return None
        first = events[0]
        business_events = [
            event
            for event in events
            if str(event.get("type", "")) not in {"turn_finished", "run_finished"}
        ]
        last = business_events[-1] if business_events else events[-1]
        # Extract changed_files from the workspace_diff_ready event (if any) so
        # the run history list surfaces what the run touched at a glance.
        changed_files: list[Any] = []
        for event in events:
            if event.get("type") == "workspace_diff_ready":
                changed_files = event.get("changed_files", [])
                break
        summary = {
            "run_id": run_id,
            "status": _status_from_events(events),
            "event_count": len(events),
            "tool_count": sum(1 for event in events if event.get("type") == "tool_call"),
            "error_count": sum(
                1 for event in events if event.get("type") == "error" or event.get("is_error")
            ),
            "last_event_type": str(last.get("type", "")),
            "started_at": str(first.get("created_at") or first.get("timestamp") or ""),
            "updated_at": str(last.get("created_at") or first.get("created_at") or ""),
        }
        summary["changed_files"] = changed_files
        summary["audit"] = _audit_summary(run_id, events)
        return summary

    def _read_events(self, run_id: str, session_id: str = "") -> list[dict[str, Any]]:
        evidence_root = self._resolve_evidence_root(session_id)
        path = evidence_root / run_id / "trace.jsonl"
        if not path.is_file():
            return []
        events: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
        return events


def _audit_summary(run_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    status = _status_from_events(events)
    changed_files = _changed_files_from_events(events)
    step_states: list[dict[str, Any]] = []
    approvals: list[dict[str, Any]] = []

    for event in events:
        event_type = str(event.get("type", ""))
        if event_type == "approval_required":
            approvals.append({"event": event, "decision": None, "used": False})
            continue
        if event_type == "approval_granted":
            approval = _matching_approval_decision(approvals, event)
            if approval is not None:
                approval["decision"] = event
            continue
        if event_type == "tool_call":
            approval = _matching_approval(approvals, event)
            if approval is not None:
                approval["used"] = True
            step_states.append({"call": event, "result": None, "approval": approval})
            continue
        if event_type != "tool_result":
            continue
        target = _matching_step(step_states, event)
        if target is None:
            approval = _matching_approval(approvals, event)
            if approval is not None:
                approval["used"] = True
            target = {"call": None, "result": None, "approval": approval}
            step_states.append(target)
        elif target.get("approval") is None:
            approval = _matching_approval(approvals, event)
            if approval is not None:
                approval["used"] = True
                target["approval"] = approval
        target["result"] = event

    for approval in approvals:
        if not approval["used"]:
            step_states.append({"call": None, "result": None, "approval": approval})

    steps = [_project_audit_step(state) for state in step_states]
    completed = sum(step["status"] == "completed" for step in steps)
    failed = sum(step["status"] == "error" for step in steps)
    duration_ms = _run_duration_ms(events)
    parts = [_run_status_label(status), f"{len(steps)} 项工具"]
    if changed_files:
        parts.append(f"修改 {len(changed_files)} 个文件")
    return {
        "run_id": run_id,
        "status": status,
        "headline": " · ".join(parts),
        "tool_count": len(steps),
        "completed_tool_count": completed,
        "failed_tool_count": failed,
        "approval_count": sum(event.get("type") == "approval_required" for event in events),
        "duration_ms": duration_ms,
        "changed_files": changed_files,
        "steps": steps,
    }


def _matching_step(
    steps: list[dict[str, Any]],
    result: dict[str, Any],
) -> dict[str, Any] | None:
    tool = str(result.get("tool", ""))
    args = result.get("args")
    for step in reversed(steps):
        call = step.get("call")
        if step.get("result") is not None or not isinstance(call, dict):
            continue
        if call.get("tool") == tool and call.get("args") == args:
            return step
    for step in reversed(steps):
        call = step.get("call")
        if step.get("result") is None and isinstance(call, dict) and call.get("tool") == tool:
            return step
    return None


def _matching_approval(
    approvals: list[dict[str, Any]],
    event: dict[str, Any],
) -> dict[str, Any] | None:
    tool = str(event.get("tool", ""))
    args = event.get("args")
    tool_call_id = str(event.get("tool_call_id", "")).strip()
    if tool_call_id:
        for approval in reversed(approvals):
            payload = approval["event"]
            if not approval["used"] and payload.get("tool_call_id") == tool_call_id:
                return approval
    for approval in reversed(approvals):
        payload = approval["event"]
        if not approval["used"] and payload.get("tool") == tool and payload.get("args") == args:
            return approval
    for approval in reversed(approvals):
        payload = approval["event"]
        if not approval["used"] and payload.get("tool") == tool:
            return approval
    return None


def _matching_approval_decision(
    approvals: list[dict[str, Any]],
    event: dict[str, Any],
) -> dict[str, Any] | None:
    tool_call_id = str(event.get("tool_call_id", "")).strip()
    tool = str(event.get("tool", ""))
    if tool_call_id:
        for approval in reversed(approvals):
            payload = approval["event"]
            if approval.get("decision") is None and payload.get("tool_call_id") == tool_call_id:
                return approval
    for approval in reversed(approvals):
        payload = approval["event"]
        if approval.get("decision") is None and payload.get("tool") == tool:
            return approval
    return None


def _project_audit_step(state: dict[str, Any]) -> dict[str, Any]:
    call = state.get("call") if isinstance(state.get("call"), dict) else None
    result = state.get("result") if isinstance(state.get("result"), dict) else None
    approval_state = state.get("approval")
    approval = (
        approval_state.get("event")
        if isinstance(approval_state, dict) and isinstance(approval_state.get("event"), dict)
        else None
    )
    approval_decision = (
        approval_state.get("decision")
        if isinstance(approval_state, dict) and isinstance(approval_state.get("decision"), dict)
        else None
    )
    source = call or result or approval or {}
    tool = str(source.get("tool", ""))
    raw_args: Any = {}
    for candidate in (call, result, approval):
        candidate_args = candidate.get("args") if isinstance(candidate, dict) else None
        if isinstance(candidate_args, dict) and candidate_args:
            raw_args = candidate_args
            break
    args: dict[str, Any] = (
        {str(key): value for key, value in raw_args.items()} if isinstance(raw_args, dict) else {}
    )
    safe_args = _safe_arguments(tool, args)
    arguments_preview, arguments_truncated = _bounded_preview(
        json.dumps(safe_args, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    )
    status = "waiting" if approval is not None and call is None and result is None else "running"
    if result is not None:
        status = "error" if bool(result.get("is_error")) else "completed"
    result_text = _safe_result_preview(tool, result)
    result_preview, result_truncated = _bounded_preview(result_text)
    start_event = (
        _latest_timestamped_event(call, approval_decision)
        or call
        or approval_decision
        or approval
        or result
        or {}
    )
    return {
        "tool": tool,
        "status": status,
        "action_summary": _tool_action_summary(tool, safe_args),
        "result_summary": _result_summary(result, status),
        "duration_ms": _duration_between(start_event, result),
        "arguments_preview": arguments_preview,
        "result_preview": result_preview,
        "arguments_truncated": arguments_truncated,
        "result_truncated": result_truncated,
    }


def _safe_arguments(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in args.items():
        normalized = key.lower().replace("-", "_")
        if any(part in normalized for part in _SENSITIVE_KEY_PARTS):
            safe[key] = "[REDACTED]"
        elif normalized in _OMITTED_ARGUMENT_KEYS:
            safe[key] = "[OMITTED]"
        else:
            safe[key] = _sanitize_value(value)
    if tool in {"write_file", "patch_file"}:
        for key in tuple(safe):
            if key.lower() not in {"path", "create", "overwrite"}:
                safe[key] = "[OMITTED]"
    return safe


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, dict):
        return _safe_arguments("", value)
    return value


def _redact_text(text: str) -> str:
    redacted = re.sub(
        r"(?i)(authorization\s*:\s*)(?:bearer\s+|basic\s+)?[^\s'\"]+",
        r"\1[REDACTED]",
        text,
    )
    redacted = re.sub(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", redacted)
    redacted = re.sub(
        r"(?i)\b([A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD))\s*=\s*[^\s]+",
        r"\1=[REDACTED]",
        redacted,
    )
    return redacted


def _safe_result_preview(tool: str, result: dict[str, Any] | None) -> str:
    if tool == "read_file":
        return "已读取文件内容（摘要不展示正文）"
    if result is None:
        return ""
    return _redact_text(str(result.get("content", "")))


def _tool_action_summary(tool: str, args: dict[str, Any]) -> str:
    path = str(args.get("path", "")).strip()
    if tool == "read_file":
        return f"读取 {path}" if path else "读取文件"
    if tool == "list_files":
        return f"列出 {path or '.'}"
    if tool == "search":
        pattern = str(args.get("pattern", "")).strip()
        return f"搜索 {pattern or path or '工作区'}"
    if tool == "write_file":
        return f"写入 {path or '文件'}"
    if tool == "patch_file":
        return f"修改 {path or '文件'}"
    if tool == "run_shell":
        command = str(args.get("command", "")).strip()
        return f"执行 {command or 'shell 命令'}"
    if tool == "knowledge_search":
        query = str(args.get("query", "")).strip()
        return f"检索知识库 {query or '证据'}"
    if tool == "search_web":
        query = str(args.get("query", "")).strip()
        return f"搜索网页 {query or '公开资料'}"
    if tool == "fetch_web":
        url = str(args.get("url", "")).strip()
        return f"抓取网页 {url or '正文'}"
    if tool == "agent":
        task = str(args.get("task", "")).strip()
        return f"子任务 {task or '执行'}"
    return f"调用 {tool or '工具'}"


def _result_summary(result: dict[str, Any] | None, status: str) -> str:
    if result is None:
        return "等待确认" if status == "waiting" else "执行中"
    content = str(result.get("content", ""))
    exit_code = re.search(r"(?m)^exit_code:\s*(-?\d+)\s*$", content)
    parts: list[str] = []
    if exit_code:
        parts.append(f"退出码 {exit_code.group(1)}")
    if bool(result.get("is_error")):
        parts.append("执行失败")
    elif not parts:
        parts.append("执行完成")
    return " · ".join(parts)


def _changed_files_from_events(events: list[dict[str, Any]]) -> list[str]:
    for event in reversed(events):
        if event.get("type") != "workspace_diff_ready":
            continue
        files = event.get("changed_files")
        if isinstance(files, list):
            return [str(item) for item in files if isinstance(item, str)]
    return []


def _run_duration_ms(events: list[dict[str, Any]]) -> int:
    for event in reversed(events):
        if event.get("type") == "run_finished":
            try:
                return max(0, int(event.get("duration_ms", 0)))
            except (TypeError, ValueError):
                return 0
    if events:
        return _duration_between(events[0], events[-1])
    return 0


def _duration_between(start: dict[str, Any] | None, end: dict[str, Any] | None) -> int:
    if not start or not end:
        return 0
    try:
        started = datetime.fromisoformat(_event_timestamp(start).replace("Z", "+00:00"))
        finished = datetime.fromisoformat(_event_timestamp(end).replace("Z", "+00:00"))
        return max(0, int((finished - started).total_seconds() * 1000))
    except (TypeError, ValueError):
        return 0


def _latest_timestamped_event(
    *events: dict[str, Any] | None,
) -> dict[str, Any] | None:
    latest: dict[str, Any] | None = None
    latest_at: datetime | None = None
    for event in events:
        if not event:
            continue
        try:
            created_at = datetime.fromisoformat(_event_timestamp(event).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if latest_at is None or created_at > latest_at:
            latest = event
            latest_at = created_at
    return latest


def _run_status_label(status: str) -> str:
    return {
        "cancelled": "运行已取消",
        "completed": "运行完成",
        "error": "运行失败",
        "running": "正在运行",
        "step_limit": "达到步骤上限",
    }.get(status, "运行结束")


def _bounded_preview(text: str) -> tuple[str, bool]:
    encoded = text.encode("utf-8")
    if len(encoded) <= _PREVIEW_LIMIT_BYTES:
        return text, False
    tail_budget = 900
    omitted = len(encoded) - _PREVIEW_HEAD_BYTES - tail_budget
    marker = f"\n… 省略 {max(0, omitted)} 字节 …\n".encode()
    tail_budget = max(0, _PREVIEW_LIMIT_BYTES - _PREVIEW_HEAD_BYTES - len(marker))
    omitted = len(encoded) - _PREVIEW_HEAD_BYTES - tail_budget
    marker = f"\n… 省略 {omitted} 字节 …\n".encode()
    head = _decode_edge(encoded[:_PREVIEW_HEAD_BYTES], from_tail=False)
    tail = _decode_edge(encoded[-tail_budget:] if tail_budget else b"", from_tail=True)
    preview = head + marker.decode() + tail
    while len(preview.encode("utf-8")) > _PREVIEW_LIMIT_BYTES and tail:
        tail = tail[1:]
        preview = head + marker.decode() + tail
    return preview, True


def _decode_edge(value: bytes, *, from_tail: bool) -> str:
    if not value:
        return ""
    if from_tail:
        return value.decode("utf-8", errors="ignore")
    return value.decode("utf-8", errors="ignore")


def _status_from_events(events: list[dict[str, Any]]) -> str:
    event_types = [str(event.get("type", "")) for event in events]
    if "run_finished" in event_types:
        # Prefer the explicit status recorded on the run_finished event.
        for event in reversed(events):
            if event.get("type") == "run_finished":
                return str(event.get("status", "completed"))
    if "cancelled" in event_types:
        return "cancelled"
    if "error" in event_types:
        return "error"
    if "final" in event_types:
        return "completed"
    if "step_limit" in event_types:
        return "step_limit"
    return "running"


def _timeline_from_events(events: list[dict[str, Any]]) -> list[dict[str, str]]:
    timeline: list[dict[str, str]] = []
    for event in events:
        entry = _timeline_entry(event)
        if entry is not None:
            timeline.append(entry)
    return timeline


def _timeline_entry(event: dict[str, Any]) -> dict[str, str] | None:
    event_type = str(event.get("type", ""))
    timestamp = _event_timestamp(event)
    if event_type == "model_requested":
        return _entry(
            kind="model",
            title="Model request",
            detail=_model_request_detail(event),
            status="running",
            timestamp=timestamp,
        )
    if event_type == "model_parsed":
        kind = str(event.get("kind", ""))
        return _entry(
            kind="model",
            title=f"Parsed {kind}".strip(),
            detail="",
            status="done",
            timestamp=timestamp,
        )
    if event_type == "tool_call":
        tool = str(event.get("tool", ""))
        return _entry(
            kind="tool",
            title=f"Run {tool}".strip(),
            detail=_args_detail(event.get("args")),
            status="running",
            tool=tool,
            timestamp=timestamp,
        )
    if event_type == "tool_result":
        tool = str(event.get("tool", ""))
        failed = bool(event.get("is_error"))
        return _entry(
            kind="result",
            title=f"{tool} {'failed' if failed else 'succeeded'}".strip(),
            detail=_clip(str(event.get("content", ""))),
            status="error" if failed else "done",
            tool=tool,
            timestamp=timestamp,
        )
    if event_type == "approval_required":
        tool = str(event.get("tool", ""))
        return _entry(
            kind="approval",
            title=f"Approval required: {tool}".strip(),
            detail=str(event.get("description", "")),
            status="blocked",
            tool=tool,
            timestamp=timestamp,
        )
    if event_type == "approval_granted":
        tool = str(event.get("tool", ""))
        return _entry(
            kind="approval",
            title=f"Approval granted: {tool}".strip(),
            detail="",
            status="done",
            tool=tool,
            timestamp=timestamp,
        )
    if event_type == "final":
        return _entry(
            kind="final",
            title="Final answer",
            detail=_clip(str(event.get("content", ""))),
            status="done",
            timestamp=timestamp,
        )
    if event_type == "cancelled":
        return _entry(
            kind="system",
            title="Run cancelled",
            detail=_clip(str(event.get("content", ""))),
            status="error",
            timestamp=timestamp,
        )
    if event_type == "step_limit":
        return _entry(
            kind="system",
            title="Step limit reached",
            detail=_clip(str(event.get("content", ""))),
            status="error",
            timestamp=timestamp,
        )
    if event_type == "error":
        return _entry(
            kind="error",
            title="Runtime error",
            detail=_clip(str(event.get("message", ""))),
            status="error",
            timestamp=timestamp,
        )
    if event_type == "retry":
        return _entry(
            kind="model",
            title="Protocol retry",
            detail=_clip(str(event.get("content", ""))),
            status="error",
            timestamp=timestamp,
        )
    if event_type == "run_finished":
        status = str(event.get("status", "completed"))
        return _entry(
            kind="system",
            title="Run finished",
            detail=status,
            status="error" if status in {"error", "cancelled"} else "done",
            timestamp=timestamp,
        )
    return None


def _entry(
    *,
    kind: str,
    title: str,
    detail: str,
    status: str,
    tool: str = "",
    timestamp: str = "",
) -> dict[str, str]:
    return {
        "kind": kind,
        "title": title,
        "detail": detail,
        "status": status,
        "tool": tool,
        "timestamp": timestamp,
    }


def _model_request_detail(event: dict[str, Any]) -> str:
    attempt = event.get("attempts")
    step = event.get("tool_steps")
    prompt_chars = event.get("prompt_chars")
    parts: list[str] = []
    if attempt is not None:
        parts.append(f"attempt {attempt}")
    if step is not None:
        parts.append(f"step {step}")
    if prompt_chars is not None:
        parts.append(f"{prompt_chars} chars")
    return " · ".join(parts)


def _args_detail(args: Any) -> str:
    if not isinstance(args, dict) or not args:
        return ""
    details: list[str] = []
    for key, value in args.items():
        if isinstance(value, str):
            rendered = value.replace("\n", "\\n")
        elif isinstance(value, int | float | bool):
            rendered = str(value)
        else:
            rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
        details.append(f"{key}={_clip(rendered, limit=80)}")
        if len(details) >= 3:
            break
    return " · ".join(details)


def _event_timestamp(event: dict[str, Any]) -> str:
    return str(event.get("created_at") or event.get("timestamp") or "")


def _clip(text: str, limit: int = 180) -> str:
    cleaned = text.strip()
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[: limit - 1].rstrip()}…"
