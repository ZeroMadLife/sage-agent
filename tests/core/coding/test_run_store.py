"""Run trace store tests."""

from pathlib import Path

from core.coding.persistence import RunStore


def test_run_store_lists_run_summaries_from_trace(tmp_path: Path) -> None:
    """RunStore summarizes trace files for UI run history."""
    store = RunStore(tmp_path)
    store.start_run("run_a")
    store.append_trace("run_a", {"type": "model_requested", "created_at": "2026-07-08T10:00:00"})
    store.append_trace("run_a", {"type": "tool_call", "tool": "read_file"})
    store.append_trace("run_a", {"type": "tool_result", "tool": "read_file", "is_error": False})
    store.append_trace("run_a", {"type": "final", "content": "done"})

    summaries = store.list_runs()

    assert summaries == [
        {
            "run_id": "run_a",
            "status": "completed",
            "event_count": 4,
            "tool_count": 1,
            "error_count": 0,
            "last_event_type": "final",
            "started_at": "2026-07-08T10:00:00",
            "updated_at": "2026-07-08T10:00:00",
        }
    ]


def test_run_store_reads_trace_events(tmp_path: Path) -> None:
    """RunStore can return the full trace for a run."""
    store = RunStore(tmp_path)
    store.start_run("run_a")
    store.append_trace("run_a", {"type": "cancelled", "content": "stopped"})

    detail = store.get_run("run_a")

    assert detail["run_id"] == "run_a"
    assert detail["events"] == [{"type": "cancelled", "content": "stopped"}]


def test_run_store_builds_readable_timeline_from_trace(tmp_path: Path) -> None:
    """Run detail includes a UI-ready worklog timeline instead of raw event names only."""
    store = RunStore(tmp_path)
    store.start_run("run_a")
    store.append_trace(
        "run_a",
        {
            "type": "model_requested",
            "attempts": 1,
            "tool_steps": 0,
            "prompt_chars": 1200,
            "created_at": "2026-07-08T10:00:00",
        },
    )
    store.append_trace("run_a", {"type": "model_parsed", "kind": "tool"})
    store.append_trace(
        "run_a", {"type": "tool_call", "tool": "read_file", "args": {"path": "README.md"}}
    )
    store.append_trace(
        "run_a",
        {
            "type": "tool_result",
            "tool": "read_file",
            "args": {"path": "README.md"},
            "content": "# Sage\nA personal coding agent.",
            "is_error": False,
        },
    )
    store.append_trace("run_a", {"type": "final", "content": "README 总结完成。"})

    detail = store.get_run("run_a")

    assert detail["timeline"] == [
        {
            "kind": "model",
            "title": "Model request",
            "detail": "attempt 1 · step 0 · 1200 chars",
            "status": "running",
            "tool": "",
            "timestamp": "2026-07-08T10:00:00",
        },
        {
            "kind": "model",
            "title": "Parsed tool",
            "detail": "",
            "status": "done",
            "tool": "",
            "timestamp": "",
        },
        {
            "kind": "tool",
            "title": "Run read_file",
            "detail": "path=README.md",
            "status": "running",
            "tool": "read_file",
            "timestamp": "",
        },
        {
            "kind": "result",
            "title": "read_file succeeded",
            "detail": "# Sage\nA personal coding agent.",
            "status": "done",
            "tool": "read_file",
            "timestamp": "",
        },
        {
            "kind": "final",
            "title": "Final answer",
            "detail": "README 总结完成。",
            "status": "done",
            "tool": "",
            "timestamp": "",
        },
    ]


def test_run_store_session_partitioned(tmp_path: Path) -> None:
    """Two sessions' runs live in separate partitions and never cross-list."""
    store_a = RunStore(tmp_path / "runs", session_id="session_a")
    store_b = RunStore(tmp_path / "runs", session_id="session_b")

    store_a.start_run("run_a1")
    store_a.append_trace("run_a1", {"type": "final", "content": "a done"})

    store_b.start_run("run_b1")
    store_b.append_trace("run_b1", {"type": "final", "content": "b done"})

    # Each store only sees its own runs.
    assert [run["run_id"] for run in store_a.list_runs()] == ["run_a1"]
    assert [run["run_id"] for run in store_b.list_runs()] == ["run_b1"]

    # The partitions are physically separate directories.
    assert (tmp_path / "runs" / "evidence" / "session_a" / "runs" / "run_a1").is_dir()
    assert (tmp_path / "runs" / "evidence" / "session_b" / "runs" / "run_b1").is_dir()

    # A run from the other session is not visible.
    import pytest

    with pytest.raises(FileNotFoundError):
        store_a.get_run("run_b1")


def test_run_store_global_store_can_inspect_session_partition(tmp_path: Path) -> None:
    """A session-less store can read a specific session partition via session_id."""
    scoped = RunStore(tmp_path / "runs", session_id="session_a")
    scoped.start_run("run_a1")
    scoped.append_trace("run_a1", {"type": "final", "content": "a done"})

    global_store = RunStore(tmp_path / "runs")
    # Global store sees nothing by default.
    assert global_store.list_runs() == []
    # But can inspect session_a's partition by passing session_id.
    assert [run["run_id"] for run in global_store.list_runs(session_id="session_a")] == [
        "run_a1"
    ]
    assert global_store.get_run("run_a1", session_id="session_a")["run_id"] == "run_a1"


def test_run_store_backward_compat_global_when_no_session_id(tmp_path: Path) -> None:
    """Without session_id the store uses the legacy flat root layout."""
    store = RunStore(tmp_path)
    store.start_run("run_x")
    store.append_trace("run_x", {"type": "final", "content": "done"})

    # Trace lands directly under tmp_path/run_x, not under evidence/...
    assert (tmp_path / "run_x" / "trace.jsonl").is_file()
    assert [run["run_id"] for run in store.list_runs()] == ["run_x"]


def test_run_status_from_run_finished(tmp_path: Path) -> None:
    """run_status reads the terminal status from the run_finished event."""
    store = RunStore(tmp_path, session_id="s1")
    store.start_run("run_ok")
    store.append_trace("run_ok", {"type": "final", "content": "done"})
    store.append_trace(
        "run_ok", {"type": "run_finished", "status": "completed", "duration_ms": 42}
    )

    store.start_run("run_err")
    store.append_trace("run_err", {"type": "error", "message": "boom"})
    store.append_trace(
        "run_err", {"type": "run_finished", "status": "error", "duration_ms": 5}
    )

    assert store.run_status("run_ok") == "completed"
    assert store.run_status("run_err") == "error"
    # Unknown run returns "unknown".
    assert store.run_status("run_missing") == "unknown"


def test_run_tool_count(tmp_path: Path) -> None:
    """run_tool_count counts tool_result events in a run trace."""
    store = RunStore(tmp_path, session_id="s1")
    store.start_run("run_a")
    store.append_trace("run_a", {"type": "tool_call", "tool": "read_file"})
    store.append_trace("run_a", {"type": "tool_result", "tool": "read_file"})
    store.append_trace("run_a", {"type": "tool_call", "tool": "write_file"})
    store.append_trace("run_a", {"type": "tool_result", "tool": "write_file"})
    store.append_trace("run_a", {"type": "final", "content": "done"})

    # tool_result count, not tool_call count.
    assert store.run_tool_count("run_a") == 2
    # A run with only tool_call events has zero tool_results.
    store.start_run("run_b")
    store.append_trace("run_b", {"type": "tool_call", "tool": "read_file"})
    assert store.run_tool_count("run_b") == 0
    # Missing run directory returns 0.
    assert store.run_tool_count("run_missing") == 0
