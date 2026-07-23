"""Legacy and DeerFlow V2 parity metrics over the public timeline."""

from __future__ import annotations

from pathlib import Path
from time import monotonic, sleep
from typing import Any

from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from api.main import create_app
from core.coding.context import (
    CompactionCheckpoint,
    CompactionResult,
    CompactionSummary,
    PreparedContext,
)
from core.coding.context.budget import ContextUsage
from core.coding.engine.events import (
    ContextCompactionCompletedEvent,
    ContextCompactionStartedEvent,
)
from evals.coding.profile_parity import (
    ProfileParityReport,
    RuntimeProfile,
    project_profile_timeline,
)
from tests.core.coding.scripted_api_client import ScriptedApiClient


class LegacyReadModel(ScriptedApiClient):
    def __init__(self) -> None:
        super().__init__(
            [
                '<tool>{"name":"read_file","args":{"path":"README.md"}}</tool>',
                "<final>项目名是 Sage。</final>",
            ]
        )


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
        super().__init__(
            [
                '<tool>{"name":"read_file","args":{"path":"missing.md"}}</tool>',
                '<tool>{"name":"read_file","args":{"path":"README.md"}}</tool>',
                "<final>修正路径后确认项目名是 Sage。</final>",
            ]
        )


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
        super().__init__(
            [
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
            ]
        )


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
            LegacyFreshReadEditModel if profile == "legacy" else DeerflowFreshReadEditModel
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


class BindableFakeMessagesListChatModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # type: ignore[no-untyped-def]
        _ = tools, tool_choice, kwargs
        return self


class AppliedParityContextController:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id

    @staticmethod
    def _usage() -> ContextUsage:
        return ContextUsage(
            used_tokens=100,
            effective_limit_tokens=1_000,
            usage_ratio=0.1,
            level="normal",
            estimated=False,
        )

    async def on_turn_start(
        self,
        history: list[dict[str, Any]],
        user_message: str,
        run_id: str,
        **kwargs: Any,
    ) -> PreparedContext:
        del user_message, kwargs
        summary = CompactionSummary(
            goal="保留历史摘要并继续当前读取任务",
            source_transcript_range=(1, max(1, len(history))),
        )
        checkpoint = CompactionCheckpoint(
            compaction_id="compact-parity",
            transcript_start=1,
            transcript_end=max(1, len(history)),
            summary=summary,
            summary_hash="parity-summary-hash",
        )
        projected = [
            {
                "role": "system",
                "kind": "compact_summary",
                "content": summary.render_for_prompt(),
            }
        ]
        result = CompactionResult(
            applied=True,
            projected_history=projected,
            checkpoint=checkpoint,
            before_tokens=2_000,
            after_tokens=100,
            archived_items=len(history),
            compaction_id="compact-parity",
            trigger="auto",
        )
        return PreparedContext.create(
            projected_history=projected,
            usage=self._usage(),
            allow_model_request=True,
            compaction_result=result,
            events=(
                ContextCompactionStartedEvent(
                    session_id=self.session_id,
                    run_id=run_id,
                    compaction_id="compact-parity",
                    trigger="auto",
                    before_tokens=2_000,
                ),
                ContextCompactionCompletedEvent(
                    session_id=self.session_id,
                    run_id=run_id,
                    compaction_id="compact-parity",
                    before_tokens=2_000,
                    after_tokens=100,
                    archived_items=len(history),
                ),
            ),
        )

    def before_model_request(
        self,
        history: list[dict[str, Any]],
        **kwargs: Any,
    ) -> PreparedContext:
        del kwargs
        return PreparedContext.create(
            projected_history=history,
            usage=self._usage(),
            allow_model_request=True,
        )


def _native_tool_call(
    name: str,
    args: dict[str, object],
    tool_call_id: str,
) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": name,
                "args": args,
                "id": tool_call_id,
                "type": "tool_call",
            }
        ],
    )


