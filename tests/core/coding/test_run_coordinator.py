from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from core.coding.persistence.session_event_journal import SessionEventJournal
from core.coding.run_coordinator import ActiveRunConflictError, RunCoordinator, RunEvent


async def _events(*events: RunEvent):
    for event in events:
        yield event


@pytest.mark.asyncio
async def test_subscribe_bridges_history_and_live_without_gap_or_duplicate(tmp_path: Path) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    journal.append(run_id="old", kind="user", status="completed", payload={"content": "old"})
    coordinator = RunCoordinator(journal)
    release = asyncio.Event()

    async def stream():
        yield RunEvent(kind="tool", status="running", payload={"tool": "read_file"})
        await release.wait()
        yield RunEvent(kind="assistant", status="completed", payload={"content": "done"})

    subscription = coordinator.subscribe(after=0)
    first = await anext(subscription)
    task = await coordinator.start_run("run-1", stream())
    second = await anext(subscription)
    release.set()
    third = await anext(subscription)
    fourth = await anext(subscription)
    await task
    await subscription.aclose()

    sequences = [first.sequence, second.sequence, third.sequence, fourth.sequence]
    assert sequences == sorted(set(sequences))
    assert [first.payload, second.payload, third.payload, fourth.payload] == [
        {"content": "old"},
        {"event": "run_started"},
        {"tool": "read_file"},
        {"content": "done"},
    ]


@pytest.mark.asyncio
async def test_closing_subscription_does_not_cancel_run(tmp_path: Path) -> None:
    coordinator = RunCoordinator(SessionEventJournal(tmp_path, "session-1"))
    release = asyncio.Event()

    async def stream():
        yield RunEvent(kind="tool", status="running", payload={})
        await release.wait()
        yield RunEvent(kind="assistant", status="completed", payload={})

    subscription = coordinator.subscribe(after=0)
    pending = asyncio.create_task(anext(subscription))
    task = await coordinator.start_run("run-1", stream())
    await pending
    await subscription.aclose()
    release.set()
    await task

    assert coordinator.active_run_id is None
    statuses = [item.status for item in coordinator.journal.replay(after=0, limit=20).items]
    assert statuses[-1] == "completed"


@pytest.mark.asyncio
async def test_only_one_active_run_per_session(tmp_path: Path) -> None:
    coordinator = RunCoordinator(SessionEventJournal(tmp_path, "session-1"))
    release = asyncio.Event()

    async def blocked():
        await release.wait()
        if False:
            yield RunEvent(kind="tool", status="running", payload={})

    first = await coordinator.start_run("run-1", blocked())
    with pytest.raises(ActiveRunConflictError, match="run-1"):
        await coordinator.start_run("run-2", _events())
    release.set()
    await first


@pytest.mark.asyncio
async def test_cancel_token_cancels_task_and_persists_one_terminal(tmp_path: Path) -> None:
    coordinator = RunCoordinator(SessionEventJournal(tmp_path, "session-1"))
    entered = asyncio.Event()

    async def blocked():
        entered.set()
        await asyncio.Event().wait()
        if False:
            yield RunEvent(kind="tool", status="running", payload={})

    task = await coordinator.start_run("run-1", blocked())
    await entered.wait()
    assert await coordinator.cancel("run-1") is True
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await coordinator.cancel("run-1") is False

    events = coordinator.journal.replay(after=0, limit=20).items
    terminal = [item for item in events if item.status in {"completed", "cancelled", "error", "interrupted"}]
    assert [item.status for item in terminal] == ["cancelled"]


@pytest.mark.asyncio
async def test_immediate_cancel_still_closes_run(tmp_path: Path) -> None:
    coordinator = RunCoordinator(SessionEventJournal(tmp_path, "session-1"))

    async def never_started():
        await asyncio.Event().wait()
        if False:
            yield RunEvent(kind="tool", status="running", payload={})

    task = await coordinator.start_run("run-1", never_started())
    assert await coordinator.cancel("run-1") is True
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)

    assert coordinator.active_run_id is None
    events = coordinator.journal.replay(after=0, limit=20).items
    assert [(item.kind, item.status) for item in events] == [
        ("system", "running"),
        ("terminal", "cancelled"),
    ]


