"""Immutable context projection and safe prompt assembly tests."""

from copy import deepcopy
from typing import Any

import pytest

from core.coding.context import ContextLevel, ContextManager, ContextProjector


def _tool(
    name: str,
    path: str,
    content: str,
    *,
    artifact_ref: str = "",
) -> dict[str, Any]:
    return {
        "role": "tool",
        "name": name,
        "args": {"path": path},
        "content": content,
        "artifact_ref": artifact_ref,
    }


def test_projection_never_mutates_history() -> None:
    history = [
        _tool("read_file", "README.md", "old result"),
        {"role": "assistant", "content": "continue", "metadata": {"step": 1}},
        _tool("read_file", "README.md", "new result"),
        _tool("search", "core", "result 1"),
        _tool("list_files", ".", "result 2"),
        _tool("read_file", "pyproject.toml", "result 3"),
    ]
    before = deepcopy(history)

    projected = ContextProjector().project(history, level="snip")

    assert history == before
    assert projected != history
    projected[1]["metadata"]["step"] = 2
    assert history[1]["metadata"]["step"] == 1


def test_projection_keeps_latest_three_tool_results() -> None:
    history = [_tool("read_file", f"file-{index}", f"result {index}") for index in range(1, 6)]

    projected = ContextProjector().project(history, level="high")

    assert [item["content"] for item in projected if item["role"] == "tool"][-3:] == [
        "result 3",
        "result 4",
        "result 5",
    ]


@pytest.mark.parametrize(
    ("level", "cap"),
    [
        ("normal", 50_000),
        ("budget", 30_000),
        ("snip", 30_000),
        ("compact", 30_000),
        ("high", 15_000),
        ("emergency", 15_000),
    ],
)
def test_projection_applies_level_specific_tool_output_cap(level: ContextLevel, cap: int) -> None:
    history = [_tool("run_shell", "", "x" * (cap + 100), artifact_ref="call-1.txt")]

    projected = ContextProjector().project(history, level=level)

    content = projected[0]["content"]
    assert len(content) <= cap
    assert "truncated" in content
    assert "artifact_ref=call-1.txt" in content


def test_projection_does_not_mark_exact_cap_as_truncated() -> None:
    content = "x" * 50_000

    projected = ContextProjector().project(
        [_tool("run_shell", "", content, artifact_ref="call-1.txt")], level="normal"
    )

    assert projected[0]["content"] == content


@pytest.mark.parametrize("name", ["read_file", "search", "list_files"])
def test_projection_replaces_only_older_duplicate_reads(name: str) -> None:
    history = [
        _tool(name, "same/path", "old result", artifact_ref="old.txt"),
        _tool("run_shell", "", "one"),
        _tool(name, "same/path", "new result", artifact_ref="new.txt"),
        _tool("run_shell", "", "two"),
        _tool("run_shell", "", "three"),
    ]

    projected = ContextProjector().project(history, level="snip")

    assert "older duplicate result removed" in projected[0]["content"]
    assert "artifact_ref=old.txt" in projected[0]["content"]
    assert projected[2]["content"] == "new result"


def test_projection_signature_includes_tool_name_and_path() -> None:
    history = [
        _tool("read_file", "first.py", "first read"),
        _tool("read_file", "second.py", "second read"),
        _tool("search", "first.py", "same path different tool"),
        _tool("run_shell", "", "one"),
        _tool("run_shell", "", "two"),
        _tool("run_shell", "", "three"),
    ]

    projected = ContextProjector().project(history, level="compact")

    assert [item["content"] for item in projected[:3]] == [
        "first read",
        "second read",
        "same path different tool",
    ]


def test_projection_keeps_searches_with_different_patterns() -> None:
    history = [
        {
            **_tool("search", "core", "class matches"),
            "args": {"pattern": "class", "path": "core"},
        },
        {
            **_tool("search", "core", "function matches"),
            "args": {"pattern": "def", "path": "core"},
        },
        _tool("run_shell", "", "one"),
        _tool("run_shell", "", "two"),
        _tool("run_shell", "", "three"),
    ]

    projected = ContextProjector().project(history, level="compact")

    assert projected[0]["content"] == "class matches"
    assert projected[1]["content"] == "function matches"


