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
            "changed_files": [],
            "audit": {
                "run_id": "run_a",
                "status": "completed",
                "headline": "运行完成 · 1 项工具",
                "tool_count": 1,
                "completed_tool_count": 1,
                "failed_tool_count": 0,
                "approval_count": 0,
                "duration_ms": 0,
                "changed_files": [],
                "steps": [
                    {
                        "tool": "read_file",
                        "status": "completed",
                        "action_summary": "读取文件",
                        "result_summary": "执行完成",
                        "duration_ms": 0,
                        "arguments_preview": "{}",
                        "result_preview": "已读取文件内容（摘要不展示正文）",
                        "arguments_truncated": False,
                        "result_truncated": False,
                    }
                ],
            },
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
    assert detail["audit"]["status"] == "cancelled"


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
    store_a = RunStore(tmp_path, session_id="session_a")
    store_b = RunStore(tmp_path, session_id="session_b")

    store_a.start_run("run_a1")
    store_a.append_trace("run_a1", {"type": "final", "content": "a done"})

    store_b.start_run("run_b1")
    store_b.append_trace("run_b1", {"type": "final", "content": "b done"})

    # Each store only sees its own runs.
    assert [run["run_id"] for run in store_a.list_runs()] == ["run_a1"]
    assert [run["run_id"] for run in store_b.list_runs()] == ["run_b1"]

    # The partitions are physically separate directories.
    assert (tmp_path / "evidence" / "session_a" / "runs" / "run_a1").is_dir()
    assert (tmp_path / "evidence" / "session_b" / "runs" / "run_b1").is_dir()

    # A run from the other session is not visible.
    import pytest

    with pytest.raises(FileNotFoundError):
        store_a.get_run("run_b1")


def test_run_store_global_store_can_inspect_session_partition(tmp_path: Path) -> None:
    """A session-less store can read a specific session partition via session_id."""
    scoped = RunStore(tmp_path, session_id="session_a")
    scoped.start_run("run_a1")
    scoped.append_trace("run_a1", {"type": "final", "content": "a done"})

    global_store = RunStore(tmp_path)
    # Global store sees nothing by default.
    assert global_store.list_runs() == []
    # But can inspect session_a's partition by passing session_id.
    assert [run["run_id"] for run in global_store.list_runs(session_id="session_a")] == ["run_a1"]
    assert global_store.get_run("run_a1", session_id="session_a")["run_id"] == "run_a1"


def test_run_store_backward_compat_global_when_no_session_id(tmp_path: Path) -> None:
    """Without session_id the store uses the legacy global runs/ layout.

    A session-less store keeps runs under ``root / "runs"`` so existing
    deployments that read ``.coding/runs/<run_id>`` keep working.
    """
    store = RunStore(tmp_path)
    store.start_run("run_x")
    store.append_trace("run_x", {"type": "final", "content": "done"})

    # Trace lands under tmp_path/runs/run_x (the global runs/ directory), not
    # under evidence/...
    assert (tmp_path / "runs" / "run_x" / "trace.jsonl").is_file()
    assert [run["run_id"] for run in store.list_runs()] == ["run_x"]


def test_run_status_from_run_finished(tmp_path: Path) -> None:
    """run_status reads the terminal status from the run_finished event."""
    store = RunStore(tmp_path, session_id="s1")
    store.start_run("run_ok")
    store.append_trace("run_ok", {"type": "final", "content": "done"})
    store.append_trace("run_ok", {"type": "run_finished", "status": "completed", "duration_ms": 42})

    store.start_run("run_err")
    store.append_trace("run_err", {"type": "error", "message": "boom"})
    store.append_trace("run_err", {"type": "run_finished", "status": "error", "duration_ms": 5})

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


def test_run_audit_projects_tools_approval_duration_and_workspace_diff(tmp_path: Path) -> None:
    """Audit summary pairs repeated tools by order and keeps one bounded run projection."""
    store = RunStore(tmp_path)
    store.start_run("run_a")
    store.append_trace(
        "run_a",
        {"type": "turn_started", "created_at": "2026-07-14T10:00:00+00:00"},
    )
    store.append_trace(
        "run_a",
        {
            "type": "approval_required",
            "tool": "run_shell",
            "args": {"command": "pytest -q", "timeout": 30},
            "created_at": "2026-07-14T10:00:01+00:00",
        },
    )
    store.append_trace(
        "run_a",
        {
            "type": "approval_granted",
            "tool": "run_shell",
            "created_at": "2026-07-14T10:00:02+00:00",
        },
    )
    store.append_trace(
        "run_a",
        {
            "type": "tool_call",
            "tool": "run_shell",
            "args": {"command": "pytest -q", "timeout": 30},
            "created_at": "2026-07-14T10:00:03+00:00",
        },
    )
    store.append_trace(
        "run_a",
        {
            "type": "tool_result",
            "tool": "run_shell",
            "args": {"command": "pytest -q", "timeout": 30},
            "content": "exit_code: 0\nstdout:\n12 passed\nstderr:\n(empty)",
            "is_error": False,
            "created_at": "2026-07-14T10:00:07.250000+00:00",
        },
    )
    store.append_trace(
        "run_a",
        {
            "type": "tool_call",
            "tool": "run_shell",
            "args": {"command": "ruff check ."},
            "created_at": "2026-07-14T10:00:08+00:00",
        },
    )
    store.append_trace(
        "run_a",
        {
            "type": "tool_result",
            "tool": "run_shell",
            "args": {"command": "ruff check ."},
            "content": "exit_code: 1\nstdout:\n(empty)\nstderr:\nsyntax error",
            "is_error": True,
            "created_at": "2026-07-14T10:00:09+00:00",
        },
    )
    store.append_trace(
        "run_a",
        {"type": "workspace_diff_ready", "changed_files": ["a.py", "b.py"]},
    )
    store.append_trace(
        "run_a",
        {"type": "run_finished", "status": "error", "duration_ms": 9500},
    )

    audit = store.get_run("run_a")["audit"]

    assert audit == {
        "run_id": "run_a",
        "status": "error",
        "headline": "运行失败 · 2 项工具 · 修改 2 个文件",
        "tool_count": 2,
        "completed_tool_count": 1,
        "failed_tool_count": 1,
        "approval_count": 1,
        "duration_ms": 9500,
        "changed_files": ["a.py", "b.py"],
        "steps": [
            {
                "tool": "run_shell",
                "status": "completed",
                "action_summary": "执行 pytest -q",
                "result_summary": "退出码 0",
                "duration_ms": 4250,
                "arguments_preview": '{"command":"pytest -q","timeout":30}',
                "result_preview": "exit_code: 0\nstdout:\n12 passed\nstderr:\n(empty)",
                "arguments_truncated": False,
                "result_truncated": False,
            },
            {
                "tool": "run_shell",
                "status": "error",
                "action_summary": "执行 ruff check .",
                "result_summary": "退出码 1 · 执行失败",
                "duration_ms": 1000,
                "arguments_preview": '{"command":"ruff check ."}',
                "result_preview": "exit_code: 1\nstdout:\n(empty)\nstderr:\nsyntax error",
                "arguments_truncated": False,
                "result_truncated": False,
            },
        ],
    }


