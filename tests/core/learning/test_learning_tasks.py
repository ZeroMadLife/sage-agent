from pathlib import Path
from threading import Barrier, Thread

import pytest

from core.learning import (
    LearningSourcePolicy,
    LearningTaskConflictError,
    LearningTaskCreate,
    LearningTaskPatch,
    LearningTaskRepository,
    LearningTaskService,
)

WORKSPACE_ID = "workspace-test"


def _service(path: Path) -> LearningTaskService:
    return LearningTaskService(LearningTaskRepository(path))


def test_draft_persists_hard_source_constraints_across_restart(tmp_path: Path) -> None:
    database = tmp_path / "learning-tasks.sqlite3"
    created = _service(database).create_draft(
        owner_id="local",
        workspace_id=WORKSPACE_ID,
        request=LearningTaskCreate(topic="我想学习阳明心学，只用我的知识库，不要联网"),
    )

    assert created.task_revision == 1
    assert created.status == "draft"
    assert created.source_policy.knowledge == "required"
    assert created.source_policy.web == "forbidden"
    assert created.risk_class == "general_education"
    assert created.clarification.required_fields == (
        "desired_outcome",
        "starting_level",
        "time_budget_minutes_per_week",
    )

    restored = _service(database).get(
        owner_id="local", workspace_id=WORKSPACE_ID, task_id=created.task_id
    )

    assert restored == created


def test_patch_uses_cas_and_recomputes_financial_education_boundary(tmp_path: Path) -> None:
    service = _service(tmp_path / "learning-tasks.sqlite3")
    created = service.create_draft(
        owner_id="local",
        workspace_id=WORKSPACE_ID,
        request=LearningTaskCreate(topic="我想学习投资"),
    )

    assert created.risk_class == "financial_education"
    assert created.risk_notice == "仅提供金融与投资教育，不提供个性化证券买卖建议或收益承诺。"

    updated = service.update_draft(
        owner_id="local",
        workspace_id=WORKSPACE_ID,
        task_id=created.task_id,
        expected_revision=1,
        patch=LearningTaskPatch(
            desired_outcome="能够读懂基础财务报表并解释常见风险",
            starting_level="beginner",
            time_budget_minutes_per_week=240,
        ),
    )

    assert updated.task_revision == 2
    assert updated.clarification.required_fields == ()
    assert updated.learner_profile.time_budget_minutes_per_week == 240
    assert updated.risk_class == "financial_education"

    with pytest.raises(LearningTaskConflictError, match="current revision is 2"):
        service.update_draft(
            owner_id="local",
            workspace_id=WORKSPACE_ID,
            task_id=created.task_id,
            expected_revision=1,
            patch=LearningTaskPatch(desired_outcome="stale update"),
        )


def test_learning_task_owner_scope_isolated(tmp_path: Path) -> None:
    service = _service(tmp_path / "learning-tasks.sqlite3")
    task = service.create_draft(
        owner_id="owner-a",
        workspace_id=WORKSPACE_ID,
        request=LearningTaskCreate(topic="学习 Java 并发"),
    )

    with pytest.raises(KeyError):
        service.get(owner_id="owner-b", workspace_id=WORKSPACE_ID, task_id=task.task_id)


def test_patch_can_clear_optional_fields_without_weakening_hard_source_constraint(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path / "learning-tasks.sqlite3")
    created = service.create_draft(
        owner_id="local",
        workspace_id=WORKSPACE_ID,
        request=LearningTaskCreate(
            topic="我想学金融基础，不要联网",
            desired_outcome="能够解释风险与收益的关系",
            starting_level="beginner",
            time_budget_minutes_per_week=120,
        ),
    )

    updated = service.update_draft(
        owner_id="local",
        workspace_id=WORKSPACE_ID,
        task_id=created.task_id,
        expected_revision=1,
        patch=LearningTaskPatch(
            desired_outcome=None,
            source_policy=LearningSourcePolicy(web="allowed_when_insufficient"),
        ),
    )

    assert updated.desired_outcome is None
    assert updated.clarification.required_fields == ("desired_outcome",)
    assert updated.source_policy.web == "forbidden"


def test_concurrent_cas_updates_have_exactly_one_winner(tmp_path: Path) -> None:
    database = tmp_path / "learning-tasks.sqlite3"
    service = _service(database)
    created = service.create_draft(
        owner_id="local",
        workspace_id=WORKSPACE_ID,
        request=LearningTaskCreate(topic="学习 Java 并发"),
    )
    barrier = Barrier(3)
    revisions: list[int] = []
    conflicts: list[int] = []

    def update(outcome: str) -> None:
        worker = _service(database)
        barrier.wait()
        try:
            task = worker.update_draft(
                owner_id="local",
                workspace_id=WORKSPACE_ID,
                task_id=created.task_id,
                expected_revision=1,
                patch=LearningTaskPatch(desired_outcome=outcome),
            )
            revisions.append(task.task_revision)
        except LearningTaskConflictError as exc:
            conflicts.append(exc.current_revision)

    threads = [Thread(target=update, args=(f"目标 {index}",)) for index in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert revisions == [2]
    assert conflicts == [2]
    assert (
        service.get(
            owner_id="local", workspace_id=WORKSPACE_ID, task_id=created.task_id
        ).task_revision
        == 2
    )


def test_repository_rejects_symlink_database_path(tmp_path: Path) -> None:
    target = tmp_path / "target.sqlite3"
    target.touch()
    link = tmp_path / "learning-tasks.sqlite3"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="must not be a symlink"):
        LearningTaskRepository(link)
