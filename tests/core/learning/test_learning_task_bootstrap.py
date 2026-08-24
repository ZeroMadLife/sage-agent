from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

import pytest

from core.learning import (
    LearningActivationError,
    LearningActivationService,
    LearningActivationTurnContextBinding,
    LearningTaskCreate,
    LearningTaskNotFoundError,
    LearningTaskPatch,
    LearningTaskRepository,
    LearningTaskService,
)


@dataclass
class _FakeResources:
    sessions: dict[str, bool]
    goals: dict[str, int]
    plans: dict[str, LearningActivationTurnContextBinding]
    lock: Lock

    @classmethod
    def create(cls) -> _FakeResources:
        return cls(sessions={}, goals={}, plans={}, lock=Lock())

    def ensure_session(self, *, activation, task) -> None:  # type: ignore[no-untyped-def]
        with self.lock:
            self.sessions[activation.session_id] = False

    def ensure_thread_goal(self, *, activation, task) -> int:  # type: ignore[no-untyped-def]
        with self.lock:
            return self.goals.setdefault(activation.session_id, 1)

    def ensure_turn_context_plan(  # type: ignore[no-untyped-def]
        self,
        *,
        activation,
        task,
        thread_goal_revision,
    ) -> LearningActivationTurnContextBinding:
        _ = task, thread_goal_revision
        with self.lock:
            return self.plans.setdefault(
                activation.session_id,
                LearningActivationTurnContextBinding(
                    turn_context_plan_hash="sha256:" + "a" * 64,
                    catalog_revision="catalog-r1",
                    capability_revision="lcap_" + "b" * 32,
                    allowed_capabilities=("local:knowledge_search",),
                ),
            )

    def archive_session(self, *, activation) -> None:  # type: ignore[no-untyped-def]
        with self.lock:
            if activation.session_id in self.sessions:
                self.sessions[activation.session_id] = True


class _GoalConflictResources(_FakeResources):
    def ensure_thread_goal(self, *, activation, task) -> int:  # type: ignore[no-untyped-def]
        raise LearningActivationError(
            "learning activation goal binding conflict",
            code="learning_activation_goal_conflict",
        )


def _service(
    path: Path,
    resources: _FakeResources,
    *,
    fail_at: str | None = None,
) -> tuple[LearningTaskService, LearningActivationService]:
    repository = LearningTaskRepository(path)

    def inject(point: str, _activation) -> None:  # type: ignore[no-untyped-def]
        if point == fail_at:
            raise RuntimeError(f"injected:{point}")

    return (
        LearningTaskService(repository),
        LearningActivationService(
            repository,
            resources,
            failure_injector=inject if fail_at is not None else None,
        ),
    )


def _ready_task(tasks: LearningTaskService, *, owner_id: str = "local"):
    return tasks.create_draft(
        owner_id=owner_id,
        request=LearningTaskCreate(
            topic="系统学习阳明心学",
            desired_outcome="能够解释核心概念并比较主要争议",
            starting_level="beginner",
            time_budget_minutes_per_week=240,
        ),
    )


@pytest.mark.parametrize(
    "failure_point",
    ("after_intent", "after_session", "after_goal", "before_receipt"),
)
def test_activation_reconciles_each_durable_failure_point_without_duplicate_resources(
    tmp_path: Path,
    failure_point: str,
) -> None:
    database = tmp_path / "learning-tasks.sqlite3"
    resources = _FakeResources.create()
    tasks, failing = _service(database, resources, fail_at=failure_point)
    task = _ready_task(tasks)

    with pytest.raises(LearningActivationError, match="learning activation failed"):
        failing.activate(
            owner_id="local",
            task_id=task.task_id,
            expected_revision=1,
            idempotency_key="activate-yangming-v1",
        )

    failed = failing.get(owner_id="local", task_id=task.task_id)
    assert failed.receipt_status == "activation_failed"
    assert failed.failure_code == "RuntimeError"
    if failed.session_created:
        assert resources.sessions[failed.session_id] is True

    _, restarted = _service(database, resources)
    assert restarted.reconcile() == 1
    active = restarted.get(owner_id="local", task_id=task.task_id)

    assert active.receipt_status == "active"
    assert active.completed_at is not None
    assert resources.sessions == {active.session_id: False}
    assert resources.goals == {active.session_id: 1}
    assert set(resources.plans) == {active.session_id}

    repeated = restarted.activate(
        owner_id="local",
        task_id=task.task_id,
        expected_revision=1,
        idempotency_key="activate-yangming-v1",
    )
    assert repeated == active
    assert len(resources.sessions) == len(resources.goals) == len(resources.plans) == 1


def test_activation_rejects_a_second_key_for_the_same_task_revision(tmp_path: Path) -> None:
    resources = _FakeResources.create()
    tasks, activation = _service(tmp_path / "learning-tasks.sqlite3", resources)
    task = _ready_task(tasks)
    first = activation.activate(
        owner_id="local",
        task_id=task.task_id,
        expected_revision=1,
        idempotency_key="activate-key-one",
    )

    with pytest.raises(LearningActivationError, match="different idempotency key"):
        activation.activate(
            owner_id="local",
            task_id=task.task_id,
            expected_revision=1,
            idempotency_key="activate-key-two",
        )

    assert activation.get(owner_id="local", task_id=task.task_id) == first
    assert len(resources.sessions) == len(resources.goals) == len(resources.plans) == 1


