"""Per-run trace persistence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


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
            self.evidence_root = root  # backward compat: global runs/
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
            event for event in events if str(event.get("type", "")) not in {"turn_finished", "run_finished"}
        ]
        last = business_events[-1] if business_events else events[-1]
        return {
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
