"""Learning 运行态面向浏览器的安全公开投影合同。"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from sage_harness.runtime.events import HarnessStreamItem

from api.coding import (
    _learning_run_detail_payload,
    _learning_run_summary_payload,
    _learning_scope_failure_events,
    _learning_workspace_diff_payload,
)
from core.harness.event_adapter import HarnessEventAdapter
from core.harness.learning_public import LearningPublicProjector


def test_learning_scope_denial_timeline_discards_pending_tool_arguments() -> None:
    adapter = HarnessEventAdapter(session_id="session_scope", run_id="run_scope")
    call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "run_shell",
                "args": {
                    "query": "PRIVATE_QUERY_SENTINEL",
                    "path": "/private/source-secret",
                    "prompt": "SKILL_PROMPT_SENTINEL",
                    "token": "SECRET_TOKEN_SENTINEL",
                },
                "id": "call-scope",
                "type": "tool_call",
            }
        ],
    )
    assert (
        adapter.adapt(
            HarnessStreamItem(
                sequence=1,
                mode="messages",
                payload=(call, {}),
                source_event_id="graph:messages:1:call",
            )
        )
        == ()
    )
    denial = ToolMessage(
        content="learning_scope_tool_forbidden",
        tool_call_id="call-scope",
        name="run_shell",
        status="error",
        additional_kwargs={
            "sage_learning_scope": {
                "task_id": "ltask_scope",
                "status": "denied",
                "reason_code": "learning_scope_tool_forbidden",
            }
        },
    )

    events = adapter.adapt(
        HarnessStreamItem(
            sequence=2,
            mode="messages",
            payload=(denial, {}),
            source_event_id="graph:messages:2:denial",
        )
    )

    assert len(events) == 1
    assert events[0].payload == {
        "type": "learning_scope_denied",
        "task_id": "ltask_scope",
        "tool_call_id": "call-scope",
        "status": "denied",
        "reason_code": "learning_scope_tool_forbidden",
        "run_id": "run_scope",
        "session_id": "session_scope",
    }
    serialized = str(events)
    assert "PRIVATE_QUERY_SENTINEL" not in serialized
    assert "/private/source-secret" not in serialized
    assert "SKILL_PROMPT_SENTINEL" not in serialized
    assert "SECRET_TOKEN_SENTINEL" not in serialized


def test_learning_model_text_is_replaced_by_one_content_free_public_receipt() -> None:
    adapter = HarnessEventAdapter(
        session_id="session_scope",
        run_id="run_scope",
        learning_scope_task_id="ltask_scope",
    )

    first = adapter.adapt(
        HarnessStreamItem(
            sequence=1,
            mode="messages",
            payload=(
                AIMessage(
                    content=(
                        "PRIVATE_QUERY_SENTINEL /private/source-secret "
                        "WEB_BODY_SENTINEL SECRET_TOKEN_SENTINEL"
                    )
                ),
                {},
            ),
            source_event_id="graph:messages:1:text",
        )
    )
    second = adapter.adapt(
        HarnessStreamItem(
            sequence=2,
            mode="messages",
            payload=(AIMessage(content="another private chunk"), {}),
            source_event_id="graph:messages:2:text",
        )
    )

    expected = LearningPublicProjector.model_output_receipt(
        task_id="ltask_scope",
        run_id="run_scope",
    )
    expected["session_id"] = "session_scope"
    assert [event.payload for event in first] == [expected]
    assert second == ()
    assert adapter.finish() == ()
    serialized = str((first, second))
    assert "text_delta" not in serialized
    assert "PRIVATE_QUERY_SENTINEL" not in serialized
    assert "/private/source-secret" not in serialized
    assert "WEB_BODY_SENTINEL" not in serialized
    assert "SECRET_TOKEN_SENTINEL" not in serialized


def test_learning_scope_failure_receipt_keeps_specific_reason_in_terminal() -> None:
    events = _learning_scope_failure_events(
        "run_scope",
        "learning_scope_capability_revision_mismatch",
    )

    assert [event.payload["reason_code"] for event in events] == [
        "learning_scope_capability_revision_mismatch",
        "learning_scope_capability_revision_mismatch",
    ]
    assert events[-1].payload["error_type"] == "learning_scope_conflict"


def test_learning_scope_success_timeline_contains_only_safe_capability_receipts() -> None:
    adapter = HarnessEventAdapter(
        session_id="session_scope",
        run_id="run_scope",
        learning_scope_task_id="ltask_scope",
        learning_capability_ids_by_tool_name={"knowledge_search": "local:knowledge_search"},
    )
    call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "knowledge_search",
                "args": {"query": "PRIVATE_QUERY_SENTINEL"},
                "id": "call-scope",
                "type": "tool_call",
            }
        ],
    )
    assert (
        adapter.adapt(
            HarnessStreamItem(
                sequence=1,
                mode="messages",
                payload=(call, {}),
                source_event_id="graph:messages:1:call",
            )
        )
        == ()
    )
    result = ToolMessage(
        content=(
            '{"query":"PRIVATE_QUERY_SENTINEL",'
            '"source_relative_path":"/private/source-secret",'
            '"excerpt":"WEB_BODY_SENTINEL"}'
        ),
        tool_call_id="call-scope",
        name="knowledge_search",
        status="success",
    )

    events = adapter.adapt(
        HarnessStreamItem(
            sequence=2,
            mode="messages",
            payload=(result, {}),
            source_event_id="graph:messages:2:result",
        )
    )

    assert [event.payload["type"] for event in events] == [
        "learning_tool_call",
        "learning_tool_result",
    ]
    for event in events:
        assert event.payload["task_id"] == "ltask_scope"
        assert event.payload["capability_id"] == "local:knowledge_search"
        assert event.payload["tool_call_id"] == "call-scope"
    serialized = str(events)
    assert "PRIVATE_QUERY_SENTINEL" not in serialized
    assert "/private/source-secret" not in serialized
    assert "WEB_BODY_SENTINEL" not in serialized


@pytest.mark.parametrize(
    "payload",
    [
        {
            "type": "tool_call",
            "tool": "knowledge_search",
            "tool_call_id": "call-custom",
            "args": {"query": "PRIVATE_QUERY_SENTINEL"},
        },
        {
            "type": "tool_result",
            "tool": "knowledge_search",
            "tool_call_id": "call-custom",
            "content": "WEB_BODY_SENTINEL",
        },
        {
            "type": "memory_proposal_ready",
            "reflection_id": "reflection-custom",
            "candidate": "SECRET_TOKEN_SENTINEL",
        },
        {
            "type": "unknown_learning_event",
            "query": "PRIVATE_QUERY_SENTINEL",
            "source_path": "/private/source-secret",
        },
    ],
)
def test_learning_timeline_drops_custom_payloads_without_a_safe_projection(
    payload: dict[str, object],
) -> None:
    adapter = HarnessEventAdapter(
        session_id="session_scope",
        run_id="run_scope",
        learning_scope_task_id="ltask_scope",
    )

    events = adapter.adapt(
        HarnessStreamItem(
            sequence=1,
            mode="custom",
            payload=payload,
            source_event_id="graph:custom:unsafe",
        )
    )

    assert events == ()


def test_learning_timeline_projects_only_safe_research_child_receipt() -> None:
    adapter = HarnessEventAdapter(
        session_id="session_scope",
        run_id="run_scope",
        learning_scope_task_id="ltask_scope",
    )

    events = adapter.adapt(
        HarnessStreamItem(
            sequence=1,
            mode="custom",
            payload={
                "type": "subagent_progress",
                "task_id": "ltask_forged",
                "child_run_id": "child_scope",
                "parent_run_id": "run_scope",
                "status": "completed",
                "reason_code": "evidence_collected",
                "description": "SKILL_PROMPT_SENTINEL",
                "args": {"query": "PRIVATE_QUERY_SENTINEL"},
                "content": "WEB_BODY_SENTINEL",
                "source_path": "/private/source-secret",
                "secret": "SECRET_TOKEN_SENTINEL",
            },
            source_event_id="graph:custom:research",
        )
    )

    assert len(events) == 1
    assert events[0].payload == {
        "type": "subagent_progress",
        "task_id": "ltask_scope",
        "child_run_id": "child_scope",
        "parent_run_id": "run_scope",
        "agent_run_id": "child_scope",
        "status": "completed",
        "reason_code": "evidence_collected",
        "run_id": "run_scope",
        "session_id": "session_scope",
    }
    serialized = str(events)
    assert "ltask_forged" not in serialized
    assert "SKILL_PROMPT_SENTINEL" not in serialized
    assert "PRIVATE_QUERY_SENTINEL" not in serialized
    assert "WEB_BODY_SENTINEL" not in serialized
    assert "/private/source-secret" not in serialized
    assert "SECRET_TOKEN_SENTINEL" not in serialized


def test_learning_run_api_projections_hide_trace_and_workspace_paths() -> None:
    raw_detail = {
        "run_id": "child_scope",
        "events": [
            {
                "type": "subagent_started",
                "run_id": "child_scope",
                "parent_run_id": "run_scope",
                "description": "SKILL_PROMPT_SENTINEL",
                "status": "running",
            },
            {
                "type": "tool_call",
                "run_id": "child_scope",
                "tool_call_id": "call-scope",
                "args": {"query": "PRIVATE_QUERY_SENTINEL"},
            },
            {
                "type": "tool_result",
                "run_id": "child_scope",
                "tool_call_id": "call-scope",
                "content": "WEB_BODY_SENTINEL /private/source-secret",
                "is_error": True,
                "error_code": "learning_scope_plan_mismatch",
            },
        ],
        "timeline": [{"detail": "PRIVATE_QUERY_SENTINEL"}],
        "audit": {
            "status": "error",
            "duration_ms": 12,
            "steps": [{"result_preview": "WEB_BODY_SENTINEL"}],
        },
    }
    raw_summary = {
        "run_id": "child_scope",
        "status": "error",
        "event_count": 3,
        "tool_count": 1,
        "error_count": 1,
        "last_event_type": "tool_result",
        "started_at": "2026-08-24T00:00:00Z",
        "updated_at": "2026-08-24T00:00:01Z",
        "changed_files": ["/private/source-secret"],
        "audit": raw_detail["audit"],
    }

    detail = _learning_run_detail_payload(raw_detail, task_id="ltask_scope")
    summary = _learning_run_summary_payload(raw_summary)
    diff = _learning_workspace_diff_payload(
        "run_scope",
        {"changed_files": ["/private/source-secret"]},
    )

    assert detail == LearningPublicProjector.run_detail(raw_detail, task_id="ltask_scope")
    assert summary == LearningPublicProjector.run_summary(raw_summary)
    assert diff == LearningPublicProjector.workspace_diff(
        "run_scope",
        {"changed_files": ["/private/source-secret"]},
    )
    assert detail["events"][-1]["reason_code"] == "learning_scope_plan_mismatch"
    assert detail["timeline"] == []
    assert detail["audit"]["steps"] == []
    assert summary["changed_files"] == []
    assert summary["audit"]["changed_files"] == []
    assert diff == {
        "type": "workspace_diff_ready",
        "run_id": "run_scope",
        "status": "completed",
        "changed_file_count": 1,
    }
    serialized = str((detail, summary, diff))
    assert "PRIVATE_QUERY_SENTINEL" not in serialized
    assert "SKILL_PROMPT_SENTINEL" not in serialized
    assert "WEB_BODY_SENTINEL" not in serialized
    assert "/private/source-secret" not in serialized
