"""Skill abstraction tests."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.react_agent import AgentRuntime
from core.skill import (
    KnowledgeSource,
    MemoryConfig,
    Skill,
    SkillRegistry,
    ToolSpec,
    build_travel_planning_skill,
)
from core.verifier import ItineraryVerifier


def _skill(
    name: str = "custom-skill",
    system_prompt: str = "你是一个测试 Skill Runtime。",
    tools: list[ToolSpec] | None = None,
) -> Skill:
    return Skill(
        name=name,
        system_prompt=system_prompt,
        tools=tools or [],
        sub_agent_graph=None,
        verifier=None,
        memory_config=MemoryConfig(),
        knowledge_sources=[],
    )


def test_skill_registry_registers_and_finds_by_name() -> None:
    """SkillRegistry stores complete Skill definitions by name."""
    registry = SkillRegistry()
    skill = _skill(name="travel-planning")

    registry.register(skill)

    assert registry.get("travel-planning") is skill
    assert registry.names() == ["travel-planning"]
    with pytest.raises(KeyError):
        registry.get("missing")


def test_travel_planning_skill_packages_prompt_tools_graph_and_verifier() -> None:
    """The travel Skill should package the existing tourism capabilities."""
    generate_itinerary = MagicMock()
    sub_agent_graph = object()

    skill = build_travel_planning_skill(
        tools={"generate_itinerary": generate_itinerary},
        sub_agent_graph=sub_agent_graph,
    )

    assert skill.name == "travel-planning"
    assert "学生穷游助手" in skill.system_prompt
    assert skill.tool_map["generate_itinerary"] is generate_itinerary
    assert skill.sub_agent_graph is sub_agent_graph
    assert isinstance(skill.verifier, ItineraryVerifier)
    assert skill.knowledge_sources == []


async def test_agent_runtime_reads_prompt_and_tools_from_skill() -> None:
    """AgentRuntime should use the injected Skill prompt and tool registry."""

    async def echo(value: str) -> dict[str, Any]:
        return {"echo": value}

    llm = MagicMock()
    llm.ainvoke = AsyncMock(
        side_effect=[
            MagicMock(content='{"action": "echo", "input": {"value": "hi"}}'),
            MagicMock(content="工具已经执行。"),
        ]
    )
    skill = _skill(
        system_prompt="CUSTOM_SKILL_PROMPT",
        tools=[
            ToolSpec(
                name="echo",
                description="Echo input.",
                handler=echo,
                parameters={"value": "str"},
            )
        ],
    )

    agent = AgentRuntime(llm=llm, skill=skill, max_iterations=2)
    response = await agent.chat("hello", user_id="u1", session_id="s1")

    first_call_messages = llm.ainvoke.await_args_list[0].args[0]
    system_prompts = [
        message["content"] for message in first_call_messages if message.get("role") == "system"
    ]
    assert system_prompts[0] == "CUSTOM_SKILL_PROMPT"
    assert response.tool_calls[0].tool == "echo"
    assert response.tool_calls[0].output == {"echo": "hi"}


def test_skill_keeps_memory_and_knowledge_configuration() -> None:
    """A Skill carries framework-level memory and knowledge configuration."""
    memory_config = MemoryConfig(default_scope="skill:custom-skill", enable_long_term=True)
    knowledge_source = KnowledgeSource(
        name="docs",
        description="Project documents",
        metadata={"collection": "docs"},
    )

    skill = Skill(
        name="custom-skill",
        system_prompt="Prompt",
        tools=[],
        sub_agent_graph=None,
        verifier=None,
        memory_config=memory_config,
        knowledge_sources=[knowledge_source],
    )

    assert skill.memory_config.default_scope == "skill:custom-skill"
    assert skill.knowledge_sources[0].metadata["collection"] == "docs"
