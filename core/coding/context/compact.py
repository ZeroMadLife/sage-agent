"""History compaction for coding sessions."""

from __future__ import annotations

from typing import Any

from core.coding.context.manager import ContextManager
from core.coding.context.workspace import now


class CompactManager:
    """Fold older turns into a compact summary."""

    def compact(
        self,
        history: list[dict[str, Any]],
        keep_recent_turns: int = 2,
        trigger: str = "manual",
        context_manager: ContextManager | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Return compacted history and a summary record."""
        groups = self._group_turns(history)
        if len(groups) <= keep_recent_turns:
            return history, self._summary(trigger, history, history, "")

        compacted_groups = groups[:-keep_recent_turns]
        kept_groups = groups[-keep_recent_turns:]
        compacted_items = [item for group in compacted_groups for item in group]
        kept_items = [item for group in kept_groups for item in group]
        summary_text = self._summary_text(compacted_items)
        summary_item = {
            "role": "system",
            "kind": "compact_summary",
            "content": summary_text,
            "created_at": now(),
        }
        new_history = [summary_item, *kept_items]
        if context_manager is not None:
            context_manager.invalidate_system_prompt()
        return new_history, self._summary(trigger, history, new_history, summary_text)

    @staticmethod
    def _group_turns(history: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        groups: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        for item in history:
            if item.get("role") == "user" and current:
                groups.append(current)
                current = []
            current.append(item)
        if current:
            groups.append(current)
        return groups

    @staticmethod
    def _summary(
        trigger: str, before: list[dict[str, Any]], after: list[dict[str, Any]], text: str
    ) -> dict[str, Any]:
        return {
            "trigger": trigger,
            "created_at": now(),
            "pre_items": len(before),
            "post_items": len(after),
            "summary_chars": len(text),
        }

    @staticmethod
    def _summary_text(items: list[dict[str, Any]]) -> str:
        user_requests = [
            str(item.get("content", "")).strip() for item in items if item.get("role") == "user"
        ]
        files_read = [
            str(item.get("args", {}).get("path", ""))
            for item in items
            if item.get("role") == "tool" and item.get("name") == "read_file"
        ]
        files_modified = [
            str(item.get("args", {}).get("path", ""))
            for item in items
            if item.get("role") == "tool" and item.get("name") in {"write_file", "patch_file"}
        ]
        return "\n".join(
            [
                "Compacted session summary:",
                f"- Goal: {user_requests[-1] if user_requests else '-'}",
                f"- Files read: {', '.join(sorted(set(files_read))) or '-'}",
                f"- Files modified: {', '.join(sorted(set(files_modified))) or '-'}",
                f"- Current progress: compacted {len(items)} history items",
                "- Next step: continue from the latest preserved turn",
            ]
        )
