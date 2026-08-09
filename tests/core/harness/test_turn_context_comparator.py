"""Turn Context Plan 与实际 Graph 输入的 A1 shadow 比较测试。"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from sage_harness import (
    HarnessConfig,
    McpLifecycleSnapshot,
    SandboxCapabilities,
    SandboxDescriptor,
)

from core.coding.context import ContextUsage, PreparedContext
from core.coding.persistence import TurnPlanStore
from core.coding.skills import SkillLifecycleSnapshot
from core.harness.context_adapter import DeerFlowPromptComponents
from core.harness.retrieval_gate import RetrievalGateReceipt
from core.harness.task_intent import TaskIntentEnvelope
from core.harness.tool_bundle import ToolBundleSnapshot
from core.harness.turn_context_assembler import TurnContextAssembler, TurnContextAssemblyRequest
from core.harness.turn_context_comparator import compare_turn_context_plan
from core.harness.turn_context_resume import (
    TurnContextResumeExecutionRequest,
    compare_turn_context_plan_resume,
)


def _request() -> TurnContextAssemblyRequest:
    components = DeerFlowPromptComponents(
        static_policy="server-owned static policy",
        dynamic_authority="source-locked to knowledge",
        untrusted_context="private working memory",
    )
    return TurnContextAssemblyRequest(
        session_id="session-1",
        run_id="run-1",
        owner_id="owner@example.test",
        workspace_id="workspace-1",
        input_origin="user",
        user_message={
            "message_id": "msg-user-1",
            "sequence": 7,
            "content": "private user request",
        },
        surface_context={"surface": "coding", "selected_text": "private source text"},
        thread_goal={"goal_id": "goal-1", "revision": 2, "description": "private goal"},
        prepared_context=PreparedContext.create(
            projected_history=[{"role": "user", "content": "private projected history"}],
            usage=ContextUsage(
                used_tokens=1_024,
                effective_limit_tokens=100_000,
                usage_ratio=0.01024,
                level="normal",
                estimated=False,
            ),
            allow_model_request=True,
        ),
        durable_context={
            "summary_text": "private compaction summary",
            "memory_refs": [
                {
                    "memory_id": "memory-1",
                    "revision": "3",
                    "summary": "private approved memory",
                }
            ],
        },
        prompt_components=components,
        rendered_system_prompt=components.render(),
        retrieval_gate=RetrievalGateReceipt(
            decision="knowledge",
            reason_code="explicit_source_signal",
            candidate_sources=("knowledge",),
            selected_sources=("knowledge",),
            available_sources=("knowledge",),
            token_budget_by_source={"knowledge": 3_000},
            query_fingerprint="query-sha",
            latency_ms=1,
            tool_scope="retrieval_only",
        ),
        tool_snapshot=ToolBundleSnapshot(
            catalog_hash="catalog-sha",
            capability_revision="cap-r1",
            resident_ids=("local:read_file",),
            deferred_ids=(),
            capability_count=1,
            skill_scope_active=True,
            skill_allowlist=("read_file",),
            mcp_lifecycle=McpLifecycleSnapshot(
                config_revision="mcp-r1",
                scope_fingerprint="sha256:" + "1" * 64,
                catalog_hash="mcp-catalog-r1",
                tool_ids=("docs:lookup",),
            ),
            skill_lifecycle=SkillLifecycleSnapshot(
                catalog_revision="skill-catalog-r1",
                activation_ref="skill://project/review",
                activation_revision="skill-r1",
                allowed_tools=("read_file",),
            ),
        ),
        sandbox_descriptor=SandboxDescriptor(
            sandbox_id="container:internal-id",
            provider="container",
            workspace_id="workspace-1",
            capabilities=SandboxCapabilities(
                isolated=True,
                host_access=False,
                read_files=True,
                write_files=True,
                shell=True,
            ),
        ),
        harness_config=HarnessConfig(max_run_tokens=64_000),
        runtime_mode="default",
        permission_mode="default",
        model_spec="provider:model",
        intent_envelope=TaskIntentEnvelope(
            intent_kind="review",
            requested_effects=("read",),
            capability_hints=("files",),
            explicit_constraints=("read_only",),
        ),
    )


def _capture(tmp_path: Path, request: TurnContextAssemblyRequest):  # type: ignore[no-untyped-def]
    assembler = TurnContextAssembler(
        TurnPlanStore(tmp_path, request.session_id),
        plan_id_factory=lambda: "tcp-compare",
        clock=lambda: "2026-08-08T00:00:00+00:00",
    )
    return assembler.prepare_new_turn(request)


def test_a1_comparator_matches_the_actual_graph_inputs_without_exposing_content(
    tmp_path: Path,
) -> None:
    request = _request()
    captured = _capture(tmp_path, request)

    comparison = compare_turn_context_plan(captured.plan, request)
    receipt = comparison.to_receipt()
    serialized = json.dumps(receipt, ensure_ascii=False, sort_keys=True)

    assert comparison.matched is True
    assert comparison.mismatch_codes == ()
    assert receipt["type"] == "turn_context_plan_compared"
    assert receipt["checked_count"] >= 10
    for private_content in (
        "private user request",
        "private projected history",
        "private compaction summary",
        "private approved memory",
        "private working memory",
        "server-owned static policy",
    ):
        assert private_content not in serialized


def test_a1_comparator_reports_prompt_and_catalog_drift_as_codes_only(tmp_path: Path) -> None:
    original = _request()
    captured = _capture(tmp_path, original)
    drifted_snapshot = replace(
        original.tool_snapshot, catalog_hash="changed-catalog", snapshot_hash=""
    )
    drifted = replace(
        original,
        rendered_system_prompt="changed private prompt",
        tool_snapshot=drifted_snapshot,
    )

    comparison = compare_turn_context_plan(captured.plan, drifted)
    serialized = json.dumps(comparison.to_receipt(), sort_keys=True)

    assert comparison.matched is False
    assert comparison.mismatch_codes == (
        "prompt.rendered_hash",
        "tools.snapshot_hash",
        "tools.catalog_hash",
    )
    assert "changed private prompt" not in serialized
    assert "changed-catalog" not in serialized


def test_a1_comparator_rejects_task_intent_drift_without_echoing_input(tmp_path: Path) -> None:
    original = _request()
    captured = _capture(tmp_path, original)
    drifted = replace(
        original,
        intent_envelope=TaskIntentEnvelope(
            intent_kind="code_change",
            requested_effects=("read", "write"),
            capability_hints=("files",),
        ),
    )

    comparison = compare_turn_context_plan(captured.plan, drifted)

    assert comparison.mismatch_codes == ("admission.task_intent",)
    assert "private user request" not in json.dumps(comparison.to_receipt())


def test_resume_comparator_reports_typed_mcp_and_skill_lifecycle_drift(
    tmp_path: Path,
) -> None:
    original = _request()
    captured = _capture(tmp_path, original)
    assert original.tool_snapshot.mcp_lifecycle is not None
    assert original.tool_snapshot.skill_lifecycle is not None
    drifted_mcp = replace(
        original.tool_snapshot.mcp_lifecycle,
        catalog_hash="private-mcp-drift",
        snapshot_hash="",
    )
    drifted_skill = replace(
        original.tool_snapshot.skill_lifecycle,
        catalog_revision="private-skill-drift",
        snapshot_hash="",
    )
    drifted_snapshot = replace(
        original.tool_snapshot,
        mcp_lifecycle=drifted_mcp,
        skill_lifecycle=drifted_skill,
        snapshot_hash="",
    )

    comparison = compare_turn_context_plan_resume(
        captured.plan,
        replace(_resume_request(original), tool_snapshot=drifted_snapshot),
    )
    serialized = json.dumps(comparison.to_receipt(), sort_keys=True)

    assert "tools.mcp_lifecycle" in comparison.mismatch_codes
    assert "tools.skill_lifecycle" in comparison.mismatch_codes
    assert "private-mcp-drift" not in serialized
    assert "private-skill-drift" not in serialized


def _resume_request(request: TurnContextAssemblyRequest) -> TurnContextResumeExecutionRequest:
    return TurnContextResumeExecutionRequest(
        prompt_components=request.prompt_components,
        rendered_system_prompt=request.rendered_system_prompt,
        retrieval_sources=frozenset(request.retrieval_gate.selected_sources),
        retrieval_tool_scope=request.retrieval_gate.tool_scope,
        tool_snapshot=request.tool_snapshot,
        sandbox_descriptor=request.sandbox_descriptor,
        harness_config=request.harness_config,
        runtime_mode=request.runtime_mode,
        permission_mode=request.permission_mode,
        model_spec=request.model_spec,
    )


def test_resume_comparator_matches_all_frozen_runtime_dependencies(tmp_path: Path) -> None:
    original = _request()
    captured = _capture(tmp_path, original)

    comparison = compare_turn_context_plan_resume(captured.plan, _resume_request(original))

    assert comparison.matched is True
    assert comparison.mismatch_codes == ()
    assert comparison.to_receipt()["checked_count"] >= 15


def test_resume_comparator_reports_prompt_sandbox_runtime_and_limit_drift_as_codes(
    tmp_path: Path,
) -> None:
    original = _request()
    captured = _capture(tmp_path, original)
    resume_request = _resume_request(original)
    drifted_sandbox = replace(
        original.sandbox_descriptor,
        capabilities=replace(original.sandbox_descriptor.capabilities, write_files=False),
    )
    drifted = replace(
        resume_request,
        rendered_system_prompt="changed private resume prompt",
        sandbox_descriptor=drifted_sandbox,
        harness_config=replace(original.harness_config, max_tool_calls=1),
        permission_mode="auto",
    )

    comparison = compare_turn_context_plan_resume(captured.plan, drifted)
    serialized = json.dumps(comparison.to_receipt(), sort_keys=True)

    assert comparison.matched is False
    assert comparison.mismatch_codes == (
        "prompt.rendered_hash",
        "execution.runtime",
        "execution.sandbox",
        "execution.sandbox_fingerprint",
        "execution.harness_limits",
    )
    assert "changed private resume prompt" not in serialized