def test_activation_rejects_reusing_one_owner_key_for_another_task(tmp_path: Path) -> None:
    resources = _FakeResources.create()
    tasks, activation = _service(tmp_path / "learning-tasks.sqlite3", resources)
    first_task = _ready_task(tasks)
    second_task = _ready_task(tasks)
    activation.activate(
        owner_id="local",
        task_id=first_task.task_id,
        expected_revision=1,
        idempotency_key="owner-scoped-key",
    )

    with pytest.raises(LearningActivationError) as conflict:
        activation.activate(
            owner_id="local",
            task_id=second_task.task_id,
            expected_revision=1,
            idempotency_key="owner-scoped-key",
        )

    assert conflict.value.code == "activation_idempotency_conflict"
    assert tasks.get(owner_id="local", task_id=second_task.task_id).status == "draft"


def test_activation_resources_contract_error_is_durable_and_archives_session(
    tmp_path: Path,
) -> None:
    resources = _GoalConflictResources(sessions={}, goals={}, plans={}, lock=Lock())
    tasks, activation = _service(tmp_path / "learning-tasks.sqlite3", resources)
    task = _ready_task(tasks)

    with pytest.raises(LearningActivationError) as failure:
        activation.activate(
            owner_id="local",
            task_id=task.task_id,
            expected_revision=1,
            idempotency_key="goal-conflict",
        )

    assert failure.value.code == "learning_activation_goal_conflict"
    receipt = activation.get(owner_id="local", task_id=task.task_id)
    assert receipt.receipt_status == "activation_failed"
    assert receipt.failure_code == "learning_activation_goal_conflict"
    assert resources.sessions == {receipt.session_id: True}
    assert tasks.get(owner_id="local", task_id=task.task_id).status == "activation_failed"

    revised = tasks.update_draft(
        owner_id="local",
        task_id=task.task_id,
        expected_revision=1,
        patch=LearningTaskPatch(desired_outcome="能够解释核心概念、争议和实践边界"),
    )
    assert revised.task_revision == 2
    assert revised.status == "draft"
    assert activation.reconcile() == 0


def test_reconciliation_skips_corrupt_receipt_and_repairs_remaining_tasks(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    database = tmp_path / "learning-tasks.sqlite3"
    resources = _FakeResources.create()
    tasks, failing = _service(database, resources, fail_at="after_intent")
    corrupt_task = _ready_task(tasks)
    repairable_task = _ready_task(tasks)
    for task, key in (
        (corrupt_task, "corrupt-receipt"),
        (repairable_task, "repairable-receipt"),
    ):
        with pytest.raises(LearningActivationError):
            failing.activate(
                owner_id="local",
                task_id=task.task_id,
                expected_revision=1,
                idempotency_key=key,
            )

    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE learning_task_activations SET receipt_json = ? WHERE task_id = ?",
            ('{"version":1}', corrupt_task.task_id),
        )
        connection.commit()

    _, restarted = _service(database, resources)
    assert restarted.reconcile() == 1
    assert (
        restarted.get(owner_id="local", task_id=repairable_task.task_id).receipt_status == "active"
    )
    assert "Skipped 1 corrupt learning activation receipt" in caplog.text

    with pytest.raises(LearningActivationError) as corrupt:
        restarted.get(owner_id="local", task_id=corrupt_task.task_id)
    assert corrupt.value.code == "learning_activation_corrupt"


def test_activation_records_and_stable_ids_are_owner_isolated(tmp_path: Path) -> None:
    resources = _FakeResources.create()
    tasks, activation = _service(tmp_path / "learning-tasks.sqlite3", resources)
    first_task = _ready_task(tasks, owner_id="owner-a")
    second_task = _ready_task(tasks, owner_id="owner-b")
    first = activation.activate(
        owner_id="owner-a",
        task_id=first_task.task_id,
        expected_revision=1,
        idempotency_key="shared-client-key",
    )
    second = activation.activate(
        owner_id="owner-b",
        task_id=second_task.task_id,
        expected_revision=1,
        idempotency_key="shared-client-key",
    )

    assert first.session_id != second.session_id
    assert first.learning_goal_ref.goal_id != second.learning_goal_ref.goal_id
    with pytest.raises(LearningTaskNotFoundError):
        tasks.get(owner_id="owner-b", task_id=first_task.task_id)
    with pytest.raises(LearningActivationError) as hidden:
        activation.get(owner_id="owner-b", task_id=first_task.task_id)
    assert hidden.value.code == "learning_activation_not_found"


def test_concurrent_same_key_activation_returns_one_active_receipt(tmp_path: Path) -> None:
    resources = _FakeResources.create()
    tasks, activation = _service(tmp_path / "learning-tasks.sqlite3", resources)
    task = _ready_task(tasks)

    def activate():  # type: ignore[no-untyped-def]
        return activation.activate(
            owner_id="local",
            task_id=task.task_id,
            expected_revision=1,
            idempotency_key="activate-concurrently",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = tuple(executor.map(lambda _: activate(), range(2)))

    assert receipts[0] == receipts[1]
    assert receipts[0].receipt_status == "active"
    assert len(resources.sessions) == len(resources.goals) == len(resources.plans) == 1
