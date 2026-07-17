from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path

import pytest

from core.coding.persistence import session_event_journal as journal_module
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
async def test_suspend_and_resume_existing_run_preserves_one_start_event(
    tmp_path: Path,
) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    first = RunCoordinator(journal, owner_id="first-owner")
    release = asyncio.Event()

    async def blocked():
        yield RunEvent(
            kind="approval",
            status="blocked",
            payload={
                "type": "approval_required",
                "runtime_profile": "deerflow_v2",
                "approval_id": "appr-1",
                "tool": "write_file",
                "tool_call_id": "call-1",
                "args_digest": "a" * 64,
            },
        )
        await release.wait()

    task = await first.start_run("run-1", blocked())
    while journal.recoverable_approval("run-1") is None:
        await asyncio.sleep(0)
    assert await first.suspend_for_restart("run-1") is True
    assert task.cancelled()
    assert journal.active_run_id() is None

    second = RunCoordinator(
        SessionEventJournal(tmp_path, "session-1"),
        owner_id="second-owner",
    )
    with pytest.raises(ActiveRunConflictError, match="resumable approval"):
        await second.start_run("run-2", _events())
    resumed = await second.start_existing_run(
        "run-1",
        _events(RunEvent(kind="assistant", status="completed", payload={"content": "done"})),
    )
    await resumed

    events = journal.replay(after=0, limit=20).items
    assert [event.payload.get("event") for event in events].count("run_started") == 1
    assert events[-1].kind == "terminal"
    assert events[-1].status == "completed"


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
async def test_multiple_coordinators_share_one_persistent_run_lease(tmp_path: Path) -> None:
    first = RunCoordinator(SessionEventJournal(tmp_path, "session-1"))
    second = RunCoordinator(SessionEventJournal(tmp_path, "session-1"))
    release = asyncio.Event()

    async def blocked():
        await release.wait()
        if False:
            yield RunEvent(kind="tool", status="running", payload={})

    results = await asyncio.gather(
        first.start_run("run-1", blocked()),
        second.start_run("run-2", blocked()),
        return_exceptions=True,
    )

    tasks = [result for result in results if isinstance(result, asyncio.Task)]
    conflicts = [result for result in results if isinstance(result, ActiveRunConflictError)]
    assert len(tasks) == 1
    assert len(conflicts) == 1
    winner_index = next(
        index for index, result in enumerate(results) if isinstance(result, asyncio.Task)
    )
    winner_run_id = f"run-{winner_index + 1}"
    persistent = SessionEventJournal(tmp_path, "session-1")
    assert persistent.active_run_id() == winner_run_id

    third = RunCoordinator(SessionEventJournal(tmp_path, "session-1"))
    with pytest.raises(ActiveRunConflictError, match=winner_run_id):
        await third.start_run("run-3", _events())
    assert persistent.active_run_id() == winner_run_id

    release.set()
    await tasks[0]
    assert SessionEventJournal(tmp_path, "session-1").active_run_id() is None


@pytest.mark.asyncio
async def test_cancel_during_begin_run_closes_committed_lease_before_propagating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    coordinator = RunCoordinator(journal)
    begin_started = threading.Event()
    allow_begin = threading.Event()
    original_begin = journal.begin_run

    def blocked_begin(run_id: str, *, owner_id: str, owner_pid: int):
        begin_started.set()
        assert allow_begin.wait(timeout=2)
        return original_begin(run_id, owner_id=owner_id, owner_pid=owner_pid)

    monkeypatch.setattr(journal, "begin_run", blocked_begin)
    start_task = asyncio.create_task(coordinator.start_run("run-1", _events()))
    assert await asyncio.to_thread(begin_started.wait, 1)
    start_task.cancel()
    await asyncio.sleep(0.05)
    assert not start_task.done()
    allow_begin.set()

    with pytest.raises(asyncio.CancelledError):
        await start_task

    assert journal.active_run_id() is None
    events = journal.replay(after=0, limit=20).items
    assert [(item.kind, item.status) for item in events] == [
        ("system", "running"),
        ("terminal", "cancelled"),
    ]
    next_task = await coordinator.start_run("run-2", _events())
    await next_task


