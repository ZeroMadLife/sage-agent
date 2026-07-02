"""Supervisor graph orchestration tests."""

from unittest.mock import MagicMock

from langgraph.graph import END

from agents.graph import build_graph, should_replan


def test_should_replan_returns_planning_when_over_budget() -> None:
    """Over-budget plans re-enter planning before the iteration limit."""
    state = {"over_budget": True, "iteration_count": 1}

    assert should_replan(state) == "planning"


def test_should_replan_returns_end_when_iteration_exceeded() -> None:
    """The replan loop stops at the max iteration count."""
    state = {"over_budget": True, "iteration_count": 3}

    assert should_replan(state) == END


def test_should_replan_returns_end_when_within_budget() -> None:
    """Within-budget plans end the graph."""
    state = {"over_budget": False, "iteration_count": 1}

    assert should_replan(state) == END


def test_build_graph_returns_compiled_graph() -> None:
    """build_graph returns a compiled async graph."""
    graph = build_graph(
        weather_client=MagicMock(),
        scenic_client=MagicMock(),
        planning_llm=MagicMock(),
        budget_llm=MagicMock(),
    )

    assert graph is not None
    assert hasattr(graph, "ainvoke")
