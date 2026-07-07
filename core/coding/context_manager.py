"""Prompt context assembly and budget control for the coding agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEFAULT_SYSTEM_PROMPT = """You are Sage, a personal coding agent running in the user's repository.

You inspect code with tools before editing, verify changes by running tests or
commands, and keep explanations concise. When a task needs multiple steps, use
the todo ledger to track progress.

Return exactly one or more <tool> calls, or one <final> answer."""


@dataclass(frozen=True)
class SectionRender:
    """Rendered section metadata."""

    raw: str
    rendered: str

    @property
    def raw_chars(self) -> int:
        return len(self.raw)

    @property
    def rendered_chars(self) -> int:
        return len(self.rendered)


class ContextManager:
    """Build prompt text from stable prefix, tools, history, and current request."""

    def __init__(
        self, total_budget: int = 60000, system_prompt: str = DEFAULT_SYSTEM_PROMPT
    ) -> None:
        self.total_budget = total_budget
        self.system_prompt = system_prompt

    def build(
        self,
        user_message: str,
        history: list[dict[str, Any]] | None = None,
        tools: list[str] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Return a budgeted prompt and metadata."""
        history = history or []
        tools = tools or []
        raw_sections = {
            "prefix": self.system_prompt,
            "tools": "Available tools:\n" + ("\n".join(f"- {tool}" for tool in tools) or "- none"),
            "history": self._render_history(history),
            "current_request": f"Current user request:\n{user_message}",
        }
        rendered = self._render_to_budget(raw_sections)
        prompt = self._assemble(rendered)
        metadata = {
            "prompt_chars": len(prompt),
            "prompt_budget_chars": self.total_budget,
            "prompt_over_budget": len(prompt) > self.total_budget,
            "sections": {
                name: {
                    "raw_chars": section.raw_chars,
                    "rendered_chars": section.rendered_chars,
                }
                for name, section in rendered.items()
            },
        }
        return prompt, metadata

    def _render_to_budget(self, raw_sections: dict[str, str]) -> dict[str, SectionRender]:
        current = raw_sections["current_request"]
        separators = 6
        remaining = max(0, self.total_budget - len(current) - separators)
        prefix_budget = max(0, remaining // 4)
        tools_budget = max(0, remaining // 4)
        history_budget = max(0, remaining - prefix_budget - tools_budget)
        sections = {
            "prefix": SectionRender(
                raw_sections["prefix"], tail_clip(raw_sections["prefix"], prefix_budget)
            ),
            "tools": SectionRender(
                raw_sections["tools"], tail_clip(raw_sections["tools"], tools_budget)
            ),
            "history": SectionRender(
                raw_sections["history"], tail_clip(raw_sections["history"], history_budget)
            ),
            "current_request": SectionRender(current, current),
        }
        prompt = self._assemble(sections)
        if len(prompt) <= self.total_budget:
            return sections

        overflow = len(prompt) - self.total_budget
        history = sections["history"]
        sections["history"] = SectionRender(
            history.raw,
            tail_clip(history.rendered, max(0, len(history.rendered) - overflow)),
        )
        prompt = self._assemble(sections)
        if len(prompt) <= self.total_budget:
            return sections

        overflow = len(prompt) - self.total_budget
        tools = sections["tools"]
        sections["tools"] = SectionRender(
            tools.raw,
            tail_clip(tools.rendered, max(0, len(tools.rendered) - overflow)),
        )
        return sections

    @staticmethod
    def _render_history(history: list[dict[str, Any]]) -> str:
        if not history:
            return "Transcript:\n- empty"
        lines = ["Transcript:"]
        for item in history:
            role = str(item.get("role", "unknown"))
            content = str(item.get("content", ""))
            if item.get("name"):
                role = f"{role}:{item['name']}"
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    @staticmethod
    def _assemble(sections: dict[str, SectionRender]) -> str:
        return "\n\n".join(
            sections[name].rendered for name in ("prefix", "tools", "history", "current_request")
        ).strip()


def tail_clip(text: str, limit: int) -> str:
    """Keep the tail of long text with a visible clipping marker."""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    marker = "...[clipped]\n"
    if limit <= len(marker):
        return text[-limit:]
    return marker + text[-(limit - len(marker)) :]