@pytest.mark.asyncio
async def test_repeated_cancel_waits_for_cleanup_and_closes_unowned_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    coordinator = RunCoordinator(journal)
    begin_started = threading.Event()
    allow_begin = threading.Event()
    original_begin = journal.begin_run

    class UnownedStream:
        def __init__(self) -> None:
            self.closed = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

        async def aclose(self) -> None:
            self.closed = True

    stream = UnownedStream()

    def blocked_begin(run_id: str, *, owner_id: str, owner_pid: int):
        begin_started.set()
        assert allow_begin.wait(timeout=2)
        return original_begin(run_id, owner_id=owner_id, owner_pid=owner_pid)

    monkeypatch.setattr(journal, "begin_run", blocked_begin)
    start_task = asyncio.create_task(coordinator.start_run("run-1", stream))
    assert await asyncio.to_thread(begin_started.wait, 1)
    start_task.cancel()
    await asyncio.sleep(0.05)
    start_task.cancel()
    await asyncio.sleep(0.05)
    assert not start_task.done()
    allow_begin.set()
    with pytest.raises(asyncio.CancelledError):
        await start_task

    assert stream.closed is True
    assert journal.active_run_id() is None
    assert [item.status for item in journal.replay(after=0, limit=10).items] == [
        "running", "cancelled",
    ]


@pytest.mark.asyncio
async def test_active_run_repeated_cancel_waits_for_terminal_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    coordinator = RunCoordinator(journal)
    entered = asyncio.Event()
    terminal_started = threading.Event()
    allow_terminal = threading.Event()

    async def blocked():
        entered.set()
        await asyncio.Event().wait()
        if False:
            yield RunEvent(kind="tool", status="done", payload={})

    task = await coordinator.start_run("run-1", blocked())
    await entered.wait()
    original_terminal = journal.append_terminal_and_release

    def blocked_terminal(**values: object):
        terminal_started.set()
        assert allow_terminal.wait(timeout=2)
        return original_terminal(**values)

    monkeypatch.setattr(journal, "append_terminal_and_release", blocked_terminal)
    task.cancel()
    assert await asyncio.to_thread(terminal_started.wait, 1)
    task.cancel()
    await asyncio.sleep(0.05)
    assert not task.done()
    assert journal.active_run_id() == "run-1"
    allow_terminal.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert journal.active_run_id() is None
    assert coordinator.active_run_id is None


@pytest.mark.asyncio
async def test_new_owner_recovers_dead_process_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_a = RunCoordinator(
        SessionEventJournal(tmp_path, "session-1"), owner_id="owner-a"
    )
    new_owner = RunCoordinator(SessionEventJournal(tmp_path, "session-1"), owner_id="owner-b")
    release = asyncio.Event()

    async def blocked():
        await release.wait()
        if False:
            yield RunEvent(kind="tool", status="running", payload={})

    task = await owner_a.start_run("run-1", blocked())
    contender = RunCoordinator(
        SessionEventJournal(tmp_path, "session-1"), owner_id="owner-a"
    )
    with pytest.raises(ActiveRunConflictError, match="run-1"):
        await contender.start_run("run-2", _events())

    monkeypatch.setattr(
        journal_module,
        "_process_identity",
        lambda _pid: journal_module._ProcessIdentity(
            journal_module._ProcessIdentityState.ABSENT
        ),
    )

    assert await new_owner.recover_interrupted_runs() == ("run-1",)
    assert new_owner.journal.active_run_id() is None
    release.set()
    with pytest.raises(Exception, match="lease|fenc|owner"):
        await task


@pytest.mark.asyncio
async def test_recovery_does_not_take_lease_from_live_different_owner(tmp_path: Path) -> None:
    live = RunCoordinator(
        SessionEventJournal(tmp_path, "session-1"), owner_id="live-a", owner_pid=os.getpid()
    )
    observer = RunCoordinator(
        SessionEventJournal(tmp_path, "session-1"), owner_id="live-b", owner_pid=os.getpid()
    )
    release = asyncio.Event()

    async def blocked():
        await release.wait()
        if False:
            yield RunEvent(kind="tool", status="done", payload={})

    task = await live.start_run("run-1", blocked())
    assert await observer.recover_interrupted_runs() == ()
    assert observer.journal.active_run_id() == "run-1"
    release.set()
    await task


