"""Turn Context Assembler shadow-capture contract tests."""

from __future__ import annotations

import json
from pathlib import Path

from sage_harness import DeferredToolSetup, HarnessConfig, SandboxCapabilities, SandboxDescriptor

from core.coding.context import ContextUsage, PreparedContext
from core.coding.persistence import TurnPlanStore
from core.harness.context_adapter import DeerFlowPromptComponents
from core.harness.retrieval_gate import RetrievalGateReceipt
from core.harness.tools_adapter import CodingToolBundle
from core.harness.turn_context_assembler import (
    TurnContextAssembler,
    TurnContextAssemblyRequest,
    normalize_context_assembly_mode,
)


def _request() -> TurnContextAssemblyRequest:
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
            projected_history=[],
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
        prompt_components=DeerFlowPromptComponents(
            static_policy="server-owned static policy",
            dynamic_authority="source-locked to knowledge",
            untrusted_context="private working memory",
        ),
        rendered_system_prompt=(
            "server-owned static policy\n\nsource-locked to knowledge\n\nprivate working memory"
        ),
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
        tool_bundle=CodingToolBundle(
            tools=(),
            deferred_setup=DeferredToolSetup(catalog_hash="catalog-sha"),
            capability_revision="cap-r1",
            capability_ids_by_tool_name={"read_file": "local:read_file"},
            capability_count=1,
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
        active_skill_allowed_tools=frozenset({"read_file"}),
    )


def _assembler(tmp_path: Path) -> TurnContextAssembler:
    ids = iter(("tcp-first", "tcp-retry"))
    return TurnContextAssembler(
        TurnPlanStore(tmp_path, "session-1"),
        plan_id_factory=lambda: next(ids),
        clock=lambda: "2026-08-07T00:00:00+00:00",
    )


def test_shadow_capture_persists_only_references_digests_and_static_policy(tmp_path: Path) -> None:
    captured = _assembler(tmp_path).prepare_new_turn(_request())
    payload = captured.plan.to_payload()
    serialized_plan = captured.plan.payload_json
    serialized_receipt = json.dumps(captured.receipt, sort_keys=True)

    assert payload["prompt"]["static_policy"]["rendered_content"] == ("server-owned static policy")
    assert payload["budget"]["estimated_input_tokens"] == 1_024
    assert payload["budget"]["token_budget_by_source"] == {"knowledge": 3_000}
    assert payload["context_refs"]["memory_refs"][0]["memory_id"] == "memory-1"
    assert payload["execution"]["sandbox"]["provider"] == "container"
    assert captured.plan.owner_fingerprint != "owner@example.test"

    for private_content in (
        "private user request",
        "private source text",
        "private goal",
        "private compaction summary",
        "private approved memory",
        "private working memory",
        "container:internal-id",
    ):
        assert private_content not in serialized_plan
        assert private_content not in serialized_receipt
    assert "server-owned static policy" not in serialized_receipt


def test_shadow_capture_is_idempotent_for_the_same_run_selection(tmp_path: Path) -> None:
    assembler = _assembler(tmp_path)

    first = assembler.prepare_new_turn(_request())
    retried = assembler.prepare_new_turn(_request())

    assert retried.plan == first.plan
    assert retried.receipt == first.receipt


def test_context_assembly_accepts_enforce_after_b_stage_delivery() -> None:
    assert normalize_context_assembly_mode("enforce") == "enforce"