@pytest.mark.asyncio
async def test_duplicate_stream_terminal_is_persisted_once(tmp_path: Path) -> None:
    coordinator = RunCoordinator(SessionEventJournal(tmp_path, "session-1"))
    task = await coordinator.start_run(
        "run-1",
        _events(
            RunEvent(kind="terminal", status="completed", payload={"answer": "first"}),
            RunEvent(kind="terminal", status="error", payload={"error": "late"}),
        ),
    )
    await task

    terminal = [
        item
        for item in coordinator.journal.replay(after=0, limit=20).items
        if item.kind == "terminal"
    ]
    assert len(terminal) == 1
    assert terminal[0].payload == {"answer": "first"}


@pytest.mark.asyncio
async def test_nonterminal_tool_error_does_not_end_run(tmp_path: Path) -> None:
    coordinator = RunCoordinator(SessionEventJournal(tmp_path, "session-1"))
    task = await coordinator.start_run(
        "run-1",
        _events(
            RunEvent(kind="tool", status="error", payload={"error": "recoverable"}),
            RunEvent(kind="assistant", status="done", payload={"answer": "recovered"}),
        ),
    )
    await task

    events = coordinator.journal.replay(after=0, limit=20).items
    assert [(item.kind, item.status) for item in events] == [
        ("system", "running"),
        ("tool", "error"),
        ("assistant", "done"),
        ("terminal", "completed"),
    ]


@pytest.mark.asyncio
async def test_terminal_stops_consuming_blocked_upstream(tmp_path: Path) -> None:
    coordinator = RunCoordinator(SessionEventJournal(tmp_path, "session-1"))

    async def terminal_then_block():
        yield RunEvent(kind="terminal", status="completed", payload={"answer": "done"})
        await asyncio.Event().wait()

    task = await coordinator.start_run("run-1", terminal_then_block())
    await asyncio.wait_for(task, timeout=0.5)

    assert coordinator.active_run_id is None


@pytest.mark.asyncio
async def test_cancel_waits_for_inflight_append_before_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    coordinator = RunCoordinator(journal)
    release_stream = asyncio.Event()
    append_started = threading.Event()
    allow_append = threading.Event()
    append_finished = threading.Event()

    async def stream():
        await release_stream.wait()
        yield RunEvent(kind="tool", status="running", payload={"marker": "inflight"})
        await asyncio.Event().wait()

    task = await coordinator.start_run("run-1", stream())
    original_append = journal.append

    def blocked_append(**values: object):
        append_started.set()
        assert allow_append.wait(timeout=2)
        try:
            return original_append(**values)
        finally:
            append_finished.set()

    monkeypatch.setattr(journal, "append", blocked_append)
    release_stream.set()
    assert await asyncio.to_thread(append_started.wait, 1)

    cancel_task = asyncio.create_task(coordinator.cancel("run-1"))
    await asyncio.sleep(0.05)
    allow_append.set()
    assert await cancel_task is True
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await asyncio.to_thread(append_finished.wait, 1)

    events = journal.replay(after=0, limit=20).items
    assert [(item.kind, item.status) for item in events][-2:] == [
        ("tool", "running"),
        ("terminal", "cancelled"),
    ]


@pytest.mark.asyncio
async def test_restart_marks_unfinished_run_interrupted_and_retryable(tmp_path: Path) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    journal.append(
        run_id="abandoned", kind="system", status="running", payload={"event": "run_started"}
    )
    coordinator = RunCoordinator(SessionEventJournal(tmp_path, "session-1"))

    recovered = await coordinator.recover_interrupted_runs()
    repeated = await coordinator.recover_interrupted_runs()

    assert recovered == ("abandoned",)
    assert repeated == ()
    terminal = coordinator.journal.replay(after=0, limit=20).items[-1]
    assert terminal.status == "interrupted"
    assert terminal.payload == {"event": "run_interrupted", "retryable": True}
