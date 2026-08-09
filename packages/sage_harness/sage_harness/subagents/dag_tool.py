"""服务端校验并执行不可变 Task DAG 的父级工具。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, cast

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, InjectedToolCallId, tool
from langgraph.types import Command

from sage_harness.config import HarnessRunContext
from sage_harness.state import SageThreadState
from sage_harness.subagents.contracts import (
    SubagentExecutorPort,
    SubagentRequest,
    SubagentResult,
    SubagentToolConfig,
    derive_child_run_id,
)
from sage_harness.subagents.tool import (
    _delegation_entry,
    _evidence_child_run_ids,
    _execute_subagent_request,
    _reserved_max_steps,
    _reserved_token_budget,
    _state_strings,
)
from sage_harness.task_dag import (
    DEFAULT_MAX_CONCURRENT,
    DEFAULT_MAX_NODES,
    TaskDAGNode,
    TaskDAGNodeResult,
    TaskDAGPlan,
    TaskDAGPlanValidationError,
    TaskDAGScheduler,
    derive_task_dag_run_id,
    task_dag_run_status,
)

_TERMINAL_GRAPH_STATUSES = frozenset({"succeeded", "partial", "failed", "cancelled"})
_RESULT_PER_NODE_MAX = 2_000
_RESULT_TOTAL_MAX = 8_000


def _plan_from_arguments(
    nodes: list[dict[str, Any]],
    max_concurrent: int,
    config: SubagentToolConfig,
) -> TaskDAGPlan:
    """把模型参数转换为服务端拥有的严格 DAG 契约。"""
    if not isinstance(nodes, list):
        raise TaskDAGPlanValidationError("nodes must be a list")
    parsed: list[TaskDAGNode] = []
    for raw in nodes:
        if not isinstance(raw, Mapping):
            raise TaskDAGPlanValidationError("each task DAG node must be an object")
        for field_name in ("node_id", "description", "prompt"):
            if not isinstance(raw.get(field_name), str):
                raise TaskDAGPlanValidationError(f"task DAG node {field_name} must be a string")
        subagent_type = raw.get("subagent_type", "explore")
        if not isinstance(subagent_type, str):
            raise TaskDAGPlanValidationError("task DAG node subagent_type must be a string")
        dependencies = raw.get("depends_on", ())
        if not isinstance(dependencies, list | tuple):
            raise TaskDAGPlanValidationError("depends_on must be a list")
        if any(not isinstance(item, str) for item in dependencies):
            raise TaskDAGPlanValidationError("task DAG dependencies must be strings")
        parsed.append(
            TaskDAGNode(
                node_id=raw["node_id"],
                description=raw["description"],
                prompt=raw["prompt"],
                subagent_type=subagent_type,
                depends_on=tuple(dependencies),
            )
        )
    return TaskDAGPlan.create(
        nodes=parsed,
        max_concurrent=max_concurrent,
        maximum_node_count=DEFAULT_MAX_NODES,
        concurrency_limit=DEFAULT_MAX_CONCURRENT,
        allowed_profiles=config.allowed_types,
    )


def _existing_graph(
    state: Mapping[str, object],
    *,
    run_id: str,
    tool_call_id: str,
) -> Mapping[str, object] | None:
    graphs = state.get("task_graphs")
    if not isinstance(graphs, list):
        return None
    return next(
        (
            item
            for item in reversed(graphs)
            if isinstance(item, Mapping)
            and item.get("run_id") == run_id
            and item.get("tool_call_id") == tool_call_id
        ),
        None,
    )


def _has_reservation(state: Mapping[str, object], child_run_id: str) -> bool:
    entries = state.get("delegations")
    return isinstance(entries, list) and any(
        isinstance(item, Mapping)
        and item.get("id") == child_run_id
        and item.get("status")
        in {"running", "pending", "succeeded", "failed", "cancelled", "timed_out"}
        for item in entries
    )


def _error_command(
    *,
    tool_call_id: str,
    error_code: str,
    dag_id: str = "",
    dag_hash: str = "",
) -> Command[Any]:
    metadata = {
        "dag_id": dag_id,
        "dag_hash": dag_hash,
        "status": "failed",
        "error_code": error_code,
        "node_count": 0,
        "completed_count": 0,
        "failed_count": 0,
        "blocked_count": 0,
    }
    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=(
                        "Task DAG was rejected by the server before child execution. "
                        f"Error code: {error_code}."
                    ),
                    tool_call_id=tool_call_id,
                    name="task_dag",
                    status="error",
                    additional_kwargs={"sage_task_dag": metadata},
                )
            ]
        }
    )


def _graph_entry(
    *,
    plan: TaskDAGPlan,
    dag_run_id: str,
    run_id: str,
    tool_call_id: str,
    results: Mapping[str, TaskDAGNodeResult],
    child_ids: Mapping[str, str],
) -> dict[str, object]:
    """生成不含 prompt 和结果正文的 Checkpoint DAG 收据。"""
    status = task_dag_run_status(results)
    succeeded = sum(item.status == "succeeded" for item in results.values())
    blocked = sum(item.status == "blocked" for item in results.values())
    failed = len(results) - succeeded - blocked
    return {
        "dag_id": dag_run_id,
        "dag_hash": plan.dag_hash,
        "run_id": run_id,
        "tool_call_id": tool_call_id,
        "status": status,
        "node_count": len(plan.nodes),
        "completed_count": succeeded,
        "failed_count": failed,
        "blocked_count": blocked,
        "nodes": [
            {
                "node_id": node_id,
                "status": results[node_id].status,
                "child_run_id": child_ids[node_id],
                "result_ref": results[node_id].result_ref,
                "error_code": results[node_id].error_code,
                "evidence_count": len(results[node_id].evidence_refs),
                "token_usage": results[node_id].token_usage,
                "model_calls": results[node_id].model_calls,
                "tool_count": results[node_id].tool_count,
            }
            for node_id in plan.topological_order
        ],
    }


def _tool_content(
    plan: TaskDAGPlan,
    results: Mapping[str, TaskDAGNodeResult],
) -> str:
    lines = [
        f"Task DAG {task_dag_run_status(results)}. " f"Plan hash: {plan.dag_hash}. Node results:"
    ]
    for node_id in plan.topological_order:
        result = results[node_id]
        line = f"- {node_id}: {result.status}"
        if result.result:
            line += f"\n{result.result.strip()[:_RESULT_PER_NODE_MAX]}"
        elif result.error_code:
            line += f" ({result.error_code})"
        lines.append(line)
    return "\n".join(lines)[:_RESULT_TOTAL_MAX]


def _metadata(entry: Mapping[str, object]) -> dict[str, object]:
    return {
        key: entry.get(key, 0 if key.endswith("_count") else "")
        for key in (
            "dag_id",
            "dag_hash",
            "status",
            "node_count",
            "completed_count",
            "failed_count",
            "blocked_count",
        )
    }


def build_task_dag_tool(
    executor: SubagentExecutorPort,
    config: SubagentToolConfig | None = None,
) -> BaseTool:
    """构造父级 Task DAG 工具；节点仍走现有 Subagent 安全边界。"""
    effective = config or SubagentToolConfig()

    @tool("task_dag")
    async def task_dag_tool(
        nodes: list[dict[str, Any]],
        max_concurrent: int,
        runtime: ToolRuntime[HarnessRunContext, SageThreadState],
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> Command[Any]:
        """Validate and run a bounded dependency graph of registered subagents.

        Use this only when two or more independent bounded tasks can run before
        one or more dependent tasks. The server validates the graph, freezes its
        hash, reserves the whole child budget, and keeps practice nodes exclusive.
        Every node still uses the existing permission, policy, approval, and
        sandbox path. Do not create nested DAGs or request unregistered profiles.
        """
        try:
            plan = _plan_from_arguments(nodes, max_concurrent, effective)
        except (TaskDAGPlanValidationError, TypeError, ValueError):
            return _error_command(
                tool_call_id=tool_call_id,
                error_code="task_dag_invalid",
            )

        context = runtime.context
        dag_run_id = derive_task_dag_run_id(
            context.thread_id,
            context.run_id,
            tool_call_id,
            plan.dag_hash,
        )
        previous = _existing_graph(
            runtime.state,
            run_id=context.run_id,
            tool_call_id=tool_call_id,
        )
        if previous is not None and previous.get("dag_hash") != plan.dag_hash:
            return _error_command(
                tool_call_id=tool_call_id,
                error_code="task_dag_resume_hash_mismatch",
                dag_id=dag_run_id,
                dag_hash=plan.dag_hash,
            )
        if previous is not None and previous.get("status") in _TERMINAL_GRAPH_STATUSES:
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content=(
                                "Task DAG terminal receipt was reused for this resume. "
                                f"Status: {previous.get('status', 'failed')}."
                            ),
                            tool_call_id=tool_call_id,
                            name="task_dag",
                            status=(
                                "success" if previous.get("status") == "succeeded" else "error"
                            ),
                            additional_kwargs={"sage_task_dag": _metadata(previous)},
                        )
                    ]
                }
            )

        child_ids = {
            node.node_id: derive_child_run_id(
                context.thread_id,
                context.run_id,
                f"{tool_call_id}:{node.node_id}",
            )
            for node in plan.nodes
        }
        if any(not _has_reservation(runtime.state, child_ids[node.node_id]) for node in plan.nodes):
            return _error_command(
                tool_call_id=tool_call_id,
                error_code="task_dag_reservation_missing",
                dag_id=dag_run_id,
                dag_hash=plan.dag_hash,
            )

        writer = runtime.stream_writer
        writer(
            {
                "type": "task_dag_started",
                "dag_id": dag_run_id,
                "dag_hash": plan.dag_hash,
                "tool_call_id": tool_call_id,
                "status": "running",
                "node_count": len(plan.nodes),
                "max_concurrent": plan.max_concurrent,
            }
        )
        nodes_by_id = {node.node_id: node for node in plan.nodes}
        requests: dict[str, SubagentRequest] = {}
        terminal_results: dict[str, SubagentResult] = {}
        base_evidence = _state_strings(runtime.state, "evidence_refs")
        base_query_fingerprints = _state_strings(
            runtime.state,
            "evidence_query_fingerprints",
        )
        base_source_fingerprints = _state_strings(
            runtime.state,
            "evidence_source_fingerprints",
        )
        base_child_ids = _evidence_child_run_ids(runtime.state, context.run_id)

        async def execute_node(
            node: TaskDAGNode,
            dependencies: Mapping[str, TaskDAGNodeResult],
        ) -> TaskDAGNodeResult:
            profile = effective.resolve(node.subagent_type)
            assert profile is not None  # plan validation already fixed the server profile.
            dependency_evidence = tuple(
                reference
                for dependency in dependencies.values()
                for reference in dependency.evidence_refs
            )
            dependency_child_ids = tuple(
                child_ids[node_id]
                for node_id, result in dependencies.items()
                if result.status == "succeeded"
                and result.evidence_refs
                and nodes_by_id[node_id].subagent_type == "research"
            )
            child_run_id = child_ids[node.node_id]
            request = SubagentRequest(
                parent_thread_id=context.thread_id,
                parent_run_id=context.run_id,
                child_run_id=child_run_id,
                description=node.description,
                prompt=node.prompt,
                subagent_type=node.subagent_type,
                workspace_id=context.workspace_id,
                workspace_path=context.workspace_path,
                tool_scope=profile.tool_scope,
                token_budget=_reserved_token_budget(
                    runtime.state,
                    child_run_id,
                    profile.token_budget,
                ),
                timeout_seconds=profile.timeout_seconds,
                max_steps=_reserved_max_steps(
                    runtime.state,
                    child_run_id,
                    profile.max_steps,
                ),
                evidence_refs=tuple(dict.fromkeys((*base_evidence, *dependency_evidence))),
                evidence_child_run_ids=tuple(
                    dict.fromkeys((*base_child_ids, *dependency_child_ids))
                ),
                query_fingerprints=tuple(
                    dict.fromkeys(
                        (
                            *base_query_fingerprints,
                            *(
                                item
                                for dependency in dependencies.values()
                                for item in dependency.query_fingerprints
                            ),
                        )
                    )
                ),
                source_fingerprints=tuple(
                    dict.fromkeys(
                        (
                            *base_source_fingerprints,
                            *(
                                item
                                for dependency in dependencies.values()
                                for item in dependency.source_fingerprints
                            ),
                        )
                    )
                ),
            )
            requests[node.node_id] = request
            writer(
                {
                    "type": "task_dag_node_started",
                    "dag_id": dag_run_id,
                    "dag_hash": plan.dag_hash,
                    "node_id": node.node_id,
                    "child_run_id": child_run_id,
                    "status": "running",
                }
            )
            result = await _execute_subagent_request(
                executor=executor,
                request=request,
                writer=writer,
                profile_available=True,
                allowed_types=effective.allowed_types,
            )
            terminal_results[node.node_id] = result
            node_result = TaskDAGNodeResult(
                node_id=node.node_id,
                status=result.status,
                result=result.result,
                result_ref=result.result_ref,
                error_code=result.error_code,
                evidence_refs=result.evidence_refs,
                query_fingerprints=result.query_fingerprints,
                source_fingerprints=result.source_fingerprints,
                token_usage=result.token_usage,
                model_calls=result.model_calls,
                tool_count=result.tool_count,
            )
            writer(
                {
                    "type": f"task_dag_node_{'completed' if result.status == 'succeeded' else 'failed'}",
                    "dag_id": dag_run_id,
                    "dag_hash": plan.dag_hash,
                    "node_id": node.node_id,
                    "child_run_id": child_run_id,
                    "status": result.status,
                    "error_code": result.error_code,
                    "evidence_count": len(result.evidence_refs),
                    "token_usage": result.token_usage,
                    "model_calls": result.model_calls,
                    "tool_count": result.tool_count,
                }
            )
            return node_result

        results = await TaskDAGScheduler(plan).run(execute_node)
        for node_id, result in results.items():
            if node_id in terminal_results:
                continue
            node = nodes_by_id[node_id]
            profile = cast(Any, effective.resolve(node.subagent_type))
            request = SubagentRequest(
                parent_thread_id=context.thread_id,
                parent_run_id=context.run_id,
                child_run_id=child_ids[node_id],
                description=node.description,
                prompt=node.prompt,
                subagent_type=node.subagent_type,
                workspace_id=context.workspace_id,
                workspace_path=context.workspace_path,
                tool_scope=profile.tool_scope,
                token_budget=_reserved_token_budget(
                    runtime.state,
                    child_ids[node_id],
                    profile.token_budget,
                ),
                timeout_seconds=profile.timeout_seconds,
                max_steps=_reserved_max_steps(
                    runtime.state,
                    child_ids[node_id],
                    profile.max_steps,
                ),
            )
            requests[node_id] = request
            terminal_results[node_id] = SubagentResult(
                child_run_id=child_ids[node_id],
                status="failed",
                error_code=result.error_code or "dependency_failed",
            )
            writer(
                {
                    "type": "task_dag_node_blocked",
                    "dag_id": dag_run_id,
                    "dag_hash": plan.dag_hash,
                    "node_id": node_id,
                    "child_run_id": child_ids[node_id],
                    "status": "blocked",
                    "error_code": result.error_code or "dependency_failed",
                }
            )

        entry = _graph_entry(
            plan=plan,
            dag_run_id=dag_run_id,
            run_id=context.run_id,
            tool_call_id=tool_call_id,
            results=results,
            child_ids=child_ids,
        )
        writer({"type": "task_dag_completed", **_metadata(entry)})
        evidence_refs = tuple(
            dict.fromkeys(
                reference
                for result in terminal_results.values()
                for reference in result.evidence_refs
            )
        )
        query_fingerprints = tuple(
            dict.fromkeys(
                reference
                for result in terminal_results.values()
                for reference in result.query_fingerprints
            )
        )
        source_fingerprints = tuple(
            dict.fromkeys(
                reference
                for result in terminal_results.values()
                for reference in result.source_fingerprints
            )
        )
        return Command(
            update={
                "task_graphs": [entry],
                "delegations": [
                    _delegation_entry(requests[node_id], terminal_results[node_id])
                    for node_id in plan.topological_order
                ],
                "evidence_refs": list(evidence_refs),
                "evidence_query_fingerprints": list(query_fingerprints),
                "evidence_source_fingerprints": list(source_fingerprints),
                "messages": [
                    ToolMessage(
                        content=_tool_content(plan, results),
                        tool_call_id=tool_call_id,
                        name="task_dag",
                        status=("success" if entry["status"] == "succeeded" else "error"),
                        additional_kwargs={"sage_task_dag": _metadata(entry)},
                    )
                ],
            }
        )

    return task_dag_tool


__all__ = ["build_task_dag_tool"]
