"""Durable Turn Context Plan store tests."""

from __future__ import annotations

import sqlite3

import pytest

from core.coding.persistence.turn_plan_store import (
    TurnPlanConflictError,
    TurnPlanCorruptionError,
    TurnPlanStore,
)
from core.harness.turn_context_plan import TurnContextPlan


def _plan(
    *,
    plan_id: str = "tcp-1",
    session_id: str = "session-1",
    run_id: str = "run-1",
    source: str = "semantic_memory",
) -> TurnContextPlan:
    return TurnContextPlan.create(
        plan_id=plan_id,
        session_id=session_id,
        run_id=run_id,
        owner_fingerprint="owner-1",
        workspace_id="workspace-1",
        surface="coding",
        created_at="2026-08-07T00:00:00+00:00",
        admission={"input_origin": "user", "input_fingerprint": "input-sha"},
        prompt={"static_policy": {"template_id": "coding-harness", "revision": "v1"}},
        context_refs={},
        retrieval={"decision": "allow", "selected_sources": [source]},
        tools={"catalog_hash": "catalog-sha", "resident_ids": [], "deferred_ids": []},
        execution={"sandbox_fingerprint": "sandbox-sha"},
        resume={"checkpoint_thread_id": session_id, "checkpoint_namespace": ""},
    )


def test_store_reopens_idempotently_for_the_same_run_and_plan(tmp_path) -> None:
    first = _plan()
    store = TurnPlanStore(tmp_path, "session-1")

    assert store.put_if_absent(first) == first

    reopened = TurnPlanStore(tmp_path, "session-1")
    retry = _plan(plan_id="tcp-retry")
    assert reopened.put_if_absent(retry) == first
    assert reopened.load_for_run("run-1") == first


def test_store_rejects_conflicting_plan_for_same_run(tmp_path) -> None:
    store = TurnPlanStore(tmp_path, "session-1")
    store.put_if_absent(_plan())

    with pytest.raises(TurnPlanConflictError, match="run-1"):
        store.put_if_absent(_plan(plan_id="tcp-conflict", source="knowledge"))


def test_store_fails_closed_when_persisted_plan_hash_no_longer_matches(tmp_path) -> None:
    store = TurnPlanStore(tmp_path, "session-1")
    store.put_if_absent(_plan())

    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE turn_context_plans SET plan_hash = ? WHERE run_id = ?",
            ("sha256:" + "0" * 64, "run-1"),
        )

    with pytest.raises(TurnPlanCorruptionError, match="hash"):
        store.load_for_run("run-1")


def test_store_fails_closed_when_payload_and_hash_are_rebound_to_another_session(tmp_path) -> None:
    store = TurnPlanStore(tmp_path, "session-1")
    store.put_if_absent(_plan())
    rebound = _plan(session_id="session-2")

    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE turn_context_plans SET plan_hash = ?, payload_json = ? WHERE run_id = ?",
            (rebound.plan_hash, rebound.payload_json, "run-1"),
        )

    with pytest.raises(TurnPlanCorruptionError, match="session_id"):
        store.load_for_run("run-1")