@pytest.mark.asyncio
async def test_recovered_stale_owner_cannot_append_after_interrupted_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_owner = RunCoordinator(
        SessionEventJournal(tmp_path, "session-1"), owner_id="old"
    )
    new_owner = RunCoordinator(SessionEventJournal(tmp_path, "session-1"), owner_id="new")
    release = asyncio.Event()

    async def stale_stream():
        await release.wait()
        yield RunEvent(kind="tool", status="done", payload={"stale": True})

    task = await old_owner.start_run("run-1", stale_stream())
    monkeypatch.setattr(
        journal_module,
        "_process_identity",
        lambda _pid: journal_module._ProcessIdentity(
            journal_module._ProcessIdentityState.ABSENT
        ),
    )
    assert await new_owner.recover_interrupted_runs() == ("run-1",)
    release.set()
    with pytest.raises(Exception, match="lease|fenc|owner"):
        await task

    events = new_owner.journal.replay(after=0, limit=20).items
    assert events[-1].kind == "terminal"
    assert events[-1].status == "interrupted"
    assert not any(item.payload.get("stale") for item in events)


@pytest.mark.asyncio
async def test_slow_subscriber_repairs_queue_overflow_from_journal(tmp_path: Path) -> None:
    coordinator = RunCoordinator(
        SessionEventJournal(tmp_path, "session-1"), subscriber_queue_size=4
    )
    subscription = coordinator.subscribe(after=0)
    first_waiter = asyncio.create_task(anext(subscription))

    async def many_events():
        for index in range(20):
            yield RunEvent(kind="tool", status="done", payload={"index": index})

    task = await coordinator.start_run("run-1", many_events())
    first = await first_waiter
    await task
    remaining = [await asyncio.wait_for(anext(subscription), 1) for _ in range(21)]
    await subscription.aclose()

    assert [item.sequence for item in [first, *remaining]] == list(range(1, 23))


@pytest.mark.asyncio
async def test_subscription_polls_events_from_another_coordinator(tmp_path: Path) -> None:
    listener = RunCoordinator(
        SessionEventJournal(tmp_path, "session-1"), poll_interval_seconds=0.01
    )
    writer = RunCoordinator(SessionEventJournal(tmp_path, "session-1"))
    subscription = listener.subscribe(after=0)
    first_waiter = asyncio.create_task(anext(subscription))
    await asyncio.sleep(0.02)

    task = await writer.start_run(
        "run-1", _events(RunEvent(kind="assistant", status="done", payload={"ok": True}))
    )
    await task
    first = await asyncio.wait_for(first_waiter, 1)
    second = await asyncio.wait_for(anext(subscription), 1)
    third = await asyncio.wait_for(anext(subscription), 1)
    await subscription.aclose()

    assert [item.sequence for item in (first, second, third)] == [1, 2, 3]


@pytest.mark.asyncio
async def test_large_history_yields_before_all_pages_are_materialized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    for index in range(550):
        journal.append(run_id="run-1", kind="tool", status="done", payload={"i": index})
    coordinator = RunCoordinator(journal)
    calls = 0
    original_replay = journal.replay

    def counted_replay(*, after: int = 0, limit: int = 100):
        nonlocal calls
        calls += 1
        return original_replay(after=after, limit=limit)

    monkeypatch.setattr(journal, "replay", counted_replay)
    subscription = coordinator.subscribe(after=0)
    first = await anext(subscription)
    await subscription.aclose()

    assert first.sequence == 1
    assert calls == 1


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
    journal.begin_run("abandoned", owner_id="legacy", owner_pid=-1)
    coordinator = RunCoordinator(SessionEventJournal(tmp_path, "session-1"))

    recovered = await coordinator.recover_interrupted_runs()
    repeated = await coordinator.recover_interrupted_runs()

    assert recovered == ("abandoned",)
    assert repeated == ()
    terminal = coordinator.journal.replay(after=0, limit=20).items[-1]
    assert terminal.status == "interrupted"
    assert terminal.payload == {"event": "run_interrupted", "retryable": True}
