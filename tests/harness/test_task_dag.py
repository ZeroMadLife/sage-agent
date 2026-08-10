"""Task DAG 契约与 ready-wave 调度回归。"""

from __future__ import annotations

import asyncio

import pytest
from sage_harness.task_dag import (
    TaskDAGNode,
    TaskDAGNodeResult,
    TaskDAGPlan,
    TaskDAGPlanValidationError,
    TaskDAGScheduler,
)


def _node(
    node_id: str,
    *,
    depends_on: tuple[str, ...] = (),
    subagent_type: str = "explore",
) -> TaskDAGNode:
    return TaskDAGNode(
        node_id=node_id,
        description=f"inspect {node_id}",
        prompt=f"Inspect bounded question {node_id}.",
        subagent_type=subagent_type,
        depends_on=depends_on,
    )


def test_plan_hash_and_topological_order_are_input_order_independent() -> None:
    first = TaskDAGPlan.create(
        nodes=(_node("join", depends_on=("left", "right")), _node("left"), _node("right")),
        max_concurrent=2,
    )
    second = TaskDAGPlan.create(
        nodes=(_node("right"), _node("join", depends_on=("right", "left")), _node("left")),
        max_concurrent=2,
    )

    assert first.dag_hash == second.dag_hash
    assert first.dag_id == second.dag_id
    assert first.topological_order == ("left", "right", "join")
    assert first.receipt() == second.receipt()
    assert "prompt" not in first.receipt()["nodes"][0]


@pytest.mark.parametrize(
    ("nodes", "message"),
    [
        ((_node("a", depends_on=("missing",)),), "unknown dependency"),
        (
            (
                _node("a", depends_on=("b",)),
                _node("b", depends_on=("a",)),
            ),
            "cycle",
        ),
    ],
)
def test_plan_rejects_invalid_dependency_graph(
    nodes: tuple[TaskDAGNode, ...], message: str
) -> None:
    with pytest.raises(TaskDAGPlanValidationError, match=message):
        TaskDAGPlan.create(nodes=nodes)


def test_node_rejects_self_dependency() -> None:
    with pytest.raises(TaskDAGPlanValidationError, match="cannot depend on itself"):
        _node("a", depends_on=("a",))


def test_plan_rejects_unbounded_or_expanding_concurrency() -> None:
    with pytest.raises(TaskDAGPlanValidationError, match="max_concurrent"):
        TaskDAGPlan.create(nodes=(_node("a"),), max_concurrent=4)

    with pytest.raises(TaskDAGPlanValidationError, match="maximum node count"):
        TaskDAGPlan.create(nodes=tuple(_node(str(index)) for index in range(7)))


def test_scheduler_runs_ready_nodes_in_parallel_and_dependencies_afterward() -> None:
    plan = TaskDAGPlan.create(
        nodes=(_node("join", depends_on=("left", "right")), _node("left"), _node("right")),
        max_concurrent=2,
    )
    active = 0
    max_active = 0
    started: list[str] = []
    finished: list[str] = []

    async def execute(
        node: TaskDAGNode,
        dependencies: dict[str, TaskDAGNodeResult],
    ) -> TaskDAGNodeResult:
        nonlocal active, max_active
        if node.node_id == "join":
            assert set(dependencies) == {"left", "right"}
            assert all(item.status == "succeeded" for item in dependencies.values())
        active += 1
        max_active = max(max_active, active)
        started.append(node.node_id)
        await asyncio.sleep(0.01)
        active -= 1
        finished.append(node.node_id)
        return TaskDAGNodeResult(node_id=node.node_id, status="succeeded")

    results = asyncio.run(TaskDAGScheduler(plan).run(execute))

    assert max_active == 2
    assert set(started[:2]) == {"left", "right"}
    assert started[-1] == "join"
    assert finished[-1] == "join"
    assert all(item.status == "succeeded" for item in results.values())


def test_scheduler_blocks_only_failed_descendants_and_keeps_independent_branch() -> None:
    plan = TaskDAGPlan.create(
        nodes=(
            _node("failed"),
            _node("independent"),
            _node("blocked", depends_on=("failed",)),
            _node("continued", depends_on=("independent",)),
        ),
        max_concurrent=2,
    )

    async def execute(
        node: TaskDAGNode,
        dependencies: dict[str, TaskDAGNodeResult],
    ) -> TaskDAGNodeResult:
        _ = dependencies
        status = "failed" if node.node_id == "failed" else "succeeded"
        return TaskDAGNodeResult(
            node_id=node.node_id, status=status, error_code="boom" if status == "failed" else ""
        )

    results = asyncio.run(TaskDAGScheduler(plan).run(execute))

    assert results["failed"].status == "failed"
    assert results["blocked"].status == "blocked"
    assert results["blocked"].error_code == "dependency_failed"
    assert results["continued"].status == "succeeded"


def test_scheduler_serializes_workspace_practice_nodes() -> None:
    plan = TaskDAGPlan.create(
        nodes=(
            _node("write-a", subagent_type="practice"),
            _node("write-b", subagent_type="practice"),
        ),
        max_concurrent=3,
    )
    active = 0
    max_active = 0

    async def execute(
        node: TaskDAGNode,
        dependencies: dict[str, TaskDAGNodeResult],
    ) -> TaskDAGNodeResult:
        nonlocal active, max_active
        _ = node, dependencies
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return TaskDAGNodeResult(node_id=node.node_id, status="succeeded")

    asyncio.run(TaskDAGScheduler(plan).run(execute))

    assert max_active == 1


def test_scheduler_cancels_every_running_node_when_parent_is_cancelled() -> None:
    plan = TaskDAGPlan.create(
        nodes=(_node("left"), _node("right")),
        max_concurrent=2,
    )
    started = asyncio.Event()
    active = 0
    cancelled: set[str] = set()

    async def execute(
        node: TaskDAGNode,
        dependencies: dict[str, TaskDAGNodeResult],
    ) -> TaskDAGNodeResult:
        nonlocal active
        _ = dependencies
        active += 1
        if active == 2:
            started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.add(node.node_id)
            raise
        return TaskDAGNodeResult(node_id=node.node_id, status="succeeded")

    async def scenario() -> None:
        execution = asyncio.create_task(TaskDAGScheduler(plan).run(execute))
        await asyncio.wait_for(started.wait(), timeout=1)
        execution.cancel()
        with pytest.raises(asyncio.CancelledError):
            await execution

    asyncio.run(scenario())

    assert cancelled == {"left", "right"}
