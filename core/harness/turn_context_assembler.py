"""把已选择的运行时输入收敛为一份不可变 Turn Context Plan。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import uuid4

from sage_harness import HarnessConfig, SandboxDescriptor

from core.coding.context import PreparedContext
from core.coding.persistence.turn_plan_store import TurnPlanStore
from core.harness.context_adapter import DeerFlowPromptComponents
from core.harness.retrieval_gate import RetrievalGateReceipt
from core.harness.task_intent import TaskIntentEnvelope
from core.harness.tool_bundle import ToolBundleSnapshot
from core.harness.turn_context_plan import TurnContextPlan, build_turn_context_plan_receipt

_PROMPT_TEMPLATE_ID = "sage-deerflow-coding"
_PROMPT_REVISION = "2026-08-07.1"
ContextAssemblyMode = Literal["off", "shadow", "enforce"]


class TurnContextAssemblyError(ValueError):
    """本轮选择无法安全表达为不可变 Plan。"""


def normalize_context_assembly_mode(value: object) -> ContextAssemblyMode:
    """规范化渐进迁移模式；enforce 会把 Plan 校验提升为 Graph 前置门禁。"""
    normalized = str(value).strip().lower() or "shadow"
    if normalized not in {"off", "shadow", "enforce"}:
        raise ValueError(f"unsupported context assembly mode: {normalized}")
    return cast(ContextAssemblyMode, normalized)


@dataclass(frozen=True, slots=True)
class TurnContextAssemblyRequest:
    """一个新 Turn 的已选输入；该请求不持有任何外部 Port。"""

    session_id: str
    run_id: str
    owner_id: str
    workspace_id: str
    input_origin: str
    user_message: Mapping[str, object]
    surface_context: Mapping[str, Any] | None
    thread_goal: Mapping[str, Any] | None
    prepared_context: PreparedContext
    durable_context: Mapping[str, object]
    prompt_components: DeerFlowPromptComponents
    rendered_system_prompt: str
    retrieval_gate: RetrievalGateReceipt
    tool_snapshot: ToolBundleSnapshot
    sandbox_descriptor: SandboxDescriptor
    harness_config: HarnessConfig
    runtime_mode: str
    permission_mode: str
    model_spec: str = ""
    surface: str = "coding"
    intent_envelope: TaskIntentEnvelope | None = None


@dataclass(frozen=True, slots=True)
class AssembledTurnContext:
    """已持久化的 Plan 与不含正文的 Timeline receipt。"""

    plan: TurnContextPlan
    receipt: Mapping[str, Any]


class TurnContextAssembler:
    """不重新查询 Memory、MCP、Retrieval 或工具，构造并持久化一份 Plan。"""

    def __init__(
        self,
        store: TurnPlanStore,
        *,
        plan_id_factory: Callable[[], str] | None = None,
        clock: Callable[[], str] | None = None,
    ) -> None:
        """绑定 Plan Store 和可测试的 ID/时钟生成器。"""
        self._store = store
        self._plan_id_factory = plan_id_factory or (lambda: f"tcp-{uuid4().hex}")
        self._clock = clock or (lambda: datetime.now(UTC).isoformat())

    def prepare_new_turn(self, request: TurnContextAssemblyRequest) -> AssembledTurnContext:
        """把本轮既有选择净化为不可变 Plan；不触发任何外部发现或检索。"""
        if request.session_id != self._store.session_id:
            raise TurnContextAssemblyError("turn context assembler session scope mismatch")
        plan = TurnContextPlan.create(
            plan_id=self._plan_id_factory(),
            session_id=request.session_id,
            run_id=request.run_id,
            owner_fingerprint=_digest_text(request.owner_id),
            workspace_id=request.workspace_id,
            surface=request.surface,
            created_at=self._clock(),
            admission=_admission(request),
            prompt=_prompt(request),
            context_refs=_context_refs(request),
            retrieval=_retrieval(request.retrieval_gate),
            tools=_tools(request),
            execution=_execution(request),
            resume={
                "checkpoint_thread_id": request.session_id,
                "checkpoint_namespace": "",
                "recovery_policy": "fail_closed",
            },
            budget=_budget(request),
        )
        persisted = self._store.put_if_absent(plan)
        return AssembledTurnContext(
            plan=persisted,
            receipt=build_turn_context_plan_receipt(persisted),
        )


def _admission(request: TurnContextAssemblyRequest) -> dict[str, object]:
    """仅保存输入引用和不可逆摘要，不保存用户或页面正文。"""
    content = str(request.user_message.get("content", "")).strip()
    if not content:
        raise TurnContextAssemblyError("turn user message content is required")
    message_id = str(request.user_message.get("message_id", "")).strip()
    sequence = _positive_int(request.user_message.get("sequence"), "user message sequence")
    admission: dict[str, object] = {
        "input_origin": request.input_origin,
        "input_fingerprint": _digest_text(content),
        "user_input_ref": {"message_id": message_id, "sequence": sequence},
    }
    if request.surface_context is not None:
        admission["surface_context_ref"] = {"digest": _digest_json(request.surface_context)}
    if request.thread_goal is not None:
        admission["thread_goal_ref"] = {
            "goal_id": str(request.thread_goal.get("goal_id", ""))[:128],
            "revision": _non_negative_int(request.thread_goal.get("revision")),
            "digest": _digest_json(request.thread_goal),
        }
    if request.intent_envelope is not None:
        admission["task_intent"] = request.intent_envelope.as_dict()
    return admission


def _prompt(request: TurnContextAssemblyRequest) -> dict[str, object]:
    """静态规则保留正文，其余动态或不可信块只保存结构与 digest。"""
    components = request.prompt_components
    dynamic_contract = {
        "retrieval_tool_scope": request.retrieval_gate.tool_scope,
        "retrieval_sources": sorted(request.retrieval_gate.selected_sources),
        "rendered_content_hash": _digest_text(components.dynamic_authority),
    }
    snapshot = request.tool_snapshot
    return {
        "rendered_prompt_hash": _digest_text(request.rendered_system_prompt),
        "static_policy": {
            "template_id": _PROMPT_TEMPLATE_ID,
            "revision": _PROMPT_REVISION,
            "rendered_content": components.static_policy,
        },
        "dynamic_authority": dynamic_contract,
        "untrusted_context": {
            "working_memory_digest": _digest_text(components.untrusted_context),
        },
        "deferred_capability_index": {
            "catalog_hash": snapshot.catalog_hash,
            "safe_index_digest": _digest_json(snapshot.deferred_ids),
        },
    }


def _context_refs(request: TurnContextAssemblyRequest) -> dict[str, object]:
    """把 Transcript、Compaction 和 Memory 正文降为可验证引用。"""
    sequence = _positive_int(request.user_message.get("sequence"), "user message sequence")
    refs: dict[str, object] = {
        "user_message_ref": {
            "message_id": str(request.user_message.get("message_id", ""))[:160],
            "sequence": sequence,
        },
        "transcript_range": [1, sequence],
        "memory_refs": _memory_refs(request.durable_context),
        "artifact_refs": [],
        "projected_history_digest": _digest_json(request.prepared_context.projected_history),
        "durable_context_digest": _digest_json(request.durable_context),
    }
    summary_text = str(request.durable_context.get("summary_text", "")).strip()
    compaction = request.prepared_context.compaction_result
    if summary_text:
        summary_ref: dict[str, object] = {"digest": _digest_text(summary_text)}
        if compaction is not None:
            summary_ref["compaction_id"] = compaction.compaction_id
            checkpoint = compaction.checkpoint
            if checkpoint is not None:
                summary_ref["source_transcript_range"] = [
                    checkpoint.transcript_start,
                    checkpoint.transcript_end,
                ]
        refs["compaction_ref"] = summary_ref
    return refs


def _memory_refs(durable_context: Mapping[str, object]) -> list[dict[str, object]]:
    raw_refs = durable_context.get("memory_refs")
    if not isinstance(raw_refs, Sequence) or isinstance(raw_refs, str | bytes | bytearray):
        return []
    refs: list[dict[str, object]] = []
    for raw in raw_refs[:32]:
        if not isinstance(raw, Mapping):
            continue
        memory_id = str(raw.get("memory_id", "")).strip()[:120]
        if not memory_id:
            continue
        refs.append(
            {
                "memory_id": memory_id,
                "revision": str(raw.get("revision", ""))[:80],
                "digest": _digest_text(str(raw.get("summary", ""))),
            }
        )
    return refs


def _retrieval(gate: RetrievalGateReceipt) -> dict[str, object]:
    return {
        "decision": gate.decision,
        "reason_code": gate.reason_code,
        "selected_sources": sorted(gate.selected_sources),
        "available_sources": sorted(gate.available_sources),
        "token_budget_by_source": dict(sorted(gate.token_budget_by_source.items())),
        "query_fingerprint": gate.query_fingerprint,
        "degraded": gate.degraded,
        "tool_scope": gate.tool_scope,
    }


def _tools(request: TurnContextAssemblyRequest) -> dict[str, object]:
    snapshot = request.tool_snapshot
    tools: dict[str, object] = {
        **snapshot.as_dict(),
        "approval_contract_digest": _digest_json(
            {"graph_approvals": True, "permission_mode": request.permission_mode}
        ),
    }
    return tools


def _execution(request: TurnContextAssemblyRequest) -> dict[str, object]:
    """冻结运行限制和 Sandbox 能力，不保存内部 sandbox/container identity。"""
    descriptor = request.sandbox_descriptor
    capabilities = descriptor.capabilities
    sandbox_contract: dict[str, object] = {
        "provider": descriptor.provider,
        "workspace_id": descriptor.workspace_id,
        "capabilities": {
            "isolated": capabilities.isolated,
            "host_access": capabilities.host_access,
            "read_files": capabilities.read_files,
            "write_files": capabilities.write_files,
            "shell": capabilities.shell,
        },
    }
    config = request.harness_config
    return {
        "runtime_mode": request.runtime_mode,
        "permission_mode": request.permission_mode,
        "model_spec": request.model_spec,
        "harness_limits": {
            "max_model_calls": config.max_model_calls,
            "max_tool_calls": config.max_tool_calls,
            "max_run_tokens": config.max_run_tokens,
            "recursion_limit": config.recursion_limit,
            "max_run_seconds": config.max_run_seconds,
        },
        "sandbox": sandbox_contract,
        "sandbox_fingerprint": _digest_json(sandbox_contract),
    }


def _budget(request: TurnContextAssemblyRequest) -> dict[str, object]:
    usage = request.prepared_context.usage
    return {
        "estimated_input_tokens": usage.used_tokens,
        "effective_limit_tokens": usage.effective_limit_tokens,
        "estimated": usage.estimated,
        "level": usage.level,
        "token_budget_by_source": dict(
            sorted(request.retrieval_gate.token_budget_by_source.items())
        ),
        "max_run_tokens": request.harness_config.max_run_tokens,
    }


def _digest_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _digest_json(value: object) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise TurnContextAssemblyError("turn context reference is not strict JSON") from exc
    return _digest_text(encoded)


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise TurnContextAssemblyError(f"{field} must be positive")
    return value


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


__all__ = [
    "AssembledTurnContext",
    "ContextAssemblyMode",
    "TurnContextAssembler",
    "TurnContextAssemblyError",
    "TurnContextAssemblyRequest",
    "normalize_context_assembly_mode",
]