def _run_scripted_approval_scenario(
    tmp_path: Path,
    *,
    scenario: str,
    profile: str,
    workspace_files: dict[str, str],
    legacy_responses: list[str],
    v2_responses: list[AIMessage],
    prompt: str,
    approval_choice: str,
) -> tuple[list[dict[str, object]], Path]:
    workspace = tmp_path / f"{scenario}-{profile}"
    workspace.mkdir()
    for relative_path, content in workspace_files.items():
        target = workspace / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def model_factory():  # type: ignore[no-untyped-def]
        if profile == "legacy":
            return ScriptedApiClient(list(legacy_responses))
        return BindableFakeMessagesListChatModel(responses=list(v2_responses))

    app = create_app(
        coding_model_factory=model_factory,
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
            websocket.send_json({"content": prompt})
            events: list[dict[str, object]] = []
            while True:
                event = websocket.receive_json()
                events.append(event)
                payload = event.get("payload")
                if isinstance(payload, dict) and payload.get("type") == "approval_required":
                    response = client.post(
                        f"/api/v1/coding/{session_id}/approval/respond",
                        json={
                            "approval_id": payload["approval_id"],
                            "choice": approval_choice,
                        },
                    )
                    assert response.status_code == 200
                if event["kind"] == "terminal":
                    return events, workspace


def _run_compaction_scenario(
    tmp_path: Path,
    profile: str,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    workspace = tmp_path / f"compaction-{profile}"
    workspace.mkdir()
    (workspace / "README.md").write_text("# Sage\n", encoding="utf-8")
    old_turns = 7
    legacy_responses = [f"<final>历史回答 {index}</final>" for index in range(old_turns)] + [
        '<tool>{"name":"read_file","args":{"path":"README.md"}}</tool>',
        "<final>压缩历史后确认项目名是 Sage。</final>",
    ]
    v2_responses = [AIMessage(content=f"历史回答 {index}") for index in range(old_turns)] + [
        _native_tool_call("read_file", {"path": "README.md"}, "call-compact-read"),
        AIMessage(content="压缩历史后确认项目名是 Sage。"),
    ]

    def model_factory():  # type: ignore[no-untyped-def]
        if profile == "legacy":
            return ScriptedApiClient(list(legacy_responses))
        return BindableFakeMessagesListChatModel(responses=list(v2_responses))

    app = create_app(
        coding_model_factory=model_factory,
        coding_workspace_root=workspace,
        coding_storage_root=workspace / ".coding",
        coding_deerflow_v2_enabled=True,
    )
    with TestClient(app) as client:
        session_id = client.post(
            "/api/v1/coding/session",
            json={"runtime_profile": profile},
        ).json()["session_id"]
        runtime = app.state.coding_sessions[session_id]
        coordinator = app.state.coding_run_registry.get(session_id)

        def wait_until_idle() -> None:
            deadline = monotonic() + 2
            while coordinator.active_run_id is not None and monotonic() < deadline:
                sleep(0.001)
            assert coordinator.active_run_id is None

        with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
            for index in range(old_turns):
                websocket.send_json({"content": f"历史问题 {index}"})
                while websocket.receive_json()["kind"] != "terminal":
                    pass
                wait_until_idle()
            runtime.context_controller = AppliedParityContextController(session_id)
            websocket.send_json({"content": "压缩历史后读取 README"})
            events: list[dict[str, object]] = []
            while True:
                event = websocket.receive_json()
                events.append(event)
                if event["kind"] == "terminal":
                    return events, dict(runtime.session.get("context_state", {}))


def _final_contains(events: list[dict[str, object]], expected: str) -> bool:
    return any(
        isinstance(payload := event.get("payload"), dict)
        and payload.get("type") == "final"
        and expected in str(payload.get("content", ""))
        for event in events
    )


def _event_count(events: list[dict[str, object]], event_type: str) -> int:
    return sum(
        isinstance(payload := event.get("payload"), dict) and payload.get("type") == event_type
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


def test_legacy_and_v2_approval_denial_preserves_the_file(tmp_path: Path) -> None:
    legacy_responses = [
        '<tool>{"name":"read_file","args":{"path":"app.py"}}</tool>',
        (
            '<tool>{"name":"patch_file","args":{"path":"app.py",'
            '"old_text":"value = 1","new_text":"value = 2"}}</tool>'
        ),
        "<final>修改被拒绝，文件保持 value = 1。</final>",
    ]
    v2_responses = [
        _native_tool_call("read_file", {"path": "app.py"}, "call-deny-read"),
        _native_tool_call(
            "patch_file",
            {
                "path": "app.py",
                "old_text": "value = 1",
                "new_text": "value = 2",
            },
            "call-deny-patch",
        ),
        AIMessage(content="修改被拒绝，文件保持 value = 1。"),
    ]
    legacy_events, legacy_workspace = _run_scripted_approval_scenario(
        tmp_path,
        scenario="approval-deny",
        profile="legacy",
        workspace_files={"app.py": "value = 1\n"},
        legacy_responses=legacy_responses,
        v2_responses=v2_responses,
        prompt="读取 app.py 后尝试修改，但遵守我的拒绝",
        approval_choice="deny",
    )
    v2_events, v2_workspace = _run_scripted_approval_scenario(
        tmp_path,
        scenario="approval-deny",
        profile="deerflow_v2",
        workspace_files={"app.py": "value = 1\n"},
        legacy_responses=legacy_responses,
        v2_responses=v2_responses,
        prompt="读取 app.py 后尝试修改，但遵守我的拒绝",
        approval_choice="deny",
    )
    report = ProfileParityReport(
        results=[
            project_profile_timeline(
                "approval-deny",
                "legacy",
                legacy_events,
                assertions_passed=(
                    (legacy_workspace / "app.py").read_text(encoding="utf-8") == "value = 1\n"
                    and _final_contains(legacy_events, "修改被拒绝")
                ),
            ),
            project_profile_timeline(
                "approval-deny",
                "deerflow_v2",
                v2_events,
                assertions_passed=(
                    (v2_workspace / "app.py").read_text(encoding="utf-8") == "value = 1\n"
                    and _final_contains(v2_events, "修改被拒绝")
                ),
            ),
        ]
    )

    assert report.regressions() == ()
    assert all(result.passed for result in report.results), report.to_dict()
    assert all(result.tool_calls == 2 for result in report.results)
    assert all(result.tool_errors == 1 for result in report.results)
    assert [_event_count(events, "approval_required") for events in (legacy_events, v2_events)] == [
        1,
        1,
    ]
    assert report.metrics("legacy").tool_call_success_rate == 0.5
    assert report.metrics("deerflow_v2").tool_call_success_rate == 0.5


def test_legacy_and_v2_session_approval_is_reused_for_two_edits(tmp_path: Path) -> None:
    legacy_responses = [
        '<tool>{"name":"read_file","args":{"path":"first.py"}}</tool>',
        (
            '<tool>{"name":"patch_file","args":{"path":"first.py",'
            '"old_text":"value = 1","new_text":"value = 2"}}</tool>'
        ),
        '<tool>{"name":"read_file","args":{"path":"second.py"}}</tool>',
        (
            '<tool>{"name":"patch_file","args":{"path":"second.py",'
            '"old_text":"value = 1","new_text":"value = 2"}}</tool>'
        ),
        "<final>会话授权已复用，两处 value 均更新为 2。</final>",
    ]
    v2_responses = [
        _native_tool_call("read_file", {"path": "first.py"}, "call-session-read-first"),
        _native_tool_call(
            "patch_file",
            {
                "path": "first.py",
                "old_text": "value = 1",
                "new_text": "value = 2",
            },
            "call-session-patch-first",
        ),
        _native_tool_call("read_file", {"path": "second.py"}, "call-session-read-second"),
        _native_tool_call(
            "patch_file",
            {
                "path": "second.py",
                "old_text": "value = 1",
                "new_text": "value = 2",
            },
            "call-session-patch-second",
        ),
        AIMessage(content="会话授权已复用，两处 value 均更新为 2。"),
    ]
    workspaces: list[Path] = []
    timelines: list[list[dict[str, object]]] = []
    for profile in ("legacy", "deerflow_v2"):
        events, workspace = _run_scripted_approval_scenario(
            tmp_path,
            scenario="approval-session",
            profile=profile,
            workspace_files={"first.py": "value = 1\n", "second.py": "value = 1\n"},
            legacy_responses=legacy_responses,
            v2_responses=v2_responses,
            prompt="把两个文件中的 value 都改为 2",
            approval_choice="session",
        )
        timelines.append(events)
        workspaces.append(workspace)

    results = []
    profiles: tuple[RuntimeProfile, ...] = ("legacy", "deerflow_v2")
    for profile, events, workspace in zip(profiles, timelines, workspaces, strict=True):
        files_updated = all(
            (workspace / filename).read_text(encoding="utf-8") == "value = 2\n"
            for filename in ("first.py", "second.py")
        )
        results.append(
            project_profile_timeline(
                "approval-session",
                profile,
                events,
                assertions_passed=(
                    files_updated and _final_contains(events, "两处 value 均更新为 2")
                ),
            )
        )
    report = ProfileParityReport(results=results)

    assert report.regressions() == ()
    assert all(result.passed for result in report.results), report.to_dict()
    assert all(result.tool_calls == 4 for result in report.results)
    assert all(result.tool_errors == 0 for result in report.results)
    assert [_event_count(events, "approval_required") for events in timelines] == [1, 1]
    assert report.metrics("legacy").tool_call_success_rate == 1.0
    assert report.metrics("deerflow_v2").tool_call_success_rate == 1.0


def test_legacy_and_v2_continue_tool_loop_after_auto_compaction(tmp_path: Path) -> None:
    legacy_events, legacy_state = _run_compaction_scenario(tmp_path, "legacy")
    v2_events, v2_state = _run_compaction_scenario(tmp_path, "deerflow_v2")
    report = ProfileParityReport(
        results=[
            project_profile_timeline(
                "auto-compaction-tool-loop",
                "legacy",
                legacy_events,
                assertions_passed=(
                    _final_contains(legacy_events, "Sage")
                    and legacy_state.get("checkpoint_id") == "compact-parity"
                    and _event_count(legacy_events, "context_compaction_completed") == 1
                ),
            ),
            project_profile_timeline(
                "auto-compaction-tool-loop",
                "deerflow_v2",
                v2_events,
                assertions_passed=(
                    _final_contains(v2_events, "Sage")
                    and v2_state.get("checkpoint_id") == "compact-parity"
                    and _event_count(v2_events, "context_compaction_completed") == 1
                    and _event_count(v2_events, "graph_context_compacted") == 1
                ),
            ),
        ]
    )

    assert report.regressions() == ()
    assert all(result.passed for result in report.results), report.to_dict()
    assert all(result.streamed for result in report.results)
    assert all(result.tool_calls == 1 for result in report.results)
    assert all(result.tool_errors == 0 for result in report.results)
    assert report.metrics("legacy").tool_call_success_rate == 1.0
    assert report.metrics("deerflow_v2").tool_call_success_rate == 1.0


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
    profile_metrics = report.to_dict()["profiles"]
    assert isinstance(profile_metrics, dict)
    legacy_metrics = profile_metrics["legacy"]
    v2_metrics = profile_metrics["deerflow_v2"]
    assert isinstance(legacy_metrics, dict)
    assert isinstance(v2_metrics, dict)
    assert legacy_metrics["task_completion_rate"] == 1.0
    assert v2_metrics["task_completion_rate"] == 0.0


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
