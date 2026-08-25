from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from core.coding.memory import workspace_id_from_path
from core.coding.persistence.session_event_journal import SessionEventJournal
from core.learning import (
    LearningActivationService,
    LearningKickoffService,
    LearningTaskCreate,
    LearningTaskRepository,
    LearningTaskService,
)
from core.learning.runtime_resources import (
    SageLearningActivationResources,
    SageLearningKickoffResources,
)


def _services(root: Path):  # type: ignore[no-untyped-def]
    workspace = root / "workspace"
    workspace.mkdir(exist_ok=True)
    storage = root / ".coding"
    repository = LearningTaskRepository(storage / "learning-tasks.sqlite3")
    activation = LearningActivationService(
        repository,
        SageLearningActivationResources(
            storage_root=storage,
            workspace_root=workspace,
            runtime_profile="legacy",
            sandbox_provider="local_workspace",
            sandbox_image="",
            knowledge_available=False,
            web_search_available=False,
            web_fetch_available=False,
        ),
    )
    kickoff = LearningKickoffService(
        repository,
        SageLearningKickoffResources(storage_root=storage),
    )
    return LearningTaskService(repository), activation, kickoff


def test_two_services_accept_one_kickoff_receipt_and_journal_message(tmp_path: Path) -> None:
    tasks, activation, _ = _services(tmp_path)
    workspace_id = workspace_id_from_path(tmp_path / "workspace")
    task = tasks.create_draft(
        owner_id="local",
        workspace_id=workspace_id,
        request=LearningTaskCreate(
            topic="学习并发 kickoff",
            desired_outcome="解释一次接受语义",
            starting_level="intermediate",
            time_budget_minutes_per_week=60,
        ),
    )
    active = activation.activate(
        owner_id="local",
        workspace_id=workspace_id,
        task_id=task.task_id,
        expected_revision=1,
        idempotency_key="activate-concurrent-kickoff",
    )
    _, _, first = _services(tmp_path)
    _, _, second = _services(tmp_path)
    barrier = Barrier(3)

    def dispatch(service: LearningKickoffService):
        barrier.wait()
        return service.dispatch(
            owner_id="local",
            workspace_id=workspace_id,
            task_id=task.task_id,
            expected_revision=1,
            idempotency_key="kickoff-concurrent-r1",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(dispatch, service) for service in (first, second)]
        barrier.wait()
        receipts = tuple(future.result(timeout=10) for future in futures)

    assert receipts[0] == receipts[1]
    assert receipts[0].receipt_status == "accepted"
    journal = SessionEventJournal(tmp_path / ".coding", active.session_id)
    assert len(journal.events_for_run(receipts[0].acceptance_run_id)) == 1
    with sqlite3.connect(tmp_path / ".coding" / "learning-tasks.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM learning_task_kickoffs").fetchone() == (1,)
