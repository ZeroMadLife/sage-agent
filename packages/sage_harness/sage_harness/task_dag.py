"""不可变 Task DAG 契约与有界 ready-wave 调度。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

TaskDAGNodeStatus = Literal[
    "succeeded",
    "failed",
    "blocked",
    "cancelled",
    "timed_out",
]
TaskDAGRunStatus = Literal["succeeded", "partial", "failed", "cancelled"]
TaskDAGNodeExecutor = Callable[
    ["TaskDAGNode", Mapping[str, "TaskDAGNodeResult"]],
    Awaitable["TaskDAGNodeResult"],
]

TASK_DAG_SCHEMA_VERSION = 1
DEFAULT_MAX_NODES = 6
DEFAULT_MAX_CONCURRENT = 3
MAX_DAG_DEPTH = 8
_NODE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_PROFILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


class TaskDAGPlanValidationError(ValueError):
    """DAG schema、依赖或服务端执行边界无效。"""


@dataclass(frozen=True, slots=True)
class TaskDAGNode:
    """一个有界子任务节点；prompt 仅供执行，不进入公开 receipt。"""

    node_id: str
    description: str
    prompt: str = field(repr=False)
    subagent_type: str = "explore"
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        node_id = str(self.node_id).strip()
        description = " ".join(str(self.description).split())
        prompt = str(self.prompt).strip()
        subagent_type = str(self.subagent_type).strip().casefold()
        dependencies = tuple(sorted(set(str(item).strip() for item in self.depends_on)))
        if _NODE_ID.fullmatch(node_id) is None:
            raise TaskDAGPlanValidationError("node_id must be bounded")
        if not description or len(description) > 200:
            raise TaskDAGPlanValidationError("node description must be non-empty and bounded")
        if not prompt or len(prompt) > 12_000:
            raise TaskDAGPlanValidationError("node prompt must be non-empty and bounded")
        if _PROFILE.fullmatch(subagent_type) is None:
            raise TaskDAGPlanValidationError("subagent_type must be bounded")
        if len(dependencies) > DEFAULT_MAX_NODES - 1 or any(
            _NODE_ID.fullmatch(item) is None for item in dependencies
        ):
            raise TaskDAGPlanValidationError("node dependencies must be bounded")
        if node_id in dependencies:
            raise TaskDAGPlanValidationError("node cannot depend on itself")
        object.__setattr__(self, "node_id", node_id)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "prompt", prompt)
        object.__setattr__(self, "subagent_type", subagent_type)
        object.__setattr__(self, "depends_on", dependencies)

    def _canonical_payload(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "description": self.description,
            "prompt": self.prompt,
            "subagent_type": self.subagent_type,
            "depends_on": list(self.depends_on),
        }

    def receipt(self) -> dict[str, object]:
        """返回不含 prompt 的审计投影。"""
        return {
            "node_id": self.node_id,
            "description": self.description,
            "subagent_type": self.subagent_type,
            "depends_on": list(self.depends_on),
        }


@dataclass(frozen=True, slots=True)
class TaskDAGPlan:
    """模型提出、服务端校验后冻结的任务依赖图。"""

    dag_id: str
    dag_hash: str
    nodes: tuple[TaskDAGNode, ...]
    max_concurrent: int
    topological_order: tuple[str, ...]
    depth: int

    @classmethod
    def create(
        cls,
        *,
        nodes: Sequence[TaskDAGNode],
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
        maximum_node_count: int = DEFAULT_MAX_NODES,
        concurrency_limit: int = DEFAULT_MAX_CONCURRENT,
        allowed_profiles: frozenset[str] | None = None,
    ) -> TaskDAGPlan:
        """规范化节点，完成依赖校验、环检测并计算稳定 hash。"""
        if isinstance(max_concurrent, bool) or not 1 <= max_concurrent <= concurrency_limit:
            raise TaskDAGPlanValidationError("max_concurrent exceeds the server limit")
        if isinstance(maximum_node_count, bool) or not 1 <= maximum_node_count <= 50:
            raise TaskDAGPlanValidationError("maximum node count is invalid")
        normalized = tuple(sorted(nodes, key=lambda item: item.node_id))
        if not 1 <= len(normalized) <= maximum_node_count:
            raise TaskDAGPlanValidationError("maximum node count exceeded")
        by_id = {node.node_id: node for node in normalized}
        if len(by_id) != len(normalized):
            raise TaskDAGPlanValidationError("node ids must be unique")
        for node in normalized:
            unknown = [item for item in node.depends_on if item not in by_id]
            if unknown:
                raise TaskDAGPlanValidationError(
                    f"unknown dependency for {node.node_id}: {unknown[0]}"
                )
            if allowed_profiles is not None and node.subagent_type not in allowed_profiles:
                raise TaskDAGPlanValidationError(
                    f"unregistered subagent profile for {node.node_id}"
                )
        order, depth = _topological_order(normalized)
        payload = {
            "version": TASK_DAG_SCHEMA_VERSION,
            "max_concurrent": max_concurrent,
            "nodes": [node._canonical_payload() for node in normalized],
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        dag_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        return cls(
            dag_id=f"dag_{dag_hash[:24]}",
            dag_hash=dag_hash,
            nodes=normalized,
            max_concurrent=max_concurrent,
            topological_order=order,
            depth=depth,
        )

    def receipt(self) -> dict[str, object]:
        """生成 Timeline/Checkpoint 可用的脱敏图收据。"""
        return {
            "version": TASK_DAG_SCHEMA_VERSION,
            "dag_id": self.dag_id,
            "dag_hash": self.dag_hash,
            "node_count": len(self.nodes),
            "max_concurrent": self.max_concurrent,
            "depth": self.depth,
            "topological_order": list(self.topological_order),
            "nodes": [node.receipt() for node in self.nodes],
        }


def derive_task_dag_run_id(
    thread_id: str,
    run_id: str,
    tool_call_id: str,
    dag_hash: str,
) -> str:
    """把不可变计划与本次 ToolCall 绑定为稳定的 DAG 运行标识。"""
    values = (thread_id, run_id, tool_call_id, dag_hash)
    if any(not str(value).strip() for value in values):
        raise ValueError("task DAG run identity requires bounded inputs")
    digest = hashlib.sha256("\0".join(values).encode("utf-8")).hexdigest()[:24]
    return f"dagrun_{digest}"


@dataclass(frozen=True, slots=True)
class TaskDAGNodeResult:
    """调度器使用的节点终态；大正文留在 child result store。"""

    node_id: str
    status: TaskDAGNodeStatus
    result: str = field(default="", repr=False)
    result_ref: str = ""
    error_code: str = ""
    evidence_refs: tuple[str, ...] = ()
    query_fingerprints: tuple[str, ...] = ()
    source_fingerprints: tuple[str, ...] = ()
    token_usage: int = 0
    model_calls: int = 0
    tool_count: int = 0

    def __post_init__(self) -> None:
        if _NODE_ID.fullmatch(str(self.node_id).strip()) is None:
            raise ValueError("node result id is invalid")
        if self.status not in {"succeeded", "failed", "blocked", "cancelled", "timed_out"}:
            raise ValueError("node result status is invalid")
        if min(self.token_usage, self.model_calls, self.tool_count) < 0:
            raise ValueError("node result usage must be non-negative")
        object.__setattr__(self, "node_id", str(self.node_id).strip())
        object.__setattr__(
            self,
            "evidence_refs",
            tuple(sorted(set(item.strip() for item in self.evidence_refs if item.strip()))),
        )
        for field_name in ("query_fingerprints", "source_fingerprints"):
            values = tuple(
                sorted(
                    set(
                        item.strip()
                        for item in getattr(self, field_name)
                        if item.strip()
                    )
                )
            )
            object.__setattr__(self, field_name, values)


class TaskDAGScheduler:
    """按依赖分 wave 调度；写型 practice 节点始终独占一个 wave。"""

    def __init__(
        self,
        plan: TaskDAGPlan,
        *,
        exclusive_profiles: frozenset[str] = frozenset({"practice"}),
    ) -> None:
        self.plan = plan
        self.exclusive_profiles = exclusive_profiles

    async def run(self, execute: TaskDAGNodeExecutor) -> dict[str, TaskDAGNodeResult]:
        """执行 ready 节点；失败只阻断后继，独立分支继续。"""
        by_id = {node.node_id: node for node in self.plan.nodes}
        pending = set(by_id)
        results: dict[str, TaskDAGNodeResult] = {}
        while pending:
            blocked = [
                node_id
                for node_id in sorted(pending)
                if any(
                    dependency in results and results[dependency].status != "succeeded"
                    for dependency in by_id[node_id].depends_on
                )
            ]
            for node_id in blocked:
                pending.remove(node_id)
                results[node_id] = TaskDAGNodeResult(
                    node_id=node_id,
                    status="blocked",
                    error_code="dependency_failed",
                )
            ready = [
                by_id[node_id]
                for node_id in sorted(pending)
                if all(
                    dependency in results and results[dependency].status == "succeeded"
                    for dependency in by_id[node_id].depends_on
                )
            ]
            if not ready:
                if pending:
                    raise RuntimeError("validated task DAG scheduler stalled")
                break
            batch = self._select_batch(ready)
            tasks: list[asyncio.Future[TaskDAGNodeResult]] = [
                asyncio.ensure_future(
                    execute(node, {item: results[item] for item in node.depends_on})
                )
                for node in batch
            ]
            try:
                completed = await asyncio.gather(*tasks, return_exceptions=True)
            except asyncio.CancelledError:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            for node, outcome in zip(batch, completed, strict=True):
                pending.remove(node.node_id)
                if isinstance(outcome, asyncio.CancelledError):
                    raise outcome
                if isinstance(outcome, BaseException):
                    results[node.node_id] = TaskDAGNodeResult(
                        node_id=node.node_id,
                        status="failed",
                        error_code="scheduler_executor_failed",
                    )
                    continue
                if outcome.node_id != node.node_id:
                    results[node.node_id] = TaskDAGNodeResult(
                        node_id=node.node_id,
                        status="failed",
                        error_code="scheduler_result_mismatch",
                    )
                    continue
                results[node.node_id] = outcome
        return {node_id: results[node_id] for node_id in self.plan.topological_order}

    def _select_batch(self, ready: Sequence[TaskDAGNode]) -> tuple[TaskDAGNode, ...]:
        shared = tuple(node for node in ready if node.subagent_type not in self.exclusive_profiles)
        if shared:
            return shared[: self.plan.max_concurrent]
        return (ready[0],)


def task_dag_run_status(results: Mapping[str, TaskDAGNodeResult]) -> TaskDAGRunStatus:
    """把节点终态归并为一个稳定的 DAG 终态。"""
    statuses = {item.status for item in results.values()}
    if statuses == {"succeeded"}:
        return "succeeded"
    if "cancelled" in statuses and statuses <= {"cancelled", "blocked"}:
        return "cancelled"
    if "succeeded" in statuses:
        return "partial"
    return "failed"


def _topological_order(nodes: Sequence[TaskDAGNode]) -> tuple[tuple[str, ...], int]:
    by_id = {node.node_id: node for node in nodes}
    remaining = {node.node_id: len(node.depends_on) for node in nodes}
    dependents: dict[str, list[str]] = {node.node_id: [] for node in nodes}
    depths = {node.node_id: 1 for node in nodes}
    for node in nodes:
        for dependency in node.depends_on:
            dependents[dependency].append(node.node_id)
    ready = sorted(node_id for node_id, count in remaining.items() if count == 0)
    order: list[str] = []
    while ready:
        node_id = ready.pop(0)
        order.append(node_id)
        for dependent in sorted(dependents[node_id]):
            remaining[dependent] -= 1
            depths[dependent] = max(depths[dependent], depths[node_id] + 1)
            if remaining[dependent] == 0:
                ready.append(dependent)
                ready.sort()
    if len(order) != len(nodes):
        raise TaskDAGPlanValidationError("task DAG contains a cycle")
    depth = max(depths.values(), default=0)
    if depth > MAX_DAG_DEPTH:
        raise TaskDAGPlanValidationError("task DAG depth exceeds the server limit")
    if set(order) != set(by_id):
        raise TaskDAGPlanValidationError("task DAG topology is invalid")
    return tuple(order), depth


__all__ = [
    "DEFAULT_MAX_CONCURRENT",
    "DEFAULT_MAX_NODES",
    "TASK_DAG_SCHEMA_VERSION",
    "TaskDAGNode",
    "TaskDAGNodeResult",
    "TaskDAGNodeStatus",
    "TaskDAGPlan",
    "TaskDAGPlanValidationError",
    "TaskDAGRunStatus",
    "TaskDAGScheduler",
    "derive_task_dag_run_id",
    "task_dag_run_status",
]
