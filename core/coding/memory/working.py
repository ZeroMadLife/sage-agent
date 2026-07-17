"""Working memory derived from runtime evidence, discarded after each run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkingMemory:
    """Per-run working memory, rebuilt from session/run evidence."""

    task_summary: str = ""
    last_tool_result: str = ""
    last_error: str = ""
    recent_files: list[dict[str, str]] = field(default_factory=list)  # [{"path": ..., "hash": ...}]
    permission_mode: str = "default"
    plan_mode: bool = False
    active_skill: str = ""
    budget: int = 2000

    def to_context_block(self) -> str:
        """Render as a context block for ContextManager injection."""
        lines = ["<working-memory>"]
        if self.task_summary:
            lines.append(f"Task: {self.task_summary}")
        if self.recent_files:
            lines.append("Recent files:")
            for f in self.recent_files[:8]:
                lines.append(f"  - {f['path']}")
        if self.last_error:
            lines.append(f"Last error: {self.last_error[:200]}")
        if self.permission_mode != "default":
            lines.append(f"Permission mode: {self.permission_mode}")
        if self.plan_mode:
            lines.append("Plan mode: active")
        lines.append("</working-memory>")
        return "\n".join(lines)

    @classmethod
    def from_session(
        cls,
        session: dict[str, Any],
        runtime_mode: str,
        permission_mode: str,
        budget: int = 2000,
    ) -> WorkingMemory:
        """Build working memory from session state."""
        history = session.get("history", [])
        task_summary = ""
        last_error = ""
        recent_files: list[dict[str, str]] = []

        for item in reversed(history):
            role = str(item.get("role", ""))
            content = str(item.get("content", ""))
            if role == "user" and content and not task_summary:
                task_summary = content[:200]
            if role == "tool" and item.get("is_error") and not last_error:
                last_error = content[:200]
            if role == "tool" and item.get("name") in {"read_file", "write_file", "patch_file"}:
                path = (
                    str(item.get("args", {}).get("path", ""))
                    if isinstance(item.get("args"), dict)
                    else ""
                )
                if path and not any(f["path"] == path for f in recent_files):
                    recent_files.append({"path": path, "hash": ""})
            if len(recent_files) >= 8:
                break

        return cls(
            task_summary=task_summary,
            last_error=last_error,
            recent_files=recent_files,
            permission_mode=permission_mode,
            plan_mode=runtime_mode == "plan",
            budget=budget,
        )
