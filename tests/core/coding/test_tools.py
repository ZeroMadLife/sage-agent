"""Coding core tool tests."""

import json
import os
import sys
import time
from pathlib import Path

from core.coding.context import WorkspaceContext
from core.coding.tools.base import RegisteredTool, ToolResult
from core.coding.tools.registry import (
    ToolDefinition,
    build_tool_registry,
    get_active_tools,
    registered_tool_definitions,
)
from core.config.settings import Settings
from models.itinerary import BudgetBreakdown, Itinerary, ItineraryDay


def _workspace(tmp_path: Path) -> WorkspaceContext:
    return WorkspaceContext(root=tmp_path)


def test_list_files_marks_files_and_directories_and_ignores_noise(tmp_path: Path) -> None:
    """list_files returns stable file/directory markers and skips ignored names."""
    (tmp_path / "src").mkdir()
    (tmp_path / "README.md").write_text("TourSwarm", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    tools = build_tool_registry(_workspace(tmp_path))

    result = tools["list_files"].execute({"path": "."})

    assert result.is_error is False
    assert "[D] src" in result.content
    assert "[F] README.md" in result.content
    assert ".git" not in result.content


def test_read_file_returns_numbered_line_range(tmp_path: Path) -> None:
    """read_file reads a selected range with line numbers."""
    (tmp_path / "README.md").write_text("one\ntwo\nthree\n", encoding="utf-8")
    tools = build_tool_registry(_workspace(tmp_path))

    result = tools["read_file"].execute({"path": "README.md", "start": 2, "end": 3})

    assert result.is_error is False
    assert "# README.md" in result.content
    assert "   2: two" in result.content
    assert "   3: three" in result.content


def test_search_finds_matches_in_workspace(tmp_path: Path) -> None:
    """search finds text matches under the workspace."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    tools = build_tool_registry(_workspace(tmp_path))

    result = tools["search"].execute({"pattern": "alpha", "path": "."})

    assert result.is_error is False
    assert "src/app.py:1" in result.content


def test_patch_file_requires_unique_old_text(tmp_path: Path) -> None:
    """patch_file rejects ambiguous replacements and applies unique replacements."""
    target = tmp_path / "app.py"
    target.write_text("value = 1\nvalue = 1\n", encoding="utf-8")
    tools = build_tool_registry(_workspace(tmp_path))

    duplicate = tools["patch_file"].execute(
        {"path": "app.py", "old_text": "value = 1", "new_text": "value = 2"}
    )
    assert duplicate.is_error is True
    assert "exactly once" in duplicate.content

    target.write_text("value = 1\n", encoding="utf-8")
    unique = tools["patch_file"].execute(
        {"path": "app.py", "old_text": "value = 1", "new_text": "value = 2"}
    )
    assert unique.is_error is False
    assert target.read_text(encoding="utf-8") == "value = 2\n"


def test_write_file_rejects_workspace_escape(tmp_path: Path) -> None:
    """write_file cannot write outside the workspace root."""
    tools = build_tool_registry(_workspace(tmp_path))

    result = tools["write_file"].execute({"path": "../outside.txt", "content": "x"})

    assert result.is_error is True
    assert "escapes workspace root" in result.content
    assert not (tmp_path.parent / "outside.txt").exists()


def test_run_shell_uses_filtered_environment_and_reports_timeout(tmp_path: Path) -> None:
    """run_shell filters sensitive env vars and reports command timeout as an error."""
    workspace = _workspace(tmp_path)
    tools = build_tool_registry(workspace)
    os.environ["DEEPSEEK_API_KEY"] = "secret"

    env_result = tools["run_shell"].execute(
        {
            "command": (
                f"{sys.executable} -c "
                '\'import os; print(os.environ.get("DEEPSEEK_API_KEY", "missing"))\''
            )
        }
    )
    timeout_result = tools["run_shell"].execute(
        {
            "command": f"{sys.executable} -c 'import time; time.sleep(2)'",
            "timeout": 1,
        }
    )

    assert env_result.is_error is False
    assert "missing" in env_result.content
    assert "secret" not in env_result.content
    assert timeout_result.is_error is True
    assert "timed out" in timeout_result.content


def test_tool_registry_discovers_decorated_tools_with_stable_metadata(tmp_path: Path) -> None:
    """Tool discovery exposes the same public toolset plus Sage v3 metadata."""
    tools = build_tool_registry(_workspace(tmp_path))

    assert set(tools) == {
        "list_files",
        "read_file",
        "search",
        "run_shell",
        "write_file",
        "patch_file",
        "tool_search",
        "todo_add",
        "todo_update",
        "todo_list",
        "enter_plan_mode",
        "exit_plan_mode",
        "agent",
        "send_message",
        "task_stop",
        "generate_itinerary",
        "search_attractions",
        "get_weather",
        "get_forecast",
        "geocode",
        "search_nearby",
        "get_route",
    }
    assert tools["read_file"].category == "file"
    assert tools["run_shell"].category == "shell"
    assert tools["run_shell"].requires_approval is True
    assert tools["read_file"].requires_approval is False
    assert tools["tool_search"].deferred is False
    assert tools["todo_add"].deferred is True
    assert tools["generate_itinerary"].deferred is True
    assert tools["get_weather"].category == "travel"


def test_registered_tool_definitions_are_decorator_backed() -> None:
    """The registry is populated by per-module decorators instead of a central spec dict."""
    definitions = registered_tool_definitions()

    assert isinstance(definitions["read_file"], ToolDefinition)
    assert definitions["read_file"].schema_model.__name__ == "ReadFileArgs"
    assert definitions["todo_add"].category == "todo"


def test_get_active_tools_filters_deferred_tools(tmp_path: Path) -> None:
    """Only resident tools and activated deferred tools are model-visible."""
    tools = build_tool_registry(_workspace(tmp_path))

    active = get_active_tools(tools, activated={"todo_add"})

    assert "read_file" in active
    assert "tool_search" in active
    assert "todo_add" in active
    assert "todo_update" not in active
    assert "agent" not in active


def test_tool_search_activates_matching_deferred_tools(tmp_path: Path) -> None:
    """tool_search returns schemas and activates matching deferred tools for the session."""
    activated: set[str] = set()
    tools = build_tool_registry(_workspace(tmp_path), activated_tools=activated)

    result = tools["tool_search"].execute({"query": "todo"})

    assert result.is_error is False
    assert "todo_add" in result.content
    assert "todo_update" in result.content
    assert "todo_add" in activated
    assert "todo_update" in activated
    assert "read_file" not in activated


def test_tool_search_activates_travel_tools(tmp_path: Path) -> None:
    """tool_search can activate v5 travel domain tools."""
    activated: set[str] = set()
    tools = build_tool_registry(_workspace(tmp_path), activated_tools=activated)

    result = tools["tool_search"].execute({"query": "travel"})

    assert result.is_error is False
    assert "generate_itinerary" in result.content
    assert "get_weather" in result.content
    assert "generate_itinerary" in activated
    assert "get_weather" in activated


def test_travel_tool_missing_key_returns_error(tmp_path: Path, monkeypatch) -> None:
    """Travel tools degrade clearly when API keys are not configured."""
    from core.coding.tools import travel_tools

    monkeypatch.setattr(
        travel_tools,
        "get_settings",
        lambda: Settings(amap_api_key="", qweather_api_key=""),
    )
    tools = build_tool_registry(_workspace(tmp_path))

    result = tools["search_attractions"].execute({"city": "杭州"})

    assert result.is_error is True
    assert "AMAP_API_KEY" in result.content


def test_generate_itinerary_uses_existing_graph_wrapper(tmp_path: Path, monkeypatch) -> None:
    """generate_itinerary delegates to the existing itinerary tool graph wrapper."""
    from core.coding.tools import travel_tools

    class FakeGraph:
        async def ainvoke(self, state: dict[str, object]) -> dict[str, object]:
            return {
                "itinerary": Itinerary(
                    destination=str(state["destination"]),
                    days=[ItineraryDay(date="2026-07-10", total_cost=80)],
                    total_cost=80,
                    weather_summary="晴",
                    budget=BudgetBreakdown(total=500, spent=80, over_budget=False),
                ),
                "weather_info": {"current": {"text": "晴"}, "error": False},
            }

    monkeypatch.setattr(
        travel_tools,
        "get_settings",
        lambda: Settings(qweather_api_key="fake", doubao_api_key="fake"),
    )
    monkeypatch.setattr(
        travel_tools,
        "_build_itinerary_graph",
        lambda _settings, _repo_root: FakeGraph(),
    )
    tools = build_tool_registry(_workspace(tmp_path))

    result = tools["generate_itinerary"].execute(
        {"destination": "杭州", "budget_total": 500, "preferences": "美食"}
    )
    payload = json.loads(result.content)

    assert result.is_error is False
    assert payload["destination"] == "杭州"
    assert payload["total_cost"] == 80


def test_registered_tool_execute_times_out_sync_runner() -> None:
    """Synchronous tools get a generic timeout guard outside shell-specific timeout."""

    def slow_runner(_args: dict[str, object]) -> ToolResult:
        time.sleep(0.2)
        return ToolResult(content="late")

    tool = RegisteredTool(
        name="slow",
        schema={},
        description="Slow tool.",
        risky=False,
        runner=slow_runner,
        timeout=0.01,
    )

    result = tool.execute({})

    assert result.is_error is True
    assert "timed out" in result.content
