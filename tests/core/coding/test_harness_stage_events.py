from __future__ import annotations

import json

from core.coding.harness import CodingHarnessStageProjector


def _payloads(events):
    return [event.payload for event in events]


def test_projects_a_direct_reply_into_explicit_durable_stages() -> None:
    projector = CodingHarnessStageProjector("run-direct", clock=lambda: 10.0)

    events = [*projector.start()]
    events.extend(projector.before({"type": "model_requested"}))
    events.extend(projector.before({"type": "text_delta", "delta": "答"}))
    events.extend(projector.after({"type": "final", "content": "答案"}))
    events.extend(projector.finish("completed"))

    payloads = _payloads(events)
    assert [(item["type"], item.get("stage_id")) for item in payloads if item["type"].startswith("stage_")] == [
        ("stage_started", "receive"),
        ("stage_completed", "receive"),
        ("stage_started", "context"),
        ("stage_completed", "context"),
        ("stage_started", "plan"),
        ("stage_completed", "plan"),
        ("stage_started", "reply"),
        ("stage_completed", "reply"),
    ]
    decision = next(item for item in payloads if item["type"] == "retrieval_decision")
    assert decision == {
        "type": "retrieval_decision",
        "definition_id": "sage.coding.practice",
        "definition_version": 1,
        "decision": "skip",
        "gate": "model_tool_selection",
    }
    assert all(
        "operation_ref" not in item
        for item in payloads
        if item.get("stage_id") in {"receive", "context", "plan", "reply"}
    )


def test_records_revision_bound_retrieval_metrics_and_react_loop() -> None:
    times = iter([20.0, 20.125])
    projector = CodingHarnessStageProjector("run-retrieve", clock=lambda: next(times))
    result = json.dumps({
        "status": "evidence_found",
        "used_tokens": 420,
        "token_budget": 1200,
        "citations": [{"citation_id": "kcite_1"}, {"citation_id": "kcite_2"}],
    })

    events = [*projector.start()]
    events.extend(projector.before({"type": "model_requested"}))
    events.extend(projector.before({
        "type": "tool_call",
        "tool": "knowledge_search",
        "args": {"query": "Harness", "token_budget": 1200},
    }))
    events.extend(projector.after({
        "type": "tool_result",
        "tool": "knowledge_search",
        "content": result,
        "is_error": False,
    }))

    payloads = _payloads(events)
    decision = next(item for item in payloads if item["type"] == "retrieval_decision")
    assert decision["decision"] == "retrieve"
    completed = next(item for item in payloads if item["type"] == "retrieval_completed")
    assert completed == {
        "type": "retrieval_completed",
        "definition_id": "sage.coding.practice",
        "definition_version": 1,
        "status": "evidence_found",
        "citation_count": 2,
        "used_tokens": 420,
        "token_budget": 1200,
        "duration_ms": 125,
    }
    assert any(
        item["type"] == "transition_taken"
        and item["from_stage_id"] == "act"
        and item["to_stage_id"] == "plan"
        for item in payloads
    )


def test_approval_blocks_the_tool_stage_without_creating_a_second_visit() -> None:
    projector = CodingHarnessStageProjector("run-approval")

    events = [*projector.start()]
    events.extend(projector.before({"type": "model_requested"}))
    events.extend(projector.before({
        "type": "approval_required",
        "tool": "run_shell",
        "args": {"command": "pwd"},
    }))
    events.extend(projector.before({"type": "approval_granted", "tool": "run_shell"}))
    events.extend(projector.before({
        "type": "tool_call",
        "tool": "run_shell",
        "args": {"command": "pwd"},
    }))

    tool_starts = [
        event
        for event in events
        if event.payload.get("type") == "stage_started"
        and event.payload.get("stage_id") == "act"
    ]
    assert [event.status for event in tool_starts] == ["blocked", "running", "running"]
    assert all(event.payload["detail"] == "run_shell · pwd" for event in tool_starts)
    assert not any(
        event.payload.get("type") == "transition_taken"
        and event.payload.get("from_stage_id") == "act"
        and event.payload.get("to_stage_id") == "act"
        for event in events
    )


def test_redacts_secrets_from_tool_stage_details() -> None:
    projector = CodingHarnessStageProjector("run-secret")
    events = projector.before({
        "type": "tool_call",
        "tool": "run_shell",
        "args": {
            "command": (
                "OPENAI_API_KEY=plain-secret "
                "curl -H 'Authorization: Bearer bearer-secret' example.test"
            )
        },
    })

    detail = events[-1].payload["detail"]
    assert "plain-secret" not in detail
    assert "bearer-secret" not in detail
    assert detail.count("[REDACTED]") == 2
