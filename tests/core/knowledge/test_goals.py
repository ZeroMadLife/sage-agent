from __future__ import annotations

from core.knowledge.goals import (
    LearningCapability,
    LearningGoalDefinition,
    parse_learning_goal,
    render_learning_goal,
)


def test_legacy_purpose_remains_readable_without_inventing_capabilities() -> None:
    goal = parse_learning_goal(
        "# Purpose\n\n构建可审核、可追溯的个人知识库。\n", git_commit="abc123"
    )

    assert goal.goal_id == "personal-learning"
    assert goal.structured is False
    assert goal.capabilities == ()
    assert goal.goal_revision.startswith("kgoal_")
    assert goal.git_commit == "abc123"


def test_structured_learning_goal_round_trips_deterministically() -> None:
    definition = LearningGoalDefinition(
        goal_id="full-stack-ai-engineer",
        title="成为全栈 AI 应用工程师",
        description="能够设计、开发、评测并部署可用的 AI 产品。",
        capabilities=(
            LearningCapability(
                capability_id="agent-harness",
                label="Agent Harness",
                description="构建有状态、可恢复的运行时。",
                keywords=("Agent Harness", "recovery", "tool runtime"),
                weight=1.5,
                required=True,
            ),
        ),
    )

    first = render_learning_goal(definition)
    second = render_learning_goal(definition)
    parsed = parse_learning_goal(first, git_commit="commit")

    assert first == second
    assert parsed.structured is True
    assert parsed.definition() == definition
    assert parsed.goal_revision.startswith("kgoal_")
