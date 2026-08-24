from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier, Event, Lock

from core.coding.memory import workspace_id_from_path
from core.coding.persistence import CodingSessionStore, TurnPlanStore
from core.coding.persistence.session_event_journal import SessionEventJournal
from core.harness.thread_goal import ThreadGoalService
from core.learning import (
    LearningActivationError,
    LearningActivationService,
    LearningTaskCreate,
    LearningTaskRepository,
    LearningTaskService,
)
from core.learning.runtime_resources import SageLearningActivationResources


def _services(
    root: Path,
    *,
    failure_injector=None,  # type: ignore[no-untyped-def]
) -> tuple[LearningTaskService, LearningActivationService, LearningTaskRepository]:
    workspace = root / "workspace"
    workspace.mkdir(exist_ok=True)
    repository = LearningTaskRepository(root / ".coding" / "learning-tasks.sqlite3")
    resources = SageLearningActivationResources(
        storage_root=root / ".coding",
        workspace_root=workspace,
        runtime_profile="legacy",
        sandbox_provider="local_workspace",
        sandbox_image="",
        knowledge_available=False,
        web_search_available=False,
        web_fetch_available=False,
    )
    return (
        LearningTaskService(repository),
        LearningActivationService(repository, resources, failure_injector=failure_injector),
        repository,
    )


def _task(root: Path):  # type: ignore[no-untyped-def]
    tasks, _, _ = _services(root)
    return tasks.create_draft(
        owner_id="local",
        workspace_id=workspace_id_from_path(root / "workspace"),
        request=LearningTaskCreate(
            topic="学习跨实例并发",
            desired_outcome="能够解释并复核激活恢复不变量",
            starting_level="beginner",
            time_budget_minutes_per_week=180,
        ),
    )


def _activate(service: LearningActivationService, task, key: str):  # type: ignore[no-untyped-def]
    return service.activate(
        owner_id="local",
        workspace_id=task.workspace_id,
        task_id=task.task_id,
        expected_revision=1,
        idempotency_key=key,
    )


def _assert_single_resources(root: Path, receipt) -> None:  # type: ignore[no-untyped-def]
    database = root / ".coding" / "learning-tasks.sqlite3"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM learning_task_activations").fetchone() == (
            1,
        )
        row = connection.execute("SELECT status, stage FROM learning_task_activations").fetchone()
        assert row == ("active", "active")
    sessions = list((root / ".coding" / "sessions").glob("*.json"))
    assert len(sessions) == 1
    session = CodingSessionStore(root / ".coding" / "sessions").load(receipt.session_id)
    assert session["archived"] is False
    plan_store = TurnPlanStore(root / ".coding", receipt.session_id)
    plan = plan_store.load_for_run(receipt.kickoff_run_id)
    assert plan is not None
    with sqlite3.connect(plan_store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM turn_context_plans").fetchone() == (1,)
    goal = ThreadGoalService(SessionEventJournal(root / ".coding", receipt.session_id)).get()
    assert goal is not None
    assert goal["learning_goal"]["goal_id"] == receipt.learning_goal_ref.goal_id


def test_two_instances_same_key_create_one_intent_session_goal_and_plan(tmp_path: Path) -> None:
    task = _task(tmp_path)
    _, first, _ = _services(tmp_path)
    _, second, _ = _services(tmp_path)
    barrier = Barrier(3)

    def run(service: LearningActivationService):
        barrier.wait()
        return _activate(service, task, "same-key")

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(run, service) for service in (first, second)]
        barrier.wait()
        receipts = tuple(future.result() for future in futures)

    assert receipts[0] == receipts[1]
    _assert_single_resources(tmp_path, receipts[0])


def test_two_instances_different_keys_allow_exactly_one_activation(tmp_path: Path) -> None:
    task = _task(tmp_path)
    _, first, _ = _services(tmp_path)
    _, second, _ = _services(tmp_path)
    barrier = Barrier(3)
    receipts = []
    failures: list[str] = []
    result_lock = Lock()

    def run(service: LearningActivationService, key: str) -> None:
        barrier.wait()
        try:
            result = _activate(service, task, key)
        except LearningActivationError as exc:
            with result_lock:
                failures.append(exc.code)
        else:
            with result_lock:
                receipts.append(result)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(run, first, "different-key-a"),
            executor.submit(run, second, "different-key-b"),
        ]
        barrier.wait()
        for future in futures:
            future.result()

    assert len(receipts) == 1
    assert failures == ["activation_idempotency_conflict"]
    _assert_single_resources(tmp_path, receipts[0])


def test_second_instance_completes_while_first_is_paused_at_failure_checkpoint(
    tmp_path: Path,
) -> None:
    task = _task(tmp_path)
    reached_checkpoint = Event()
    release_failure = Event()
    stale_records = []

    def fail_after_session(point: str, activation) -> None:  # type: ignore[no-untyped-def]
        if point != "after_session":
            return
        stale_records.append(activation)
        reached_checkpoint.set()
        assert release_failure.wait(timeout=10)
        raise RuntimeError("injected after_session")

    _, first, first_repository = _services(tmp_path, failure_injector=fail_after_session)
    _, second, second_repository = _services(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(_activate, first, task, "recovery-key")
        assert reached_checkpoint.wait(timeout=10)
        second_receipt = executor.submit(_activate, second, task, "recovery-key").result(timeout=10)
        release_failure.set()
        first_receipt = first_future.result(timeout=10)

    assert first_receipt == second_receipt
    assert stale_records[0].stage == "session"
    stale_write = first_repository.save_activation(
        replace(
            stale_records[0],
            receipt_status="activation_failed",
            failure_code="late-writer",
        ),
        task_status="activation_failed",
    )
    assert stale_write.receipt_status == "active"
    assert (
        second_repository.activation(
            owner_id="local", workspace_id=task.workspace_id, task_id=task.task_id
        ).stage
        == "active"
    )
    _assert_single_resources(tmp_path, second_receipt)
