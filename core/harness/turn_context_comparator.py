"""A1 shadow comparator：核对不可变 Plan 与本轮实际 Graph 输入。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from core.harness.turn_context_assembler import TurnContextAssemblyRequest
from core.harness.turn_context_plan import TurnContextPlan


@dataclass(frozen=True, slots=True)
class TurnContextComparison:
    """仅携带安全检查码的比较结果，不保存被比较的正文或真实值。"""

    plan_id: str
    plan_hash: str
    run_id: str
    checked_codes: tuple[str, ...]
    mismatch_codes: tuple[str, ...]

    @property
    def matched(self) -> bool:
        """所有检查通过时才视为等价。"""
        return not self.mismatch_codes

    def to_receipt(self) -> dict[str, Any]:
        """生成可进入 Timeline/Trace 的脱敏 A1 比较收据。"""
        return {
            "type": "turn_context_plan_compared",
            "version": 1,
            "run_id": self.run_id,
            "plan_id": self.plan_id,
            "plan_hash": self.plan_hash,
            "matched": self.matched,
            "checked_count": len(self.checked_codes),
            "mismatch_codes": list(self.mismatch_codes),
        }


def compare_turn_context_plan(
    plan: TurnContextPlan,
    request: TurnContextAssemblyRequest,
) -> TurnContextComparison:
    """逐项核对 Plan 与即将交给 Graph 的实际输入，A1 只观测不阻断。"""
    payload = plan.to_payload()
    prompt = _mapping(payload.get("prompt"))
    context_refs = _mapping(payload.get("context_refs"))
    retrieval = _mapping(payload.get("retrieval"))
    tools = _mapping(payload.get("tools"))
    execution = _mapping(payload.get("execution"))
    budget = _mapping(payload.get("budget"))
    resume = _mapping(payload.get("resume"))
    user_content = str(request.user_message.get("content", "")).strip()
    usage = request.prepared_context.usage
    sandbox = request.sandbox_descriptor
    capabilities = sandbox.capabilities
    expected_sandbox = {
        "provider": sandbox.provider,
        "workspace_id": sandbox.workspace_id,
        "capabilities": {
            "isolated": capabilities.isolated,
            "host_access": capabilities.host_access,
            "read_files": capabilities.read_files,
            "write_files": capabilities.write_files,
            "shell": capabilities.shell,
        },
    }
    config = request.harness_config
    expected_limits = {
        "max_model_calls": config.max_model_calls,
        "max_tool_calls": config.max_tool_calls,
        "max_run_tokens": config.max_run_tokens,
        "recursion_limit": config.recursion_limit,
        "max_run_seconds": config.max_run_seconds,
    }

    checks: list[tuple[str, object, object]] = [
        ("identity.session", plan.session_id, request.session_id),
        ("identity.run", plan.run_id, request.run_id),
        ("identity.owner", plan.owner_fingerprint, _digest_text(request.owner_id)),
        ("identity.workspace", plan.workspace_id, request.workspace_id),
        ("identity.surface", plan.surface, request.surface),
        (
            "admission.input_fingerprint",
            _nested(payload, "admission", "input_fingerprint"),
            _digest_text(user_content),
        ),
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
        (
            "context.projected_history_digest",
            context_refs.get("projected_history_digest"),
            _digest_json(request.prepared_context.projected_history),
        ),
        (
            "context.durable_context_digest",
            context_refs.get("durable_context_digest"),
            _digest_json(request.durable_context),
        ),
        ("retrieval.tool_scope", retrieval.get("tool_scope"), request.retrieval_gate.tool_scope),
        (
            "retrieval.selected_sources",
            retrieval.get("selected_sources"),
            sorted(request.retrieval_gate.selected_sources),
        ),
        (
            "retrieval.token_budget",
            retrieval.get("token_budget_by_source"),
            dict(sorted(request.retrieval_gate.token_budget_by_source.items())),
        ),
        (
            "tools.snapshot_hash",
            tools.get("snapshot_hash"),
            request.tool_snapshot.snapshot_hash,
        ),
        (
            "tools.catalog_hash",
            tools.get("catalog_hash"),
            request.tool_snapshot.catalog_hash,
        ),
        (
            "tools.capability_revision",
            tools.get("capability_revision"),
            request.tool_snapshot.capability_revision,
        ),
        (
            "tools.resident_ids",
            tools.get("resident_ids"),
            list(request.tool_snapshot.resident_ids),
        ),
        (
            "tools.deferred_ids",
            tools.get("deferred_ids"),
            list(request.tool_snapshot.deferred_ids),
        ),
        (
            "tools.skill_scope_active",
            tools.get("skill_scope_active"),
            request.tool_snapshot.skill_scope_active,
        ),
        (
            "tools.skill_allowlist",
            tools.get("skill_allowlist"),
            list(request.tool_snapshot.skill_allowlist),
        ),
        (
            "tools.mcp_lifecycle",
            tools.get("mcp_lifecycle"),
            _lifecycle_payload(request.tool_snapshot.mcp_lifecycle),
        ),
        (
            "tools.skill_lifecycle",
            tools.get("skill_lifecycle"),
            _lifecycle_payload(request.tool_snapshot.skill_lifecycle),
        ),
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
        (
            "budget.context",
            (
                budget.get("estimated_input_tokens"),
                budget.get("effective_limit_tokens"),
                budget.get("estimated"),
                budget.get("level"),
                budget.get("max_run_tokens"),
            ),
            (
                usage.used_tokens,
                usage.effective_limit_tokens,
                usage.estimated,
                usage.level,
                config.max_run_tokens,
            ),
        ),
        (
            "resume.scope",
            (resume.get("checkpoint_thread_id"), resume.get("checkpoint_namespace")),
            (request.session_id, ""),
        ),
    ]
    mismatch_codes = tuple(code for code, stored, actual in checks if stored != actual)
    return TurnContextComparison(
        plan_id=plan.plan_id,
        plan_hash=plan.plan_hash,
        run_id=plan.run_id,
        checked_codes=tuple(code for code, _, _ in checks),
        mismatch_codes=mismatch_codes,
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
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return _digest_text(encoded)


def _lifecycle_payload(value: object) -> object:
    render = getattr(value, "as_dict", None)
    return render() if callable(render) else None


__all__ = ["TurnContextComparison", "compare_turn_context_plan"]
