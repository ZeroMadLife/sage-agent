"""Loop-budget parity over the public legacy and DeerFlow V2 timelines."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from sage_harness import HarnessConfig

from api.main import create_app
from evals.coding.profile_parity import (
    ProfileParityReport,
    RuntimeProfile,
    project_profile_timeline,
)
from tests.core.coding.scripted_api_client import ScriptedApiClient


class LegacyLoopModel(ScriptedApiClient):
    """Repeat one harmless tool until the legacy loop detector stops the run."""

    def __init__(self) -> None:
        call = '<tool>{"name":"list_files","args":{"path":"."}}</tool>'
        super().__init__([call] * 8)


class DeerflowBudgetLoopModel(FakeMessagesListChatModel):
    """Propose more calls than the server-owned V2 tool budget permits."""

    def __init__(self) -> None:
        super().__init__(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "list_files",
                            "args": {"path": "."},
                            "id": f"call-list-{index}",
                            "type": "tool_call",
                        }
                    ],
                )
                for index in range(1, 6)
            ]
        )

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # type: ignore[no-untyped-def]
        _ = tools, tool_choice, kwargs
        return self


class DeerflowTokenCapModel(FakeMessagesListChatModel):
    """Spend the full token budget in the same response that proposes a tool."""

    def __init__(self) -> None:
        super().__init__(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "list_files",
                            "args": {"path": "."},
                            "id": "call-after-token-cap",
                            "type": "tool_call",
                        }
                    ],
                    usage_metadata={
                        "input_tokens": 8,
                        "output_tokens": 2,
                        "total_tokens": 10,
                    },
                )
            ]
        )

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # type: ignore[no-untyped-def]
        _ = tools, tool_choice, kwargs
        return self


def _payloads(events: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        payload
        for event in events
        if isinstance((payload := event.get("payload")), dict)
    ]


def _run_loop_scenario(
    tmp_path: Path,
    profile: RuntimeProfile,
) -> list[dict[str, object]]:
    workspace = tmp_path / profile
    workspace.mkdir()
    (workspace / "README.md").write_text("# Sage\n", encoding="utf-8")
    app = create_app(
        coding_model_factory=(
            LegacyLoopModel if profile == "legacy" else DeerflowBudgetLoopModel
        ),
        coding_workspace_root=workspace,
        coding_storage_root=workspace / ".coding",
        coding_deerflow_v2_enabled=True,
        coding_harness_config=HarnessConfig(
            max_model_calls=8,
            max_tool_calls=2,
            max_run_tokens=10_000,
        ),
    )
    with TestClient(app) as client:
        session_id = client.post(
            "/api/v1/coding/session",
            json={"runtime_profile": profile},
        ).json()["session_id"]
        with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
            websocket.send_json({"content": "持续列目录，直到安全预算停止"})
            events: list[dict[str, object]] = []
            while True:
                event = websocket.receive_json()
                events.append(event)
                if event["kind"] == "terminal":
                    return events


def test_legacy_and_v2_stop_tool_loops_with_a_visible_bounded_result(
    tmp_path: Path,
) -> None:
    results = []
    for profile in ("legacy", "deerflow_v2"):
        events = _run_loop_scenario(tmp_path, profile)
        payloads = _payloads(events)
        tool_calls = [
            payload for payload in payloads if payload.get("type") == "tool_call"
        ]
        tool_results = [
            payload for payload in payloads if payload.get("type") == "tool_result"
        ]
        assert len(tool_calls) == len(tool_results)
        assert len(tool_calls) <= 3
        assert events[-1]["kind"] == "terminal"
        assert events[-1]["status"] == "completed"

        if profile == "deerflow_v2":
            budget_events = [
                payload
                for payload in payloads
                if payload.get("type") == "run_budget_exhausted"
            ]
            assert len(budget_events) == 1
            assert budget_events[0]["stop_reason"] == "tool_call_capped"
            assert budget_events[0]["used"] == 3
            assert budget_events[0]["limit"] == 2
            assert len(tool_calls) == 2

        results.append(
            project_profile_timeline(
                "bounded-tool-loop",
                profile,
                events,
                assertions_passed=True,
            )
        )

    report = ProfileParityReport(results=results)
    assert report.paired_scenarios() == ("bounded-tool-loop",)
    assert report.regressions() == ()
    assert all(result.passed for result in report.results), report.to_dict()
    assert report.metrics("legacy").tool_call_success_rate == 1.0
    assert report.metrics("deerflow_v2").tool_call_success_rate == 1.0


def test_v2_does_not_execute_a_tool_from_the_token_exhausting_response(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "token-cap"
    workspace.mkdir()
    (workspace / "README.md").write_text("# Sage\n", encoding="utf-8")
    app = create_app(
        coding_model_factory=DeerflowTokenCapModel,
        coding_workspace_root=workspace,
        coding_storage_root=workspace / ".coding",
        coding_deerflow_v2_enabled=True,
        coding_harness_config=HarnessConfig(
            max_model_calls=8,
            max_tool_calls=8,
            max_run_tokens=10,
        ),
    )

    with TestClient(app) as client:
        session_id = client.post(
            "/api/v1/coding/session",
            json={"runtime_profile": "deerflow_v2"},
        ).json()["session_id"]
        with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
            websocket.send_json({"content": "列出目录"})
            events: list[dict[str, object]] = []
            while True:
                event = websocket.receive_json()
                events.append(event)
                if event["kind"] == "terminal":
                    break

    payloads = _payloads(events)
    assert not any(
        payload.get("type") in {"tool_call", "tool_result"}
        for payload in payloads
    )
    budget = next(
        payload
        for payload in payloads
        if payload.get("type") == "run_budget_exhausted"
    )
    assert budget["stop_reason"] == "token_capped"
    assert budget["used"] == 10
    assert budget["limit"] == 10
    final = next(payload for payload in payloads if payload.get("type") == "final")
    assert "token 安全上限" in str(final["content"])
    assert events[-1]["kind"] == "terminal"
    assert events[-1]["status"] == "completed"
