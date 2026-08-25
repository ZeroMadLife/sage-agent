"""Concurrency contracts for application-owned coding run hydration."""

import asyncio
from pathlib import Path

from api.coding_runs import CodingRunRegistry


async def test_run_hydration_is_single_flight_per_session_and_parallel_across_sessions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A blocked journal recovery must not serialize another Session recovery."""
    registry = CodingRunRegistry(tmp_path / ".coding")
    coordinator_a = registry.get("session-a")
    coordinator_b = registry.get("session-b")
    a_started = asyncio.Event()
    allow_a = asyncio.Event()
    b_started = asyncio.Event()
    recover_attempts = {"session-a": 0, "session-b": 0}

    async def recover_a() -> tuple[str, ...]:
        recover_attempts["session-a"] += 1
        a_started.set()
        await allow_a.wait()
        return ()

    async def recover_b() -> tuple[str, ...]:
        recover_attempts["session-b"] += 1
        b_started.set()
        return ()

    monkeypatch.setattr(coordinator_a, "recover_interrupted_runs", recover_a)
    monkeypatch.setattr(coordinator_b, "recover_interrupted_runs", recover_b)

    first_a = asyncio.create_task(registry.hydrate("session-a"))
    second_a = asyncio.create_task(registry.hydrate("session-a"))
    await a_started.wait()
    task_b = asyncio.create_task(registry.hydrate("session-b"))
    turn_completed = asyncio.Event()
    asyncio.get_running_loop().call_soon(turn_completed.set)
    await turn_completed.wait()
    b_entered_while_a_blocked = b_started.is_set()

    allow_a.set()
    hydrated_a, repeated_a, hydrated_b = await asyncio.gather(first_a, second_a, task_b)

    assert b_entered_while_a_blocked
    assert hydrated_a is repeated_a is coordinator_a
    assert hydrated_b is coordinator_b
    assert recover_attempts == {"session-a": 1, "session-b": 1}
    assert registry._hydration_flights == {}
