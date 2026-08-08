"""Plan 权威的恢复校验与运行时依赖比较。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Set
from dataclasses import dataclass
from typing import Any

from sage_harness import (
    HarnessConfig,
    McpToolSnapshot,
    SandboxDescriptor,
    TurnContextPlanBinding,
    normalize_turn_context_plan_binding,
)

from core.harness.context_adapter import DeerFlowPromptComponents
from core.harness.tools_adapter import CodingToolBundle
from core.harness.turn_context_plan import TurnContextPlan


class TurnContextResumeError(RuntimeError):
    """恢复所需 Plan、scope 或冻结路由不可信。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class PreparedTurnContextResume:
    """从 Plan 投影出的恢复输入，不包含用户正文或完整 Plan。"""

    binding: TurnContextPlanBinding
    retrieval_sources: frozenset[str]
    retrieval_tool_scope: str
    active_skill_allowed_tools: frozenset[str] | None


@dataclass(frozen=True, slots=True)
class TurnContextResumeExecutionRequest:
    """恢复前用当前实际依赖生成的有界比较输入。"""

    prompt_components: DeerFlowPromptComponents
    rendered_system_prompt: str
    retrieval_sources: Set[str]
    retrieval_tool_scope: str
    tool_bundle: CodingToolBundle
    sandbox_descriptor: SandboxDescriptor
    harness_config: HarnessConfig
    runtime_mode: str
    permission_mode: str
    model_spec: str
    mcp_snapshot: McpToolSnapshot | None
    active_skill_allowed_tools: frozenset[str] | None


@dataclass(frozen=True, slots=True)
class TurnContextResumeComparison:
    """恢复比较只输出固定 mismatch code，禁止把依赖正文写入审计。"""

    plan_id: str
    plan_hash: str
    run_id: str
    checked_codes: tuple[str, ...]
    mismatch_codes: tuple[str, ...]

    @property
    def matched(self) -> bool:
        """只有所有冻结依赖都一致时才允许创建 Graph Adapter。"""
        return not self.mismatch_codes

    def to_receipt(self) -> dict[str, Any]:
        """生成无正文的恢复比较 receipt，供 Timeline/Trace 审计。"""
        return {
            "type": "turn_context_plan_resume_compared",
            "version": 1,
            "run_id": self.run_id,
            "plan_id": self.plan_id,
            "plan_hash": self.plan_hash,
            "matched": self.matched,
            "checked_count": len(self.checked_codes),
            "mismatch_codes": list(self.mismatch_codes),
        }


def prepare_turn_context_resume(
    plan: TurnContextPlan,
    *,
    session_id: str,
    run_id: str,
    owner_id: str,
    workspace_id: str,
    surface: str,
) -> PreparedTurnContextResume:
    """校验 Plan 身份并从冻结字段恢复 routing，绝不从 Timeline 反推。"""
    if (
        plan.session_id != session_id
        or plan.run_id != run_id
        or plan.owner_fingerprint != _digest_text(owner_id)
        or plan.workspace_id != workspace_id
        or plan.surface != surface
    ):
        raise TurnContextResumeError("resume_plan_scope_mismatch")
    payload = plan.to_payload()
    resume = _mapping(payload.get("resume"))
    if resume.get("checkpoint_thread_id") != session_id or resume.get("checkpoint_namespace", ""):
        raise TurnContextResumeError("resume_plan_checkpoint_scope_mismatch")
    if resume.get("recovery_policy") != "fail_closed":
        raise TurnContextResumeError("resume_plan_recovery_policy_invalid")

    retrieval = _mapping(payload.get("retrieval"))
    selected_sources = retrieval.get("selected_sources", [])
    if not isinstance(selected_sources, list) or not all(
        isinstance(item, str) and item for item in selected_sources
    ):
        raise TurnContextResumeError("resume_plan_retrieval_invalid")
    retrieval_tool_scope = retrieval.get("tool_scope", "default")
    if retrieval_tool_scope not in {"default", "retrieval_only", "no_tools"}:
        raise TurnContextResumeError("resume_plan_retrieval_scope_invalid")

    tools = _mapping(payload.get("tools"))
    raw_allowlist = tools.get("skill_allowlist", [])
    if not isinstance(raw_allowlist, list) or not all(
        isinstance(item, str) and item for item in raw_allowlist
    ):
        raise TurnContextResumeError("resume_plan_tool_scope_invalid")
    skill_scope_active = tools.get("skill_scope_active")
    if not isinstance(skill_scope_active, bool):
        raise TurnContextResumeError("resume_plan_tool_scope_invalid")
    if not skill_scope_active and raw_allowlist:
        raise TurnContextResumeError("resume_plan_tool_scope_invalid")
    return PreparedTurnContextResume(
        binding=normalize_turn_context_plan_binding(plan.checkpoint_binding()),
        retrieval_sources=frozenset(selected_sources),
        retrieval_tool_scope=str(retrieval_tool_scope),
        active_skill_allowed_tools=(frozenset(raw_allowlist) if skill_scope_active else None),
    )


