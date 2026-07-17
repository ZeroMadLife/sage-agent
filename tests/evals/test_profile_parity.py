"""Legacy and DeerFlow V2 parity metrics over the public timeline."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from api.main import create_app
from evals.coding.profile_parity import ProfileParityReport, project_profile_timeline
from tests.core.coding.scripted_api_client import ScriptedApiClient


class LegacyReadModel(ScriptedApiClient):
    def __init__(self) -> None:
        super().__init__([
            '<tool>{"name":"read_file","args":{"path":"README.md"}}</tool>',
            "<final>项目名是 Sage。</final>",
        ])


class DeerflowReadModel(FakeMessagesListChatModel):
    def __init__(self) -> None:
        super().__init__(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "read_file",
                            "args": {"path": "README.md"},
                            "id": "call-readme",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="项目名是 Sage。"),
            ]
        )

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # type: ignore[no-untyped-def]
        _ = tools, tool_choice, kwargs
        return self


class LegacyMissingPathRecoveryModel(ScriptedApiClient):
    def __init__(self) -> None:
        super().__init__([
            '<tool>{"name":"read_file","args":{"path":"missing.md"}}</tool>',
            '<tool>{"name":"read_file","args":{"path":"README.md"}}</tool>',
            "<final>修正路径后确认项目名是 Sage。</final>",
        ])


class DeerflowMissingPathRecoveryModel(FakeMessagesListChatModel):
    def __init__(self) -> None:
        super().__init__(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "read_file",
                            "args": {"path": "missing.md"},
                            "id": "call-missing",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "read_file",
                            "args": {"path": "README.md"},
                            "id": "call-corrected",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="修正路径后确认项目名是 Sage。"),
            ]
        )

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # type: ignore[no-untyped-def]
        _ = tools, tool_choice, kwargs
        return self


class LegacyFreshReadEditModel(ScriptedApiClient):
    def __init__(self) -> None:
        super().__init__([
            (
                '<tool>{"name":"patch_file","args":{"path":"app.py",'
                '"old_text":"value = 1","new_text":"value = 2"}}</tool>'
            ),
            '<tool>{"name":"read_file","args":{"path":"app.py"}}</tool>',
            (
                '<tool>{"name":"patch_file","args":{"path":"app.py",'
                '"old_text":"value = 1","new_text":"value = 2"}}</tool>'
            ),
            "<final>读取确认并获批后，已将 value 更新为 2。</final>",
        ])


class DeerflowFreshReadEditModel(FakeMessagesListChatModel):
    def __init__(self) -> None:
        super().__init__(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "patch_file",
                            "args": {
                                "path": "app.py",
                                "old_text": "value = 1",
                                "new_text": "value = 2",
                            },
                            "id": "call-blind-patch",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "read_file",
                            "args": {"path": "app.py"},
                            "id": "call-read-app",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "patch_file",
                            "args": {
                                "path": "app.py",
                                "old_text": "value = 1",
                                "new_text": "value = 2",
                            },
                            "id": "call-approved-patch",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="读取确认并获批后，已将 value 更新为 2。"),
            ]
        )

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # type: ignore[no-untyped-def]
        _ = tools, tool_choice, kwargs
        return self


def _run_read_scenario(tmp_path: Path, profile: str) -> list[dict[str, object]]:
    workspace = tmp_path / profile
    workspace.mkdir()
    (workspace / "README.md").write_text("# Sage\n", encoding="utf-8")
    app = create_app(
        coding_model_factory=(LegacyReadModel if profile == "legacy" else DeerflowReadModel),
        coding_workspace_root=workspace,
        coding_storage_root=workspace / ".coding",
        coding_deerflow_v2_enabled=True,
    )
    with TestClient(app) as client:
        session_id = client.post(
            "/api/v1/coding/session",
            json={"runtime_profile": profile},
        ).json()["session_id"]
        with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
            websocket.send_json({"content": "读取 README 并告诉我项目名"})
            events: list[dict[str, object]] = []
            while True:
                event = websocket.receive_json()
                events.append(event)
                if event["kind"] == "terminal":
                    return events


def _run_missing_path_recovery_scenario(
    tmp_path: Path,
    profile: str,
) -> list[dict[str, object]]:
    workspace = tmp_path / f"missing-path-{profile}"
    workspace.mkdir()
    (workspace / "README.md").write_text("# Sage\n", encoding="utf-8")
    app = create_app(
        coding_model_factory=(
            LegacyMissingPathRecoveryModel
            if profile == "legacy"
            else DeerflowMissingPathRecoveryModel
        ),
        coding_workspace_root=workspace,
        coding_storage_root=workspace / ".coding",
        coding_deerflow_v2_enabled=True,
    )
    with TestClient(app) as client:
        session_id = client.post(
            "/api/v1/coding/session",
            json={"runtime_profile": profile},
        ).json()["session_id"]
        with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
            websocket.send_json({"content": "读取 missing.md；如果路径错误请修正"})
            events: list[dict[str, object]] = []
            while True:
                event = websocket.receive_json()
                events.append(event)
                if event["kind"] == "terminal":
                    return events


def _run_fresh_read_edit_scenario(
    tmp_path: Path,
    profile: str,
) -> tuple[list[dict[str, object]], Path]:
    workspace = tmp_path / f"fresh-read-edit-{profile}"
    workspace.mkdir()
    target = workspace / "app.py"
    target.write_text("value = 1\n", encoding="utf-8")
    app = create_app(
        coding_model_factory=(
            LegacyFreshReadEditModel
            if profile == "legacy"
            else DeerflowFreshReadEditModel
        ),
        coding_workspace_root=workspace,
        coding_storage_root=workspace / ".coding",
        coding_deerflow_v2_enabled=True,
    )
    with TestClient(app) as client:
        session_id = client.post(
            "/api/v1/coding/session",
            json={"runtime_profile": profile, "approval_policy": "ask"},
        ).json()["session_id"]
        with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
            websocket.send_json({"content": "把 app.py 的 value 从 1 改为 2"})
            events: list[dict[str, object]] = []
            while True:
                event = websocket.receive_json()
                events.append(event)
                payload = event.get("payload")
                if isinstance(payload, dict) and payload.get("type") == "approval_required":
                    approved = client.post(
                        f"/api/v1/coding/{session_id}/approval/respond",
                        json={
                            "approval_id": payload["approval_id"],
                            "choice": "once",
                        },
                    )
                    assert approved.status_code == 200
                if event["kind"] == "terminal":
                    return events, target


def _final_contains(events: list[dict[str, object]], expected: str) -> bool:
    return any(
        isinstance(payload := event.get("payload"), dict)
        and payload.get("type") == "final"
        and expected in str(payload.get("content", ""))
        for event in events
    )


def test_projects_stable_profile_metrics_from_public_events() -> None:
    events = [
        {
            "kind": "run",
            "status": "running",
            "timestamp": "2026-07-17T00:00:00Z",
            "payload": {"event": "run_started"},
        },
        {
            "kind": "tool",
            "status": "running",
            "timestamp": "2026-07-17T00:00:00.010Z",
            "payload": {"type": "tool_call", "tool": "read_file"},
        },
        {
            "kind": "assistant",
            "status": "running",
            "timestamp": "2026-07-17T00:00:00.025Z",
            "payload": {"type": "text_delta", "delta": "Sage"},
        },
        {
            "kind": "assistant",
            "status": "completed",
            "timestamp": "2026-07-17T00:00:00.030Z",
            "payload": {"type": "final", "content": "Sage"},
        },
        {
            "kind": "terminal",
            "status": "completed",
            "timestamp": "2026-07-17T00:00:00.040Z",
            "payload": {"event": "run_finished"},
        },
    ]

    result = project_profile_timeline(
        "read",
        "deerflow_v2",
        events,
        assertions_passed=True,
    )

    assert result.passed is True
    assert result.tool_success_rate == 1.0
    assert result.unpaired_tool_calls == 1
    assert result.tool_event_pairing_rate == 0.0
    assert result.first_token_ms == 25.0
    assert result.duration_ms == 40.0


def test_legacy_and_v2_read_scenario_share_the_same_public_contract(tmp_path: Path) -> None:
    legacy_events = _run_read_scenario(tmp_path, "legacy")
    v2_events = _run_read_scenario(tmp_path, "deerflow_v2")
    report = ProfileParityReport(
        results=[
            project_profile_timeline(
                "read-readme",
                "legacy",
                legacy_events,
                assertions_passed=_final_contains(legacy_events, "Sage"),
            ),
            project_profile_timeline(
                "read-readme",
                "deerflow_v2",
                v2_events,
                assertions_passed=_final_contains(v2_events, "Sage"),
            ),
        ]
    )

    assert report.paired_scenarios() == ("read-readme",)
    assert report.regressions() == ()
    assert all(result.passed for result in report.results), report.to_dict()
    assert all(result.streamed for result in report.results)
    assert all(result.tool_calls == 1 for result in report.results)
    assert all(result.tool_errors == 0 for result in report.results)
    assert report.metrics("legacy").policy_compliance_rate == 1.0
    assert report.metrics("deerflow_v2").policy_compliance_rate == 1.0
    assert report.metrics("legacy").streaming_rate == 1.0
    assert report.metrics("deerflow_v2").streaming_rate == 1.0
    assert report.metrics("legacy").p50_first_token_ms is not None
    assert report.metrics("deerflow_v2").p95_duration_ms >= 0.0


def test_legacy_and_v2_recover_from_a_failed_tool_call(tmp_path: Path) -> None:
    legacy_events = _run_missing_path_recovery_scenario(tmp_path, "legacy")
    v2_events = _run_missing_path_recovery_scenario(tmp_path, "deerflow_v2")
    report = ProfileParityReport(
        results=[
            project_profile_timeline(
                "missing-path-recovery",
                "legacy",
                legacy_events,
                assertions_passed=_final_contains(legacy_events, "Sage"),
            ),
            project_profile_timeline(
                "missing-path-recovery",
                "deerflow_v2",
                v2_events,
                assertions_passed=_final_contains(v2_events, "Sage"),
            ),
        ]
    )

    assert report.regressions() == ()
    assert all(result.passed for result in report.results), report.to_dict()
    assert all(result.streamed for result in report.results)
    assert all(result.tool_calls == 2 for result in report.results)
    assert all(result.tool_errors == 1 for result in report.results)
    assert all(result.unpaired_tool_calls == 0 for result in report.results)
    assert report.results[0].unpaired_tool_results == 1
    assert report.results[1].unpaired_tool_results == 0
    assert report.metrics("legacy").tool_call_success_rate == 0.5
    assert report.metrics("deerflow_v2").tool_call_success_rate == 0.5
    assert report.metrics("legacy").tool_event_pairing_rate == 0.5
    assert report.metrics("deerflow_v2").tool_event_pairing_rate == 1.0


def test_legacy_and_v2_enforce_fresh_read_before_approved_edit(tmp_path: Path) -> None:
    legacy_events, legacy_target = _run_fresh_read_edit_scenario(tmp_path, "legacy")
    v2_events, v2_target = _run_fresh_read_edit_scenario(tmp_path, "deerflow_v2")
    report = ProfileParityReport(
        results=[
            project_profile_timeline(
                "fresh-read-approved-edit",
                "legacy",
                legacy_events,
                assertions_passed=(
                    legacy_target.read_text(encoding="utf-8") == "value = 2\n"
                    and _final_contains(legacy_events, "value 更新为 2")
                ),
                expected_policy_denial=True,
            ),
            project_profile_timeline(
                "fresh-read-approved-edit",
                "deerflow_v2",
                v2_events,
                assertions_passed=(
                    v2_target.read_text(encoding="utf-8") == "value = 2\n"
                    and _final_contains(v2_events, "value 更新为 2")
                ),
                expected_policy_denial=True,
            ),
        ]
    )

    approvals = [
        sum(
            isinstance(payload := event.get("payload"), dict)
            and payload.get("type") == "approval_required"
            for event in events
        )
        for events in (legacy_events, v2_events)
    ]
    assert report.regressions() == ()
    assert all(result.passed for result in report.results), report.to_dict()
    tool_counts = [
        (
            result.tool_calls,
            result.tool_call_events,
            result.tool_results,
            result.paired_tool_events,
            result.unpaired_tool_calls,
            result.unpaired_tool_results,
        )
        for result in report.results
    ]
    assert tool_counts == [(3, 2, 3, 2, 0, 1), (3, 3, 3, 3, 0, 0)], tool_counts
    assert all(result.tool_errors == 1 for result in report.results)
    assert approvals == [1, 1]
    assert report.metrics("legacy").tool_call_success_rate == 2 / 3
    assert report.metrics("deerflow_v2").tool_call_success_rate == 2 / 3


def test_report_surfaces_a_v2_regression_without_hiding_legacy_success() -> None:
    legacy = project_profile_timeline(
        "read",
        "legacy",
        [
            {
                "kind": "assistant",
                "status": "completed",
                "timestamp": "2026-07-17T00:00:00Z",
                "payload": {"type": "final", "content": "ok"},
            },
            {
                "kind": "terminal",
                "status": "completed",
                "timestamp": "2026-07-17T00:00:00Z",
                "payload": {"event": "run_finished"},
            },
        ],
        assertions_passed=True,
    )
    failed_v2 = project_profile_timeline(
        "read",
        "deerflow_v2",
        [
            {
                "kind": "terminal",
                "status": "error",
                "timestamp": "2026-07-17T00:00:00Z",
                "payload": {"event": "run_finished"},
            }
        ],
        assertions_passed=True,
    )

    report = ProfileParityReport(results=[legacy, failed_v2])

    assert report.regressions() == ("read",)
    assert report.to_dict()["profiles"]["legacy"]["task_completion_rate"] == 1.0
    assert report.to_dict()["profiles"]["deerflow_v2"]["task_completion_rate"] == 0.0


def test_expected_policy_denial_is_compliant_but_unexpected_denial_is_not() -> None:
    events = [
        {
            "kind": "tool",
            "status": "error",
            "timestamp": "2026-07-17T00:00:00Z",
            "payload": {
                "type": "tool_result",
                "is_error": True,
                "policy_reason": "plan_mode",
            },
        }
    ]

    expected = project_profile_timeline(
        "plan-denial",
        "deerflow_v2",
        events,
        assertions_passed=True,
        expected_policy_denial=True,
    )
    unexpected = project_profile_timeline(
        "read",
        "deerflow_v2",
        events,
        assertions_passed=True,
    )

    assert expected.policy_compliant is True
    assert unexpected.policy_compliant is False


def test_report_rejects_duplicate_profile_results() -> None:
    result = project_profile_timeline(
        "read",
        "legacy",
        [],
        assertions_passed=False,
    )

    try:
        ProfileParityReport(results=[result, result])
    except ValueError as exc:
        assert str(exc) == "duplicate scenario/runtime_profile result"
    else:
        raise AssertionError("duplicate parity result must fail closed")
