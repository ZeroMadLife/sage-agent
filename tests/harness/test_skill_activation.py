"""Skill activation and allowlist middleware contract tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import MagicMock

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.prebuilt.tool_node import ToolCallRequest
from sage_harness import (
    HarnessRunContext,
    SkillActivationMiddleware,
    parse_skill_activation,
)
from sage_harness.agents import create_sage_agent
from sage_harness.middleware import MiddlewareSpec, build_default_registry


class Catalog:
    def __init__(self) -> None:
        self.review = SimpleNamespace(
            name="review",
            source="builtin",
            description="Review the current changes",
            prompt="Inspect the changed files and report findings.",
            allowed_tools=("read_file", "search"),
            user_invocable=True,
        )

    def get(self, name: str) -> object | None:
        return self.review if name == "review" else None

    def list(self) -> list[object]:
        return [self.review]


class CapturingModel(FakeMessagesListChatModel):
    seen_messages: ClassVar[list[list[BaseMessage]]] = []

    def _generate(self, messages, *args, **kwargs):  # type: ignore[no-untyped-def]
        type(self).seen_messages.append(list(messages))
        return super()._generate(messages, *args, **kwargs)


def _request(content: str) -> ModelRequest:
    return ModelRequest(
        model=FakeMessagesListChatModel(responses=[AIMessage(content="ok")]),
        messages=[HumanMessage(content=content)],
        tools=[
            StructuredTool.from_function(
                func=lambda path: path,
                name="read_file",
                description="Read a file",
            ),
            StructuredTool.from_function(
                func=lambda command: command,
                name="run_shell",
                description="Run a shell command",
            ),
            StructuredTool.from_function(
                func=lambda query: query,
                name="tool_search",
                description="Find deferred tools",
            ),
        ],
    )


def test_parse_skill_activation_requires_a_slash_command() -> None:
    assert parse_skill_activation("/review inspect staged files") == (
        "review",
        "inspect staged files",
    )
    assert parse_skill_activation("please /review") is None


def test_before_agent_persists_only_a_non_host_skill_reference() -> None:
    middleware = SkillActivationMiddleware(Catalog())
    update = middleware.before_agent(
        {"messages": [HumanMessage(content="/review inspect")]},
        MagicMock(),
    )

    assert update == {
        "skill_context": [
            {
                "name": "review",
                "path": "skill://builtin/review",
                "description": "Review the current changes",
                "loaded_at": 0,
                "revision": "8df49d9f98975566",
            }
        ]
    }


def test_skill_revision_is_stable_across_invocation_arguments() -> None:
    catalog = Catalog()
    catalog.review.render = lambda arguments: f"{catalog.review.prompt}\nArguments: {arguments}"
    middleware = SkillActivationMiddleware(catalog)

    first = middleware.before_agent(
        {"messages": [HumanMessage(content="/review first")]},
        MagicMock(),
    )
    second = middleware.before_agent(
        {"messages": [HumanMessage(content="/review second")]},
        MagicMock(),
    )

    assert first is not None
    assert second is not None
    assert first["skill_context"][0]["revision"] == second["skill_context"][0]["revision"]


def test_skill_reference_rejects_catalog_name_mismatch_and_host_source() -> None:
    catalog = Catalog()
    catalog.review.source = "/Users/private/.sage/skills"
    middleware = SkillActivationMiddleware(catalog)

    update = middleware.before_agent(
        {"messages": [HumanMessage(content="/review inspect")]},
        MagicMock(),
    )

    assert update is not None
    assert update["skill_context"][0]["path"] == "skill://application/review"

    catalog.review.name = "different"
    assert (
        middleware.before_agent(
            {"messages": [HumanMessage(content="/review inspect")]},
            MagicMock(),
        )
        is None
    )


def test_model_injection_is_hidden_and_allowlist_filters_visible_tools() -> None:
    middleware = SkillActivationMiddleware(Catalog())
    captured: list[ModelRequest] = []

    def capture(request: ModelRequest) -> ModelResponse:
        captured.append(request)
        return ModelResponse(result=[AIMessage(content="ok")])

    middleware.wrap_model_call(_request("/review inspect"), capture)

    assert [tool.name for tool in captured[0].tools] == ["read_file", "tool_search"]
    hidden = [
        message
        for message in captured[0].messages
        if isinstance(message, HumanMessage)
        and message.additional_kwargs.get("sage_skill_activation")
    ]
    assert len(hidden) == 1
    assert hidden[0].additional_kwargs["hide_from_ui"] is True
    assert "Inspect the changed files" in str(hidden[0].content)


def test_tool_policy_blocks_forged_disallowed_calls() -> None:
    middleware = SkillActivationMiddleware(Catalog())
    request = ToolCallRequest(
        tool_call={"name": "run_shell", "args": {"command": "pwd"}, "id": "call-1"},
        tool=None,
        state={"messages": [HumanMessage(content="/review inspect")]},
        runtime=MagicMock(),
    )

    result = middleware.wrap_tool_call(request, lambda _: ToolMessage(content="executed"))

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "not allowed" in str(result.content)


def test_plain_user_message_does_not_activate_or_filter_tools() -> None:
    middleware = SkillActivationMiddleware(Catalog())
    captured: list[ModelRequest] = []

    middleware.wrap_model_call(
        _request("Review the current changes"),
        lambda request: captured.append(request) or ModelResponse(result=[AIMessage(content="ok")]),
    )

    assert [tool.name for tool in captured[0].tools] == ["read_file", "run_shell", "tool_search"]
    assert not any(
        isinstance(message, HumanMessage) and message.additional_kwargs.get("sage_skill_activation")
        for message in captured[0].messages
    )


def test_real_graph_persists_skill_ref_without_persisting_skill_body() -> None:
    registry = build_default_registry().with_spec(
        MiddlewareSpec(
            "skill_activation",
            lambda config: SkillActivationMiddleware(Catalog()),
        ),
        before="input_sanitization",
    )
    CapturingModel.seen_messages = []
    graph = create_sage_agent(
        CapturingModel(responses=[AIMessage(content="ready")]),
        registry=registry,
    )

    result = graph.invoke(
        {"messages": [HumanMessage(content="/review inspect")]},
        context=HarnessRunContext(
            thread_id="thread-skill",
            run_id="run-skill",
            owner_id="owner-skill",
            workspace_id="workspace-skill",
            workspace_path="/workspace",
        ),
    )

    assert result["skill_context"][0]["path"] == "skill://builtin/review"
    assert all(
        "Inspect the changed files" not in str(message.content) for message in result["messages"]
    )
    assert any(
        "Inspect the changed files" in str(message.content)
        for message in CapturingModel.seen_messages[0]
    )