def compare_turn_context_plan_resume(
    plan: TurnContextPlan,
    request: TurnContextResumeExecutionRequest,
) -> TurnContextResumeComparison:
    """比较恢复时实际 Prompt/Tool/Sandbox 与 Plan，失败只返回固定 code。"""
    payload = plan.to_payload()
    prompt = _mapping(payload.get("prompt"))
    retrieval = _mapping(payload.get("retrieval"))
    tools = _mapping(payload.get("tools"))
    execution = _mapping(payload.get("execution"))
    expected_sandbox = _sandbox_payload(request.sandbox_descriptor)
    config = request.harness_config
    expected_limits = {
        "max_model_calls": config.max_model_calls,
        "max_tool_calls": config.max_tool_calls,
        "max_run_tokens": config.max_run_tokens,
        "recursion_limit": config.recursion_limit,
        "max_run_seconds": config.max_run_seconds,
    }
    checks: list[tuple[str, object, object]] = [
        (
            "prompt.rendered_hash",
            prompt.get("rendered_prompt_hash"),
            _digest_text(request.rendered_system_prompt),
        ),
        (
            "prompt.dynamic_authority_hash",
            _nested(prompt, "dynamic_authority", "rendered_content_hash"),
            _digest_text(request.prompt_components.dynamic_authority),
        ),
        (
            "prompt.untrusted_context_hash",
            _nested(prompt, "untrusted_context", "working_memory_digest"),
            _digest_text(request.prompt_components.untrusted_context),
        ),
        ("retrieval.tool_scope", retrieval.get("tool_scope"), request.retrieval_tool_scope),
        (
            "retrieval.selected_sources",
            retrieval.get("selected_sources"),
            sorted(request.retrieval_sources),
        ),
        (
            "tools.catalog_hash",
            tools.get("catalog_hash"),
            request.tool_bundle.deferred_setup.catalog_hash or "resident-only",
        ),
        (
            "tools.capability_revision",
            tools.get("capability_revision"),
            request.tool_bundle.capability_revision,
        ),
        (
            "tools.resident_ids",
            tools.get("resident_ids"),
            sorted(set(request.tool_bundle.capability_ids_by_tool_name.values())),
        ),
        (
            "tools.deferred_ids",
            tools.get("deferred_ids"),
            _deferred_capability_ids(request.tool_bundle),
        ),
        (
            "tools.skill_scope_active",
            tools.get("skill_scope_active"),
            request.active_skill_allowed_tools is not None,
        ),
        (
            "tools.skill_allowlist",
            tools.get("skill_allowlist"),
            sorted(request.active_skill_allowed_tools or ()),
        ),
        ("tools.mcp_catalog", tools.get("mcp_catalog"), _mcp_catalog(request.mcp_snapshot)),
        (
            "execution.runtime",
            (
                execution.get("runtime_mode"),
                execution.get("permission_mode"),
                execution.get("model_spec"),
            ),
            (request.runtime_mode, request.permission_mode, request.model_spec),
        ),
        ("execution.sandbox", execution.get("sandbox"), expected_sandbox),
        (
            "execution.sandbox_fingerprint",
            execution.get("sandbox_fingerprint"),
            _digest_json(expected_sandbox),
        ),
        ("execution.harness_limits", execution.get("harness_limits"), expected_limits),
    ]
    return TurnContextResumeComparison(
        plan_id=plan.plan_id,
        plan_hash=plan.plan_hash,
        run_id=plan.run_id,
        checked_codes=tuple(code for code, _, _ in checks),
        mismatch_codes=tuple(code for code, stored, actual in checks if stored != actual),
    )


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _nested(value: Mapping[str, Any], *path: str) -> object:
    current: object = value
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _digest_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _digest_json(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
    )
    return _digest_text(encoded)


def _sandbox_payload(descriptor: SandboxDescriptor) -> dict[str, object]:
    capabilities = descriptor.capabilities
    return {
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


def _deferred_capability_ids(bundle: CodingToolBundle) -> list[str]:
    selection_index = bundle.deferred_setup.selection_index
    if selection_index is None:
        return []
    return sorted(
        descriptor.capability_id
        for descriptor in selection_index.registry.list()
        if descriptor.deferred
    )


def _mcp_catalog(snapshot: McpToolSnapshot | None) -> dict[str, object] | None:
    if snapshot is None:
        return None
    catalog = snapshot.catalog
    return {
        "revision": catalog.revision,
        "catalog_hash": catalog.catalog_hash,
        "tool_ids": sorted(tool.tool_id for tool in catalog.tools),
    }


__all__ = [
    "PreparedTurnContextResume",
    "TurnContextResumeComparison",
    "TurnContextResumeError",
    "TurnContextResumeExecutionRequest",
    "compare_turn_context_plan_resume",
    "prepare_turn_context_resume",
]
