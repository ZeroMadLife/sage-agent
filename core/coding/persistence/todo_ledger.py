"""Session-scoped todo ledger for the coding agent."""

from __future__ import annotations

from typing import Any, cast

from core.coding.context import now

VALID_STATUS = {"pending", "in_progress", "completed", "blocked"}
VALID_PRIORITY = {"low", "normal", "high"}


class TodoLedger:
    """Small in-memory task ledger that can be persisted by Runtime."""

    def __init__(self, state: dict[str, Any] | None = None) -> None:
        self.state = state if state is not None else {"next_id": 1, "items": []}
        self.state.setdefault("next_id", 1)
        self.state.setdefault("items", [])

    def add(
        self,
        content: str,
        status: str = "pending",
        priority: str = "normal",
        note: str = "",
    ) -> dict[str, Any]:
        """Add one todo item."""
        status = self._clean_status(status)
        priority = self._clean_priority(priority)
        todo_id = f"todo_{int(self.state.get('next_id', 1))}"
        self.state["next_id"] = int(self.state.get("next_id", 1)) + 1
        item = {
            "id": todo_id,
            "content": content.strip(),
            "status": status,
            "priority": priority,
            "note": note.strip(),
            "created_at": now(),
            "updated_at": now(),
        }
        self.state.setdefault("items", []).append(item)
        return item

    def update(self, todo_id: str, **changes: Any) -> dict[str, Any]:
        """Update an existing todo item."""
        item = self.get(todo_id)
        if changes.get("content") is not None:
            item["content"] = str(changes["content"]).strip()
        if changes.get("note") is not None:
            item["note"] = str(changes["note"]).strip()
        if changes.get("status") is not None:
            item["status"] = self._clean_status(str(changes["status"]))
        if changes.get("priority") is not None:
            item["priority"] = self._clean_priority(str(changes["priority"]))
        item["updated_at"] = now()
        return item

    def get(self, todo_id: str) -> dict[str, Any]:
        """Return one todo by id."""
        for item in self.state.setdefault("items", []):
            if item.get("id") == todo_id:
                return cast(dict[str, Any], item)
        raise ValueError(f"unknown todo_id: {todo_id}")

    def render_list(self) -> str:
        """Render todos for prompt or tool output."""
        items = list(self.state.setdefault("items", []))
        if not items:
            return "Task ledger:\n- empty"
        lines = ["Task ledger:"]
        for item in items:
            note = f" ({item['note']})" if item.get("note") else ""
            lines.append(
                f"- {item['id']} [{item['status']}] {item['priority']} - "
                f"{item['content']}{note}"
            )
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-serializable state."""
        return {
            "next_id": int(self.state.get("next_id", 1)),
            "items": [dict(item) for item in self.state.get("items", [])],
        }

    @staticmethod
    def _clean_status(value: str) -> str:
        status = str(value or "pending").strip()
        if status not in VALID_STATUS:
            raise ValueError(f"status must be one of {', '.join(sorted(VALID_STATUS))}")
        return status

    @staticmethod
    def _clean_priority(value: str) -> str:
        priority = str(value or "normal").strip()
        if priority not in VALID_PRIORITY:
            raise ValueError(f"priority must be one of {', '.join(sorted(VALID_PRIORITY))}")
        return priority
