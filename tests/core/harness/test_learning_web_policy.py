"""Learning frozen domain policy 与公开 Web port 能力合同。"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.coding.memory import workspace_id_from_path
from core.learning import (
    LearningActivationService,
    LearningSourcePolicy,
    LearningTaskCreate,
    LearningTaskRepository,
    LearningTaskService,
)
from core.learning.runtime_resources import SageLearningActivationResources


@pytest.mark.parametrize(
    ("policy_aware", "expects_fetch"),
    [(False, False), (True, True)],
)
def test_domain_frozen_activation_grants_fetch_only_to_policy_aware_port(
    tmp_path: Path,
    policy_aware: bool,
    expects_fetch: bool,
) -> None:
    case_root = tmp_path / ("aware" if policy_aware else "legacy")
    workspace = case_root / "workspace"
    workspace.mkdir(parents=True)
    storage = case_root / ".coding"
    repository = LearningTaskRepository(storage / "learning-tasks.sqlite3")
    resources = SageLearningActivationResources(
        storage_root=storage,
        workspace_root=workspace,
        runtime_profile="deerflow_v2",
        sandbox_provider="local_workspace",
        sandbox_image="python:3.11-slim",
        knowledge_available=True,
        web_search_available=True,
        web_fetch_available=True,
        web_fetch_policy_aware=policy_aware,
    )
    task = LearningTaskService(repository).create_draft(
        owner_id="local",
        workspace_id=workspace_id_from_path(workspace),
        request=LearningTaskCreate(
            topic="学习指定官方站点",
            desired_outcome="能引用指定站点解释关键概念",
            starting_level="beginner",
            time_budget_minutes_per_week=120,
            source_policy=LearningSourcePolicy(
                knowledge="disabled",
                web="allowed_when_insufficient",
                domains=("example.com",),
            ),
        ),
    )

    receipt = LearningActivationService(repository, resources).activate(
        owner_id="local",
        workspace_id=task.workspace_id,
        task_id=task.task_id,
        expected_revision=task.task_revision,
        idempotency_key=f"domain-policy-{policy_aware}",
    )

    assert ("web:fetch" in receipt.allowed_capabilities) is expects_fetch


def test_web_only_activation_grants_conditional_research(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    storage = tmp_path / ".coding"
    repository = LearningTaskRepository(storage / "learning-tasks.sqlite3")
    resources = SageLearningActivationResources(
        storage_root=storage,
        workspace_root=workspace,
        runtime_profile="deerflow_v2",
        sandbox_provider="local_workspace",
        sandbox_image="python:3.11-slim",
        knowledge_available=False,
        web_search_available=True,
        web_fetch_available=False,
    )
    task = LearningTaskService(repository).create_draft(
        owner_id="local",
        workspace_id=workspace_id_from_path(workspace),
        request=LearningTaskCreate(
            topic="用公开来源补足学习缺口",
            desired_outcome="能够引用公开来源解释关键边界",
            starting_level="beginner",
            time_budget_minutes_per_week=120,
            source_policy=LearningSourcePolicy(
                knowledge="disabled",
                web="allowed_when_insufficient",
            ),
        ),
    )

    receipt = LearningActivationService(repository, resources).activate(
        owner_id="local",
        workspace_id=task.workspace_id,
        task_id=task.task_id,
        expected_revision=task.task_revision,
        idempotency_key="web-only-research",
    )

    assert "web:search" in receipt.allowed_capabilities
    assert "subagent:research" in receipt.allowed_capabilities
