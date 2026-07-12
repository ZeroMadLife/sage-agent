"""Coding runtime run-lifecycle tests: lease, run_finished, exception cleanup.

These tests cover the V6 active-run lease, the run_finished terminal event,
and the try/except cleanup that guarantees the lease is released even when the
engine raises.
"""

import asyncio
from pathlib import Path

import pytest

from core.coding.context import ContextPolicy
from core.coding.runtime import CodingRuntime


class FakeModel:
    """Deterministic fake model for runtime tests."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)

    async def complete(self, prompt: str) -> str:
        _ = prompt
        return self.responses.pop(0)


class ExplodingModel:
    """Model whose first (and only) call raises, to exercise the except path."""

    def __init__(self, message: str = "engine blew up") -> None:
        self.message = message

    async def complete(self, prompt: str) -> str:
        raise RuntimeError(self.message)


def _runtime(tmp_path: Path, model, *, session_id: str = "s-lifecycle") -> CodingRuntime:
    (tmp_path / "README.md").write_text("TourSwarm lifecycle\n", encoding="utf-8")
    return CodingRuntime(
        session_id=session_id,
        workspace_root=tmp_path,
        model=model,
        storage_root=tmp_path / ".coding",
    )


async def test_run_turn_emits_run_finished(tmp_path: Path) -> None:
    """A normal turn ends with run_finished (status=completed) then turn_finished."""
    runtime = _runtime(
        tmp_path,
        FakeModel(
            [
                '<tool>{"name":"read_file","args":{"path":"README.md"}}</tool>',
                "<final>done</final>",
            ]
        ),
    )

    events = [event async for event in runtime.run_turn("读 README")]

    types = [event["type"] for event in events]
    # Terminal events land in this order at the very end: the workspace diff is
    # surfaced after the engine loop and before run_finished/turn_finished.
    assert types[-4:] == ["final", "workspace_diff_ready", "run_finished", "turn_finished"]

    finished = events[-2]
    assert finished["type"] == "run_finished"
    assert finished["status"] == "completed"
    assert finished["duration_ms"] >= 0
    # One tool_result -> one tool step counted.
    assert finished["tool_steps"] == 1
    assert finished["run_id"]

    # The lease is released after the turn.
    assert runtime.active_run_id is None


async def test_run_turn_rejects_concurrent_run(tmp_path: Path) -> None:
    """A second run_turn while active_run_id is set short-circuits with an error."""
    runtime = _runtime(
        tmp_path,
        FakeModel(
            [
                '<tool>{"name":"read_file","args":{"path":"README.md"}}</tool>',
                "<final>done</final>",
            ]
        ),
    )

    # Simulate an in-flight run by setting the lease directly.
    runtime.active_run_id = "run_in_flight"

    events = [event async for event in runtime.run_turn("another turn")]

    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert "already in progress" in events[0]["message"]
    # The lease is untouched (the rejected call must not clobber it).
    assert runtime.active_run_id == "run_in_flight"


async def test_run_turn_cleanup_on_exception(tmp_path: Path) -> None:
    """An engine exception becomes an error event + run_finished(error), lease released."""
    runtime = _runtime(tmp_path, ExplodingModel("engine blew up"))

    events = [event async for event in runtime.run_turn("trigger error")]

    types = [event["type"] for event in events]
    # The error event is surfaced, then run_finished with status=error, then
    # turn_finished. No "final" event is emitted because the engine raised.
    assert "error" in types
    assert types[-2:] == ["run_finished", "turn_finished"]

    error_event = next(event for event in events if event["type"] == "error")
    assert error_event["message"] == "Model request failed"
    assert "engine blew up" not in str(runtime.run_store.get_run(error_event["run_id"]))
    assert "engine blew up" not in str(runtime.transcript_store.read_all())

    finished = events[-2]
    assert finished["type"] == "run_finished"
    assert finished["status"] == "error"

    # The lease is released even after the exception.
    assert runtime.active_run_id is None

    # The persisted trace records the error + run_finished(error).
    run_status = runtime.run_store.run_status(error_event["run_id"])
    assert run_status == "error"


async def test_run_finished_event_carries_duration_and_tool_steps(tmp_path: Path) -> None:
    """run_finished reports duration_ms (>=0) and the count of tool_result events."""
    runtime = _runtime(
        tmp_path,
        FakeModel(
            [
                '<tool>{"name":"read_file","args":{"path":"README.md"}}</tool>',
                '<tool>{"name":"read_file","args":{"path":"README.md"}}</tool>',
                "<final>two reads done</final>",
            ]
        ),
    )

    events = [event async for event in runtime.run_turn("读两次 README")]

    finished = next(event for event in events if event["type"] == "run_finished")
    assert finished["duration_ms"] >= 0
    # Two read_file tool_results.
    assert finished["tool_steps"] == 2


async def test_runs_are_partitioned_by_session(tmp_path: Path) -> None:
    """Two sessions persist their runs in separate evidence partitions."""
    storage = tmp_path / ".coding"
    runtime_a = _runtime(
        tmp_path,
        FakeModel(["<final>a done</final>"]),
        session_id="session_a",
    )
    runtime_b = _runtime(
        tmp_path,
        FakeModel(["<final>b done</final>"]),
        session_id="session_b",
    )

    [event async for event in runtime_a.run_turn("a")]
    [event async for event in runtime_b.run_turn("b")]

    a_runs = runtime_a.run_store.list_runs()
    b_runs = runtime_b.run_store.list_runs()
    assert [run["run_id"] for run in a_runs] != [run["run_id"] for run in b_runs]
    assert len(a_runs) == 1
    assert len(b_runs) == 1

    # Physical partitioning under evidence/<session>/runs.
    a_dir = storage / "evidence" / "session_a" / "runs"
    b_dir = storage / "evidence" / "session_b" / "runs"
    assert a_dir.is_dir() and b_dir.is_dir()
    assert len(list(a_dir.iterdir())) == 1
    assert len(list(b_dir.iterdir())) == 1


def test_request_stop_emits_run_id(tmp_path: Path) -> None:
    """request_stop emits a stop_requested event carrying the active run_id."""
    runtime = _runtime(tmp_path, FakeModel(["<final>noop</final>"]))

    runtime.active_run_id = "run_active"
    ok = runtime.request_stop()

    # SessionEventBus persists a durable JSONL record carrying the run_id.
    events_path = runtime.session_event_bus.path
    import json

    records = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    stop_records = [record for record in records if record.get("event") == "stop_requested"]
    assert stop_records
    assert stop_records[0]["run_id"] == "run_active"
    assert stop_records[0]["session_id"] == "s-lifecycle"
    assert runtime.stop_requested is True
    # Unconditional stop (no run_id) is accepted and returns True.
    assert ok is True


async def test_run_turn_releases_lease_on_aclose(tmp_path: Path) -> None:
    """Lease is released even when the generator is closed early via aclose().

    This simulates a WebSocket disconnect: the consumer awaits one event then
    closes the async generator. A GeneratorExit is thrown at the suspended
    yield point inside the engine loop; the outer finally must release the
    lease (active_run_id -> None) and persist session state.
    """
    # A model with many tool calls so the generator suspends mid-loop after the
    # first event is yielded, giving us a point to call aclose().
    runtime = _runtime(
        tmp_path,
        FakeModel(
            [
                '<tool>{"name":"read_file","args":{"path":"README.md"}}</tool>',
                '<tool>{"name":"read_file","args":{"path":"README.md"}}</tool>',
                "<final>done</final>",
            ]
        ),
    )

    gen = runtime.run_turn("读 README")
    # Consume exactly one event, then close the generator. The generator is
    # now suspended at a `yield event` inside the engine loop. (turn_started is
    # emitted to the bus but not yielded; the first yielded event is
    # model_requested.)
    first = await gen.__anext__()
    assert first["type"] == "model_requested"
    assert first["run_id"]
    # The lease is held while the turn is in flight.
    assert runtime.active_run_id == first["run_id"]

    # Close the generator early (mirrors a dropped WebSocket). This throws
    # GeneratorExit at the suspended yield; the outer finally must run.
    await gen.aclose()

    # The lease is released even though the turn never completed.
    assert runtime.active_run_id is None
    assert runtime.stop_requested is False
    detail = runtime.run_store.get_run(first["run_id"])
    assert [event["type"] for event in detail["events"]][-3:] == [
        "cancelled",
        "run_finished",
        "turn_finished",
    ]
    assert runtime.run_store.run_status(first["run_id"]) == "cancelled"


async def test_pre_engine_failure_closes_run_and_releases_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path, FakeModel(["<final>unused</final>"]))

    def fail_snapshot() -> None:
        raise OSError("snapshot-secret")

    monkeypatch.setattr(runtime.diff_tracker, "snapshot_before_run", fail_snapshot)

    events = [event async for event in runtime.run_turn("hello")]

    assert [event["type"] for event in events] == [
        "error",
        "run_finished",
        "turn_finished",
    ]
    assert events[0]["message"] == "Run preparation failed"
    assert events[1]["status"] == "error"
    assert runtime.run_store.run_status(events[0]["run_id"]) == "error"
    assert runtime.active_run_id is None


async def test_engine_construction_failure_closes_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path, FakeModel(["<final>unused</final>"]))

    class BrokenEngine:
        def __init__(self, **kwargs: object) -> None:
            del kwargs
            raise RuntimeError("engine-constructor-secret")

    monkeypatch.setattr("core.coding.runtime.Engine", BrokenEngine)
    events = [event async for event in runtime.run_turn("hello")]

    assert [event["type"] for event in events] == [
        "error",
        "run_finished",
        "turn_finished",
    ]
    assert events[0]["message"] == "Run preparation failed"
    assert "engine-constructor-secret" not in str(events)
    assert runtime.run_store.run_status(events[0]["run_id"]) == "error"
    assert runtime.active_run_id is None


async def test_cancel_during_context_preparation_persists_terminal_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    runtime = CodingRuntime(
        session_id="s-context-cancel",
        workspace_root=tmp_path,
        model=FakeModel(["<final>unused</final>"]),
        storage_root=tmp_path / ".coding",
        context_policy=ContextPolicy(
            context_window_tokens=100_000, output_reserve_tokens=10_000
        ),
    )

    async def block_context(*args: object, **kwargs: object) -> object:
        del args, kwargs
        entered.set()
        await release.wait()
        raise AssertionError("unreachable")

    assert runtime.context_controller is not None
    monkeypatch.setattr(runtime.context_controller, "on_turn_start", block_context)
    stream = runtime.run_turn("hello")
    pending = asyncio.create_task(anext(stream))
    await entered.wait()
    run_id = runtime.active_run_id
    assert run_id is not None

    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending

    detail = runtime.run_store.get_run(run_id)
    assert [event["type"] for event in detail["events"]][-3:] == [
        "cancelled",
        "run_finished",
        "turn_finished",
    ]
    assert runtime.run_store.run_status(run_id) == "cancelled"
    assert runtime.active_run_id is None


def test_request_stop_rejects_stale_run_id(tmp_path: Path) -> None:
    """Stop for an old run_id doesn't affect a newer active run.

    A late stop request carrying a stale run_id is rejected (returns False)
    without flipping stop_requested or cancelling approvals/reviews.
    """
    runtime = _runtime(tmp_path, FakeModel(["<final>noop</final>"]))

    runtime.active_run_id = "run_new"
    ok = runtime.request_stop(run_id="run_old")

    # Stale stop is rejected and has no side effects.
    assert ok is False
    assert runtime.stop_requested is False
    # The active run is untouched.
    assert runtime.active_run_id == "run_new"


def test_request_stop_accepts_matching_run_id(tmp_path: Path) -> None:
    """Stop with a run_id matching the active run is accepted."""
    runtime = _runtime(tmp_path, FakeModel(["<final>noop</final>"]))

    runtime.active_run_id = "run_active"
    ok = runtime.request_stop(run_id="run_active")

    assert ok is True
    assert runtime.stop_requested is True


def test_request_stop_accepts_no_run_id(tmp_path: Path) -> None:
    """Stop without a run_id is applied unconditionally (backward compatible)."""
    runtime = _runtime(tmp_path, FakeModel(["<final>noop</final>"]))

    runtime.active_run_id = None
    ok = runtime.request_stop()

    assert ok is True
    assert runtime.stop_requested is True