def test_run_audit_bounds_and_redacts_sensitive_previews(tmp_path: Path) -> None:
    """Audit previews never expose credential-shaped fields or unbounded output."""
    store = RunStore(tmp_path)
    store.start_run("run_secret")
    store.append_trace(
        "run_secret",
        {
            "type": "tool_call",
            "tool": "run_shell",
            "args": {
                "command": "curl -H 'Authorization: Bearer top-secret' https://example.com",
                "api_key": "plain-secret",
            },
        },
    )
    store.append_trace(
        "run_secret",
        {
            "type": "tool_result",
            "tool": "run_shell",
            "args": {"command": "curl https://example.com"},
            "content": "OPENAI_API_KEY=plain-secret\n" + ("x" * 6000) + "\nBearer another-secret",
            "is_error": False,
        },
    )

    step = store.get_run("run_secret")["audit"]["steps"][0]
    combined = f'{step["arguments_preview"]}\n{step["result_preview"]}'

    assert "top-secret" not in combined
    assert "plain-secret" not in combined
    assert "another-secret" not in combined
    assert "[REDACTED]" in combined
    assert step["result_truncated"] is True
    assert "省略" in step["result_preview"]
    assert len(step["result_preview"].encode("utf-8")) <= 4096


def test_run_audit_omits_read_file_body_and_projects_orphan_result(tmp_path: Path) -> None:
    """Denied/invalid tools may have only a result and still produce an auditable step."""
    store = RunStore(tmp_path)
    store.start_run("run_denied")
    store.append_trace(
        "run_denied",
        {
            "type": "tool_result",
            "tool": "read_file",
            "args": {"path": ".env"},
            "content": "API_KEY=must-not-leak",
            "is_error": True,
            "policy_reason": "path_not_allowed",
        },
    )

    step = store.get_run("run_denied")["audit"]["steps"][0]

    assert step["tool"] == "read_file"
    assert step["status"] == "error"
    assert step["arguments_preview"] == '{"path":".env"}'
    assert step["result_preview"] == "已读取文件内容（摘要不展示正文）"
    assert "must-not-leak" not in str(store.get_run("run_denied")["audit"])


def test_run_audit_attaches_late_approval_to_existing_tool_step(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    store.start_run("run_approval")
    store.append_trace(
        "run_approval",
        {
            "type": "tool_call",
            "tool": "run_shell",
            "args": {},
            "tool_call_id": "call-1",
            "created_at": "2026-07-14T10:00:00+00:00",
        },
    )
    store.append_trace(
        "run_approval",
        {
            "type": "approval_required",
            "tool": "run_shell",
            "args": {"command": "pwd"},
            "tool_call_id": "call-1",
            "created_at": "2026-07-14T10:00:01+00:00",
        },
    )
    store.append_trace(
        "run_approval",
        {
            "type": "approval_granted",
            "tool": "run_shell",
            "tool_call_id": "call-1",
            "created_at": "2026-07-14T10:00:05+00:00",
        },
    )
    store.append_trace(
        "run_approval",
        {
            "type": "tool_result",
            "tool": "run_shell",
            "args": {"command": "pwd"},
            "tool_call_id": "call-1",
            "content": "exit_code: 0\n/workspace",
            "is_error": False,
            "created_at": "2026-07-14T10:00:06.700000+00:00",
        },
    )

    audit = store.get_run("run_approval")["audit"]

    assert audit["tool_count"] == 1
    assert audit["approval_count"] == 1
    assert audit["steps"][0]["status"] == "completed"
    assert audit["steps"][0]["action_summary"] == "执行 pwd"
    assert audit["steps"][0]["arguments_preview"] == '{"command":"pwd"}'
    assert audit["steps"][0]["duration_ms"] == 1700
