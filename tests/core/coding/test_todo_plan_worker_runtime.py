"""Coding runtime assembly tests for todo, plan mode, workers, and persistence."""

from pathlib import Path

from core.coding.context import WorkspaceContext
from core.coding.multiagent import WorkerManager
from core.coding.persistence import TodoLedger
from core.coding.runtime import CodingRuntime


class FakeModel:
    """Thread-safe enough fake model for deterministic tests."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)

    async def complete(self, prompt: str) -> str:
        _ = prompt
        return self.responses.pop(0)


def test_todo_ledger_add_list_update() -> None:
    """Todo ledger supports add, list, and status updates."""
    ledger = TodoLedger()

    item = ledger.add("实现 parser", priority="high")
    ledger.update(item["id"], status="in_progress")

    rendered = ledger.render_list()
    assert item["id"] == "todo_1"
    assert "[in_progress]" in rendered
    assert "实现 parser" in rendered


def test_plan_mode_creates_plan_path_and_restricts_runtime(tmp_path: Path) -> None:
    """Plan mode toggles runtime state and exposes a plan artifact path."""
    runtime = CodingRuntime(
        session_id="s-plan",
        workspace_root=tmp_path,
        model=FakeModel(["<final>noop</final>"]),
        storage_root=tmp_path / ".coding",
    )

    plan_path = runtime.enter_plan_mode("Refactor API")
    write_decision = runtime.permission_checker.check(
        runtime.tools["write_file"],
        {"path": "app.py"},
        runtime.workspace,
    )
    read_decision = runtime.permission_checker.check(
        runtime.tools["read_file"],
        {"path": "README.md"},
        runtime.workspace,
    )
    runtime.exit_plan_mode()

    assert plan_path == ".coding/plans/refactor-api-plan.md"
    assert runtime.runtime_mode == "default"
    assert write_decision.allowed is False
    assert read_decision.allowed is True


def test_worker_manager_runs_worker_and_drains_notification(tmp_path: Path) -> None:
    """A worker can run a read-only task and notify the coordinator."""
    (tmp_path / "README.md").write_text("TourSwarm worker\n", encoding="utf-8")
    workspace = WorkspaceContext(root=tmp_path)
    manager = WorkerManager(
        workspace=workspace,
        model_factory=lambda: FakeModel(
            [
                '<tool>{"name":"read_file","args":{"path":"README.md"}}</tool>',
                "<final>Read README.</final>",
            ]
        ),
    )

    payload = manager.spawn(
        description="read README",
        prompt="读 README",
        subagent_type="Explore",
        write_scope=[],
    )
    manager.wait(payload["task_id"], timeout=5)
    notifications = manager.drain_notifications()

    assert payload["status"] == "started"
    assert notifications
    assert "Read README." in notifications[0]


async def test_runtime_persists_session_events_and_run_trace(tmp_path: Path) -> None:
    """A full runtime turn writes session JSON, session events, and run trace."""
    (tmp_path / "README.md").write_text("TourSwarm runtime\n", encoding="utf-8")
    runtime = CodingRuntime(
        session_id="s-runtime",
        workspace_root=tmp_path,
        model=FakeModel(
            [
                '<tool>{"name":"read_file","args":{"path":"README.md"}}</tool>',
                "<final>项目是 TourSwarm runtime。</final>",
            ]
        ),
        storage_root=tmp_path / ".coding",
    )

    events = [event async for event in runtime.run_turn("读 README")]

    assert [event["type"] for event in events][-3:] == ["final", "run_finished", "turn_finished"]
    assert (tmp_path / ".coding" / "sessions" / "s-runtime.json").is_file()
    assert (tmp_path / ".coding" / "sessions" / "s-runtime.events.jsonl").is_file()
    # Session-partitioned runs live under evidence/<session_id>/runs.
    run_dirs = list((tmp_path / ".coding" / "runs" / "evidence" / "s-runtime" / "runs").iterdir())
    assert run_dirs
    assert (run_dirs[0] / "trace.jsonl").is_file()


def test_runtime_resumes_persisted_session_state(tmp_path: Path) -> None:
    """A saved runtime can be rehydrated for a later WebSocket connection."""
    original = CodingRuntime(
        session_id="s-resume",
        workspace_root=tmp_path,
        model=FakeModel(["<final>noop</final>"]),
        storage_root=tmp_path / ".coding",
    )
    original.session["history"].append(
        {"role": "user", "content": "继续看 README", "created_at": "2026-07-08T10:00:00"}
    )
    original.todo_ledger.add("补 resume 测试", priority="high")
    original.enter_plan_mode("Resume runtime")

    resumed = CodingRuntime.resume(
        session_id="s-resume",
        model=FakeModel(["<final>resumed</final>"]),
        storage_root=tmp_path / ".coding",
    )

    assert resumed.session_id == "s-resume"
    assert resumed.workspace.root == tmp_path
    assert resumed.session["history"][0]["content"] == "继续看 README"
    assert "补 resume 测试" in resumed.todo_ledger.render_list()
    assert resumed.runtime_mode == "plan"
    assert resumed.permission_checker.plan_mode is True


def test_runtime_resume_does_not_touch_updated_at(tmp_path: Path) -> None:
    """Opening a saved session should not reorder it before a new turn runs."""
    original = CodingRuntime(
        session_id="s-resume-order",
        workspace_root=tmp_path,
        model=FakeModel(["<final>noop</final>"]),
        storage_root=tmp_path / ".coding",
    )
    original.session["updated_at"] = "2026-07-08T09:10:00"
    original.session_store.save(original.session)

    CodingRuntime.resume(
        session_id="s-resume-order",
        model=FakeModel(["<final>resumed</final>"]),
        storage_root=tmp_path / ".coding",
    )

    saved = original.session_store.load("s-resume-order")
    assert saved["updated_at"] == "2026-07-08T09:10:00"


async def test_runtime_persists_activated_deferred_tools(tmp_path: Path) -> None:
    """tool_search activations are session-scoped and survive runtime resume."""
    original = CodingRuntime(
        session_id="s-tool-search",
        workspace_root=tmp_path,
        model=FakeModel(
            [
                '<tool>{"name":"tool_search","args":{"query":"todo"}}</tool>',
                "<final>todo tools activated</final>",
            ]
        ),
        storage_root=tmp_path / ".coding",
    )

    events = [event async for event in original.run_turn("启用 todo 工具")]
    resumed = CodingRuntime.resume(
        session_id="s-tool-search",
        model=FakeModel(["<final>resumed</final>"]),
        storage_root=tmp_path / ".coding",
    )

    assert events[-1]["type"] == "turn_finished"
    assert "todo_add" in original.activated_tools
    assert "todo_update" in original.session["activated_tools"]
    assert "todo_add" in resumed.activated_tools
    assert "todo_add" in resumed.session["activated_tools"]


async def test_run_turn_emits_runtime_mode_changed_on_plan_entry(tmp_path: Path) -> None:
    """Entering plan mode mid-turn yields a runtime_mode_changed event to the stream."""
    (tmp_path / "README.md").write_text("TourSwarm plan\n", encoding="utf-8")
    runtime = CodingRuntime(
        session_id="s-plan-stream",
        workspace_root=tmp_path,
        model=FakeModel(
            [
                # Activate the deferred enter_plan_mode tool via tool_search.
                '<tool>{"name":"tool_search","args":{"query":"plan"}}</tool>',
                # Call it to switch the runtime into plan mode.
                '<tool>{"name":"enter_plan_mode","args":{"topic":"Refactor API"}}</tool>',
                "<final>Planning started.</final>",
            ]
        ),
        storage_root=tmp_path / ".coding",
    )

    events = [event async for event in runtime.run_turn("进入规划模式")]

    types = [event["type"] for event in events]
    assert "runtime_mode_changed" in types

    # The mode change should be surfaced right after the enter_plan_mode tool
    # result, carrying the plan-mode payload to the frontend.
    mode_idx = types.index("runtime_mode_changed")
    mode_event = events[mode_idx]
    assert mode_event["mode"] == "plan"
    assert mode_event["topic"] == "Refactor API"
    assert mode_event["plan_path"] == ".coding/plans/refactor-api-plan.md"
    assert mode_event["run_id"]

    # The preceding event must be the enter_plan_mode tool result.
    assert types[mode_idx - 1] == "tool_result"
    assert events[mode_idx - 1]["tool"] == "enter_plan_mode"

    # Final answer still lands near the end of the turn (followed by
    # run_finished + turn_finished terminal events).
    assert "final" in types
    assert types[-2:] == ["run_finished", "turn_finished"]
    assert runtime.runtime_mode == "plan"


async def test_run_turn_emits_plan_ready_for_review_on_plan_exit(tmp_path: Path) -> None:
    """exit_plan_mode submits the plan for review instead of leaving plan mode.

    V5.1: exit_plan_mode no longer flips the mode directly. It yields a
    plan_ready_for_review event and the runtime stays in plan mode until the
    user approves via approve_plan().
    """
    runtime = CodingRuntime(
        session_id="s-plan-exit",
        workspace_root=tmp_path,
        model=FakeModel(
            [
                # Activate exit_plan_mode (deferred) via tool_search.
                '<tool>{"name":"tool_search","args":{"query":"plan"}}</tool>',
                # Call it to request leaving plan mode.
                '<tool>{"name":"exit_plan_mode","args":{}}</tool>',
                "<final>Plan ready for your review.</final>",
            ]
        ),
        storage_root=tmp_path / ".coding",
    )
    # Start the session already in plan mode with a plan file on disk.
    plan_path = runtime.enter_plan_mode("Refactor API")
    (tmp_path / plan_path).write_text("# Refactor plan\nstep 1\n", encoding="utf-8")

    events = [event async for event in runtime.run_turn("退出规划模式")]

    types = [event["type"] for event in events]
    # No direct mode change: the runtime stays in plan mode pending review.
    assert "runtime_mode_changed" not in types
    assert "plan_ready_for_review" in types

    review_idx = types.index("plan_ready_for_review")
    review_event = events[review_idx]
    assert review_event["plan_path"] == ".coding/plans/refactor-api-plan.md"
    assert "# Refactor plan" in review_event["summary"]
    assert review_event["review_id"]
    assert review_event["run_id"]

    # The review is surfaced right after the exit_plan_mode tool result.
    assert types[review_idx - 1] == "tool_result"
    assert events[review_idx - 1]["tool"] == "exit_plan_mode"

    assert events[-1]["type"] == "turn_finished"
    # Still in plan mode until the user approves.
    assert runtime.runtime_mode == "plan"
    assert runtime.plan_review_manager.pending is not None

    # Approving via the runtime flips the mode to default.
    approved = runtime.approve_plan()
    assert approved is True
    assert runtime.runtime_mode == "default"
    assert runtime.plan_review_manager.pending is None
