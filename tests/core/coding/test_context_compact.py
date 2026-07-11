"""Coding context budget and compaction tests."""

from datetime import date
from pathlib import Path

from core.coding.context import (
    DEFAULT_SYSTEM_PROMPT,
    SYSTEM_PROMPT_DYNAMIC_BOUNDARY,
    CompactManager,
    ContextManager,
)
from core.coding.runtime import CodingRuntime


class FakeModel:
    """Model that records prompts for context tests."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return "<final>done</final>"


def test_context_manager_preserves_safety_sections_when_prompt_is_over_budget() -> None:
    """ContextManager preserves system/current text and reports unresolved overflow."""
    history = [
        {"role": "user", "content": "old question " + ("x" * 80)},
        {"role": "assistant", "content": "old answer " + ("y" * 80)},
    ] * 30
    manager = ContextManager(total_budget=600)

    prompt, metadata = manager.build(
        user_message="current request must remain visible",
        history=history,
        tools=["read_file: read a file", "search: search files"],
    )

    assert DEFAULT_SYSTEM_PROMPT in prompt
    assert "current request must remain visible" in prompt
    assert metadata["prompt_over_budget"] is True
    assert (
        metadata["sections"]["prefix"]["rendered_chars"]
        == metadata["sections"]["prefix"]["raw_chars"]
    )
    assert (
        metadata["sections"]["history"]["rendered_chars"]
        == metadata["sections"]["history"]["raw_chars"]
    )


def test_compact_manager_summarizes_old_turns_and_keeps_recent_turns() -> None:
    """CompactManager folds old turns into a compact_summary item."""
    history = [
        {"role": "user", "content": f"request {index}"}
        if offset == 0
        else {"role": "assistant", "content": f"answer {index}"}
        for index in range(5)
        for offset in range(2)
    ]

    new_history, summary = CompactManager().compact(history, keep_recent_turns=2)

    assert new_history[0]["role"] == "system"
    assert new_history[0]["kind"] == "compact_summary"
    assert "request 2" in new_history[0]["content"]
    assert [item["content"] for item in new_history[-4:]] == [
        "request 3",
        "answer 3",
        "request 4",
        "answer 4",
    ]
    assert summary["pre_items"] == 10
    assert summary["post_items"] == 5


def test_context_manager_reuses_cached_system_prompt_across_turns() -> None:
    """Stable system prompt is built once for repeated builds with the same tools."""
    manager = ContextManager(today=lambda: date(2026, 7, 8))
    tools = ["read_file: read a file", "search: search files"]

    first, first_metadata = manager.build("first request", tools=tools)
    second, second_metadata = manager.build("second request", tools=tools)

    assert manager.system_prompt_build_count == 1
    assert "Session date: 2026-07-08" in first
    assert "Session date: 2026-07-08" in second
    assert first_metadata["sections"]["prefix"] == second_metadata["sections"]["prefix"]


def test_context_manager_invalidate_rebuilds_cached_system_prompt() -> None:
    """Explicit invalidation forces the next build to rebuild the system prompt."""
    current_day = date(2026, 7, 8)
    manager = ContextManager(today=lambda: current_day)

    first = manager.build_system_prompt_once(["read_file: read a file"])
    manager.invalidate_system_prompt()
    second = manager.build_system_prompt_once(["read_file: read a file"])

    assert first == second
    assert manager.system_prompt_build_count == 2


def test_context_manager_uses_date_precision_for_volatile_tier() -> None:
    """Volatile prompt uses date precision, not second/minute precision."""
    manager = ContextManager(today=lambda: date(2026, 7, 8))

    prompt = manager.build_system_prompt_once([])
    date_line = next(line for line in prompt.splitlines() if line.startswith("Session date:"))

    assert "Session date: 2026-07-08" in prompt
    assert "T" not in date_line
    assert ":00" not in date_line


def test_system_prompt_has_seven_layers_and_dynamic_boundary() -> None:
    """Sage system prompt exposes a stable seven-layer core before dynamic data."""
    manager = ContextManager(today=lambda: date(2026, 7, 8))

    prompt = manager.build_system_prompt_once(["read_file: read a file"])

    for heading in (
        "# System",
        "# Doing tasks",
        "# Executing actions with care",
        "# Using your tools",
        "# Tone and style",
        "# Output efficiency",
        "# Response protocol",
    ):
        assert heading in prompt
    assert SYSTEM_PROMPT_DYNAMIC_BOUNDARY in prompt
    assert prompt.index("read_file: read a file") < prompt.index(SYSTEM_PROMPT_DYNAMIC_BOUNDARY)
    assert prompt.index(SYSTEM_PROMPT_DYNAMIC_BOUNDARY) < prompt.index("Session date: 2026-07-08")


def test_workspace_reminder_is_after_boundary_and_not_stable_prompt() -> None:
    """Project reminder content is render-only context after the cache boundary."""
    manager = ContextManager(today=lambda: date(2026, 7, 8))

    prompt, _ = manager.build(
        "hello",
        workspace_reminders=["SAGE.md:\nDo not forget repo notes."],
    )

    stable, dynamic = prompt.split(SYSTEM_PROMPT_DYNAMIC_BOUNDARY, maxsplit=1)
    assert "Do not forget repo notes." not in stable
    assert "Do not forget repo notes." in dynamic


async def test_workspace_reminder_does_not_enter_session_replay(tmp_path: Path) -> None:
    """SAGE.md reminders are included in prompts but not persisted as chat messages."""
    (tmp_path / "SAGE.md").write_text("Project-only instruction", encoding="utf-8")
    model = FakeModel()
    runtime = CodingRuntime(
        session_id="coding_1",
        workspace_root=tmp_path,
        model=model,
        storage_root=tmp_path / ".coding",
    )

    events = [event async for event in runtime.run_turn("say hi")]

    assert "final" in [event["type"] for event in events]
    assert events[-1]["type"] == "turn_finished"
    assert "Project-only instruction" in model.prompts[0]
    assert runtime.session["history"] == [
        {
            "role": "user",
            "content": "say hi",
            "created_at": runtime.session["history"][0]["created_at"],
        },
        {
            "role": "assistant",
            "content": "done",
            "created_at": runtime.session["history"][1]["created_at"],
        },
    ]


def test_compact_manager_invalidates_context_cache_after_compaction() -> None:
    """Compaction can invalidate the prompt cache when memory/history changed."""
    manager = ContextManager()
    manager.build_system_prompt_once(["read_file: read a file"])
    assert manager.system_prompt_build_count == 1

    history = [
        {"role": "user", "content": f"request {index}"}
        if offset == 0
        else {"role": "assistant", "content": f"answer {index}"}
        for index in range(4)
        for offset in range(2)
    ]

    CompactManager().compact(history, keep_recent_turns=1, context_manager=manager)
    manager.build_system_prompt_once(["read_file: read a file"])

    assert manager.system_prompt_build_count == 2


def test_context_manager_injects_skill_prompt_between_prefix_and_history() -> None:
    """skill_prompt is injected after the prefix and before history/current request."""
    manager = ContextManager(today=lambda: date(2026, 7, 8))
    skill_body = "你正在使用 Sage 的 travel-planning domain skill。"
    history = [
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": "earlier answer"},
    ]

    prompt, metadata = manager.build(
        user_message="/travel-planning 我要去莆田",
        history=history,
        tools=["read_file: read a file"],
        skill_prompt=skill_body,
    )

    # The skill instruction is wrapped and present in the prompt.
    assert "<skill-instructions>" in prompt
    assert skill_body in prompt
    # Order: prefix -> skill_prompt -> history -> current_request.
    prefix_index = prompt.index("Available tools:")
    skill_index = prompt.index(skill_body)
    history_index = prompt.index("earlier question")
    request_index = prompt.index("/travel-planning 我要去莆田")
    assert prefix_index < skill_index < history_index < request_index
    # The skill_prompt section is reported in metadata.
    assert "skill_prompt" in metadata["sections"]
    assert metadata["sections"]["skill_prompt"]["rendered_chars"] > 0


def test_context_manager_omits_skill_prompt_section_when_absent() -> None:
    """Without a skill_prompt the section is absent and ordering is unchanged."""
    manager = ContextManager(today=lambda: date(2026, 7, 8))

    prompt, metadata = manager.build(
        user_message="hello",
        tools=["read_file: read a file"],
    )

    assert "<skill-instructions>" not in prompt
    assert "skill_prompt" not in metadata["sections"]


async def test_skill_prompt_injected_into_llm_request_but_not_history(tmp_path: Path) -> None:
    """A skill_prompt flows into the LLM request but is never persisted to history."""
    (tmp_path / "README.md").write_text("# Sage\n", encoding="utf-8")

    class RecordingModel:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        async def complete(self, prompt: str) -> str:
            self.prompts.append(prompt)
            return "<final>done</final>"

    model = RecordingModel()
    runtime = CodingRuntime(
        session_id="coding_skill",
        workspace_root=tmp_path,
        model=model,
        storage_root=tmp_path / ".coding",
    )

    events = [
        event
        async for event in runtime.run_turn(
            "/travel-planning 我要去莆田",
            skill_prompt="你正在使用 Sage 的 travel-planning domain skill。\n\n规划行程",
        )
    ]

    assert events[-1]["type"] == "turn_finished"
    # The skill body is in the LLM request.
    assert "你正在使用 Sage 的 travel-planning domain skill" in model.prompts[0]
    # The original command text is the persisted user message; the skill body is not.
    assert runtime.session["history"][0] == {
        "role": "user",
        "content": "/travel-planning 我要去莆田",
        "created_at": runtime.session["history"][0]["created_at"],
    }
    assert all(
        "你正在使用 Sage 的 travel-planning domain skill" not in str(item.get("content", ""))
        for item in runtime.session["history"]
    )
