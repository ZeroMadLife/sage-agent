"""FastAPI contract schema tests."""

import pytest
from pydantic import ValidationError

from api.schemas import (
    UserMessage,
)


def test_user_message_accepts_bounded_surface_context() -> None:
    message = UserMessage.model_validate(
        {
            "content": "解释当前页面",
            "surface_context": {
                "surface": "knowledge",
                "workspace_id": "knowledge-local",
                "resource": {
                    "type": "knowledge_page",
                    "id": "page-1",
                    "revision": "rev-1",
                    "label": "caller label",
                },
                "selection": {
                    "type": "graph_node",
                    "id": "node-1",
                    "revision": "rev-1",
                },
                "graph_revision": "graph-1",
                "operation_refs": [{"kind": "knowledge_job", "id": "job-1"}],
            },
            "thread_goal_revision": 3,
        }
    )

    assert message.surface_context is not None
    assert message.surface_context.workspace_id == "knowledge-local"
    assert message.surface_context.operation_refs[0].id == "job-1"
    assert message.thread_goal_revision == 3


def test_user_message_rejects_invalid_thread_goal_revision() -> None:
    with pytest.raises(ValidationError):
        UserMessage.model_validate({"content": "continue", "thread_goal_revision": 0})


def test_surface_context_rejects_unknown_fields_and_unbounded_refs() -> None:
    base = {
        "surface": "coding",
        "workspace_id": "workspace-1",
        "resource": {"type": "coding_workspace", "id": "workspace-1"},
        "selection": None,
        "operation_refs": [],
    }
    with pytest.raises(ValidationError):
        UserMessage.model_validate(
            {"content": "hello", "surface_context": {**base, "owner_id": "forged"}}
        )
    with pytest.raises(ValidationError):
        UserMessage.model_validate(
            {
                "content": "hello",
                "surface_context": {
                    **base,
                    "operation_refs": [
                        {"kind": "coding_run", "id": f"run-{index}"} for index in range(21)
                    ],
                },
            }
        )