def test_projection_keeps_reads_with_different_line_ranges() -> None:
    history = [
        {
            **_tool("read_file", "core/app.py", "first range"),
            "args": {"path": "core/app.py", "start": 1, "end": 100},
        },
        {
            **_tool("read_file", "core/app.py", "second range"),
            "args": {"path": "core/app.py", "start": 101, "end": 200},
        },
        _tool("run_shell", "", "one"),
        _tool("run_shell", "", "two"),
        _tool("run_shell", "", "three"),
    ]

    projected = ContextProjector().project(history, level="compact")

    assert projected[0]["content"] == "first range"
    assert projected[1]["content"] == "second range"


def test_projection_deduplicates_equal_args_with_different_mapping_order() -> None:
    history = [
        {**_tool("search", "core", "old"), "args": {"path": "core", "pattern": "class"}},
        {
            **_tool("search", "core", "new"),
            "args": {"pattern": "class", "path": "core"},
        },
        _tool("run_shell", "", "one"),
        _tool("run_shell", "", "two"),
        _tool("run_shell", "", "three"),
    ]

    projected = ContextProjector().project(history, level="compact")

    assert "older duplicate result removed" in projected[0]["content"]
    assert projected[1]["content"] == "new"


def test_projection_signature_preserves_scalar_types() -> None:
    history = [
        {**_tool("read_file", "core/app.py", "boolean"), "args": {"path": True}},
        {**_tool("read_file", "core/app.py", "integer"), "args": {"path": 1}},
        _tool("run_shell", "", "one"),
        _tool("run_shell", "", "two"),
        _tool("run_shell", "", "three"),
    ]

    projected = ContextProjector().project(history, level="compact")

    assert projected[0]["content"] == "boolean"
    assert projected[1]["content"] == "integer"


@pytest.mark.parametrize("name", ["write_file", "patch_file", "run_shell"])
def test_projection_never_removes_duplicate_write_or_execution_results(name: str) -> None:
    history = [
        _tool(name, "same/path", "old result"),
        _tool(name, "same/path", "new result"),
        _tool("run_shell", "", "one"),
        _tool("run_shell", "", "two"),
        _tool("run_shell", "", "three"),
    ]

    projected = ContextProjector().project(history, level="emergency")

    assert projected[0]["content"] == "old result"
    assert projected[1]["content"] == "new result"


def test_projection_only_deduplicates_at_snip_or_more_aggressive_levels() -> None:
    history = [
        _tool("read_file", "same/path", "old result"),
        _tool("read_file", "same/path", "new result"),
        _tool("run_shell", "", "one"),
        _tool("run_shell", "", "two"),
        _tool("run_shell", "", "three"),
    ]

    assert ContextProjector().project(history, level="normal")[0]["content"] == "old result"
    assert ContextProjector().project(history, level="budget")[0]["content"] == "old result"
    assert (
        "older duplicate result removed"
        in ContextProjector().project(history, level="snip")[0]["content"]
    )


def test_projection_never_clips_non_tool_messages_or_compact_summary() -> None:
    long_content = "x" * 60_000
    history = [
        {"role": "system", "content": long_content},
        {"role": "user", "content": long_content},
        {"role": "assistant", "content": long_content},
        {"role": "system", "kind": "compact_summary", "content": long_content},
        _tool("run_shell", "", "short"),
    ]

    projected = ContextProjector().project(history, level="emergency")

    assert [item["content"] for item in projected[:4]] == [long_content] * 4


def test_context_manager_does_not_clip_current_request_or_system_prefix() -> None:
    request = "current:" + "x" * 2_000
    system_prompt = "system:" + "y" * 2_000

    prompt, metadata = ContextManager(
        total_budget=1_000,
        system_prompt=system_prompt,
    ).build(request, history=[])

    assert request in prompt
    assert system_prompt in prompt
    assert (
        metadata["sections"]["prefix"]["raw_chars"]
        == metadata["sections"]["prefix"]["rendered_chars"]
    )
    assert metadata["prompt_over_budget"] is True


def test_context_manager_preserves_assistant_final_and_compact_summary() -> None:
    assistant_final = "ASSISTANT_FINAL_SENTINEL:" + "f" * 2_000
    compact_summary = "COMPACT_SUMMARY_SENTINEL:" + "s" * 2_000
    history = [
        {"role": "assistant", "content": assistant_final},
        {"role": "system", "kind": "compact_summary", "content": compact_summary},
    ]

    prompt, metadata = ContextManager(total_budget=500).build("current request", history=history)

    assert assistant_final in prompt
    assert compact_summary in prompt
    assert (
        metadata["sections"]["history"]["rendered_chars"]
        == metadata["sections"]["history"]["raw_chars"]
    )
    assert metadata["prompt_over_budget"] is True
