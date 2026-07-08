"""Local JSON session storage for coding runtime."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Literal, TypedDict, cast


class CodingChatMessage(TypedDict):
    """Replayable chat message persisted in a coding session."""

    role: Literal["user", "assistant"]
    content: str
    created_at: str


class CodingSessionStore:
    """Persist coding session state under .coding/sessions."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def path(self, session_id: str) -> Path:
        return self.root / f"{_safe_session_id(session_id)}.json"

    def event_path(self, session_id: str) -> Path:
        return self.root / f"{_safe_session_id(session_id)}.events.jsonl"

    def save(self, session: dict[str, Any]) -> Path:
        """Atomically save a session JSON file."""
        path = self.path(str(session["id"]))
        payload = json.dumps(session, indent=2, ensure_ascii=False, sort_keys=True)
        with self._lock:
            tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp_path.write_text(payload, encoding="utf-8")
            os.replace(tmp_path, path)
        return path

    def load(self, session_id: str) -> dict[str, Any]:
        """Load one session by id."""
        data = json.loads(self.path(session_id).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("session file must contain a JSON object")
        return cast(dict[str, Any], data)

    def list_sessions(self, limit: int = 30) -> list[dict[str, Any]]:
        """Return session summaries ordered by most recently updated."""
        summaries: list[dict[str, Any]] = []
        for path in self.root.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                summaries.append(_summarize_session(data))
        return sorted(summaries, key=lambda item: item["updated_at"], reverse=True)[:limit]

    def messages(self, session_id: str) -> list[CodingChatMessage]:
        """Return replayable user/assistant chat messages for one session."""
        data = self.load(session_id)
        history = data.get("history", [])
        if not isinstance(history, list):
            return []
        messages: list[CodingChatMessage] = []
        for item in history:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", ""))
            if role not in {"user", "assistant"}:
                continue
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            messages.append(
                {
                    "role": cast(Literal["user", "assistant"], role),
                    "content": content,
                    "created_at": str(item.get("created_at", "")),
                }
            )
        return messages


def _safe_session_id(session_id: str) -> str:
    value = session_id.strip()
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError("invalid session id")
    return value


def _summarize_session(data: dict[str, Any]) -> dict[str, Any]:
    workspace_root = str(data.get("workspace_root", ""))
    history = data.get("history", [])
    runtime_mode = data.get("runtime_mode", {})
    mode = runtime_mode.get("mode", "default") if isinstance(runtime_mode, dict) else "default"
    return {
        "session_id": str(data.get("id", "")),
        "title": _session_title(history, workspace_root),
        "workspace_root": workspace_root,
        "created_at": str(data.get("created_at", "")),
        "updated_at": str(data.get("updated_at", "")),
        "runtime_mode": str(mode),
        "message_count": len(history) if isinstance(history, list) else 0,
    }


def _session_title(history: Any, workspace_root: str) -> str:
    if isinstance(history, list):
        for item in history:
            if not isinstance(item, dict) or item.get("role") != "user":
                continue
            content = str(item.get("content", "")).strip()
            if content:
                return content[:60]
    name = Path(workspace_root).name
    return name or "Sage session"
