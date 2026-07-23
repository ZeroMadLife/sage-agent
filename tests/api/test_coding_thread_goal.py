from __future__ import annotations

import os
from pathlib import Path
from time import monotonic, sleep
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api.coding import _drain_mastery_outbox, _goal_evaluator
from api.main import create_app
from core.coding.persistence.session_event_journal import SessionEventJournal
from core.coding.usage_store import UsageStore
from core.harness.thread_goal import ThreadGoalService
from core.harness.thread_goal_evaluator import (
    GoalCriterionDecision,
    GoalEvaluationDecision,
    build_thread_goal_evaluation_request,
)
from core.knowledge import KnowledgeSourceRoot
from core.learning import MasteryCapability, MasteryLedger
from tests.api.test_coding_routes import FakeModel


def _app(tmp_path: Path):
    return create_app(
        coding_model_factory=FakeModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
        coding_default_runtime_profile="legacy",
    )


def _knowledge_app(tmp_path: Path):
    coding = tmp_path / "coding"
    coding.mkdir()
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    vault = tmp_path / "vault"
    vault.mkdir()
    return create_app(
        coding_model_factory=FakeModel,
        coding_workspace_root=coding,
        coding_storage_root=tmp_path / ".coding",
        coding_default_runtime_profile="legacy",
        knowledge_workspace_root=knowledge,
        knowledge_database_path=knowledge / ".sage" / "knowledge.sqlite3",
        knowledge_source_roots={
            "sage-learning": KnowledgeSourceRoot(
                root_id="sage-learning",
                kind="obsidian",
                label="Sage Learning",
                path=vault,
            )
        },
        knowledge_jobs_enabled=False,
    )


class TwoTurnModel(FakeModel):
    def __init__(self) -> None:
        super().__init__()
        self.responses.extend(
            [
                '<tool>{"name":"read_file","args":{"path":"README.md"}}</tool>',
                "<final>第二轮也产生了公开结果。</final>",
            ]
        )


class ContinueEvaluator:
    def __init__(self) -> None:
        self.calls = 0

    async def evaluate(self, request):
        self.calls += 1
        return GoalEvaluationDecision(
            status="continue",
            blocker="goal_not_met_yet",
            evidence_refs=tuple(sorted(request.allowed_evidence_refs)[:1]),
            next_action="继续读取现有资料并补齐证据",
            criteria=(GoalCriterionDecision(index=0, status="unmet", evidence_refs=()),),
        )


class FailingModel:
    async def complete(self, prompt: str) -> str:
        _ = prompt
        raise RuntimeError("provider failed")


class UsageEvaluationModel:
    async def complete(self, prompt: str, *, max_tokens: int):
        _ = prompt
        assert max_tokens == 600
        return SimpleNamespace(
            content=(
                '{"status":"continue","blocker":"goal_not_met_yet",'
                '"evidence_refs":[],"next_action":"补充证据",'
                '"criteria":[{"index":0,"status":"unmet","evidence_refs":[]}]}'
            ),
            usage_metadata={
                "input_tokens": 120,
                "output_tokens": 30,
                "total_tokens": 150,
            },
        )


def _receive_until_terminal(websocket) -> list[dict]:
    events: list[dict] = []
    while True:
        event = websocket.receive_json()
        events.append(event)
        if event["kind"] == "terminal":
            return events


def test_thread_goal_crud_evaluate_continue_and_timeline_audit(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
        assert client.get(f"/api/v1/coding/{session_id}/goal").json() == {
            "goal": None,
            "revision": 0,
        }

        created = client.put(
            f"/api/v1/coding/{session_id}/goal",
            json={
                "expected_revision": 0,
                "description": "解释 checkpoint 恢复边界",
                "completion_criteria": ["引用官方资料", "比较 thread 与 checkpoint"],
            },
        )
        assert created.status_code == 200
        goal = created.json()["goal"]
        assert goal["revision"] == 1
        assert goal["evaluation"]["blocker"] == "goal_not_met_yet"

        stale = client.put(
            f"/api/v1/coding/{session_id}/goal",
            json={
                "expected_revision": 0,
                "description": "stale",
                "completion_criteria": ["stale"],
            },
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["current_revision"] == 1

        prepared = client.post(
            f"/api/v1/coding/{session_id}/goal/continue",
            json={"expected_revision": 1},
        )
        assert prepared.status_code == 200
        assert prepared.json()["goal_revision"] == 1
        assert "checkpoint 恢复边界" in prepared.json()["prompt"]

        with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
            websocket.send_json(
                {
                    "content": prepared.json()["prompt"],
                    "thread_goal_revision": 1,
                }
            )
            events = _receive_until_terminal(websocket)

        started = next(item for item in events if item["payload"].get("event") == "run_started")
        assert started["payload"]["thread_goal"]["revision"] == 1
        assert started["payload"]["thread_goal"]["goal_id"] == goal["goal_id"]

        evaluated = client.post(
            f"/api/v1/coding/{session_id}/goal/evaluate",
            json={"expected_revision": 1},
        )
        assert evaluated.status_code == 200
        assert evaluated.json()["goal"]["revision"] == 2
        assert evaluated.json()["goal"]["evaluation"]["source_run_id"] == started["run_id"]

        timeline = client.get(f"/api/v1/coding/session/{session_id}/timeline?limit=100").json()[
            "items"
        ]
        lifecycle = [
            item["payload"]["type"]
            for item in timeline
            if item["payload"].get("type", "").startswith("thread_goal_")
        ]
        assert lifecycle == ["thread_goal_updated", "thread_goal_evaluated"]

        cleared = client.post(
            f"/api/v1/coding/{session_id}/goal/clear",
            json={"expected_revision": 2},
        )
        assert cleared.status_code == 204
        assert client.get(f"/api/v1/coding/{session_id}/goal").json() == {
            "goal": None,
            "revision": 3,
        }

        recreated = client.put(
            f"/api/v1/coding/{session_id}/goal",
            json={
                "expected_revision": 3,
                "description": "重建后的新目标",
                "completion_criteria": ["revision 继续单调增长"],
            },
        )
        assert recreated.status_code == 200
        assert recreated.json()["goal"]["revision"] == 4


def test_thread_goal_learning_binding_is_canonicalized_from_knowledge_goal(
    tmp_path: Path,
) -> None:
    app = _knowledge_app(tmp_path)
    with TestClient(app) as client:
        session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
        current = client.get("/api/v1/knowledge/goal").json()
        learning = client.put(
            "/api/v1/knowledge/goal",
            json={
                "expected_goal_revision": current["goal_revision"],
                "goal_id": "langgraph",
                "title": "LangGraph",
                "description": "恢复能力",
                "capabilities": [
                    {
                        "capability_id": "checkpoint",
                        "label": "Checkpoint 恢复",
                        "description": "正确恢复线程",
                        "keywords": ["checkpoint"],
                        "weight": 2.5,
                        "required": True,
                    }
                ],
            },
        ).json()
        created = client.put(
            f"/api/v1/coding/{session_id}/goal",
            json={
                "expected_revision": 0,
                "description": "验证 checkpoint",
                "completion_criteria": ["测试通过"],
                "learning_goal": {
                    "goal_id": "langgraph",
                    "goal_revision": learning["goal_revision"],
                    "capabilities": [{"capability_id": "checkpoint", "criterion_indexes": [0]}],
                },
            },
        )

    assert created.status_code == 200
    binding = created.json()["goal"]["learning_goal"]
    assert binding["workspace_id"] == "knowledge-local"
    assert binding["capabilities"] == [
        {
            "capability_id": "checkpoint",
            "label": "Checkpoint 恢复",
            "weight": 2.5,
            "required": True,
            "criterion_indexes": [0],
        }
    ]


def test_thread_goal_rejects_stale_learning_goal_revision(tmp_path: Path) -> None:
    app = _knowledge_app(tmp_path)
    with TestClient(app) as client:
        session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
        response = client.put(
            f"/api/v1/coding/{session_id}/goal",
            json={
                "expected_revision": 0,
                "description": "stale binding",
                "completion_criteria": ["one"],
                "learning_goal": {
                    "goal_id": "personal-learning",
                    "goal_revision": "kgoal_stale",
                    "capabilities": [{"capability_id": "missing", "criterion_indexes": [0]}],
                },
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "learning_goal_revision_conflict"


def test_stale_continue_revision_is_rejected_before_model_execution(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
        client.put(
            f"/api/v1/coding/{session_id}/goal",
            json={
                "expected_revision": 0,
                "description": "first",
                "completion_criteria": ["one"],
            },
        )
        client.put(
            f"/api/v1/coding/{session_id}/goal",
            json={
                "expected_revision": 1,
                "description": "second",
                "completion_criteria": ["two"],
            },
        )

        with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
            websocket.send_json({"content": "continue stale", "thread_goal_revision": 1})
            events = _receive_until_terminal(websocket)

    error = next(item for item in events if item["payload"].get("type") == "error")
    assert "revision changed" in error["payload"]["message"]
    assert events[-1]["payload"] == {"event": "input_rejected"}


def test_bounded_auto_followup_runs_once_without_faking_a_user_message(tmp_path: Path) -> None:
    evaluator = ContinueEvaluator()
    app = create_app(
        coding_model_factory=TwoTurnModel,
        coding_goal_evaluator_factory=evaluator,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
        coding_default_runtime_profile="legacy",
    )
    with TestClient(app) as client:
        session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
        created = client.put(
            f"/api/v1/coding/{session_id}/goal",
            json={
                "expected_revision": 0,
                "description": "读取项目证据",
                "completion_criteria": ["至少有一条工具证据"],
            },
        ).json()["goal"]
        assert created["continuation"]["mode"] == "manual"
        configured = client.patch(
            f"/api/v1/coding/{session_id}/goal/continuation",
            json={
                "expected_revision": 1,
                "mode": "bounded_auto",
                "max_auto_followups": 1,
            },
        )
        assert configured.status_code == 200
        assert configured.json()["goal"]["revision"] == 2

        with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
            websocket.send_json({"content": "读取 README", "thread_goal_revision": 2})
            events: list[dict] = []
            terminals = 0
            while terminals < 2:
                item = websocket.receive_json()
                events.append(item)
                if item["kind"] == "terminal":
                    terminals += 1

        run_starts = [item for item in events if item["payload"].get("event") == "run_started"]
        assert len(run_starts) == 2
        followup_start = run_starts[1]
        assert followup_start["run_id"].startswith("run_goal_")
        assert (
            followup_start["payload"]["goal_followup"]["source_run_id"] == run_starts[0]["run_id"]
        )
        assert any(item["payload"].get("type") == "thread_goal_followup_started" for item in events)
        assert [item for item in events if item["kind"] == "user"] == [
            next(item for item in events if item["kind"] == "user")
        ]

        deadline = monotonic() + 2
        while True:
            goal = client.get(f"/api/v1/coding/{session_id}/goal").json()["goal"]
            if goal["revision"] == 4 or monotonic() >= deadline:
                break
            sleep(0.01)
        assert goal["revision"] == 4
        assert goal["continuation"]["auto_followups_started"] == 1
        assert goal["continuation"]["stop_reason"] == "max_auto_followups"
        assert evaluator.calls == 2


def test_manual_goal_never_invokes_post_turn_evaluator(tmp_path: Path) -> None:
    evaluator = ContinueEvaluator()
    app = create_app(
        coding_model_factory=FakeModel,
        coding_goal_evaluator_factory=evaluator,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
        coding_default_runtime_profile="legacy",
    )
    with TestClient(app) as client:
        session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
        client.put(
            f"/api/v1/coding/{session_id}/goal",
            json={
                "expected_revision": 0,
                "description": "保持手动",
                "completion_criteria": ["用户决定下一轮"],
            },
        )
        with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
            websocket.send_json({"content": "读取 README", "thread_goal_revision": 1})
            _receive_until_terminal(websocket)
        goal = client.get(f"/api/v1/coding/{session_id}/goal").json()["goal"]

    assert goal["revision"] == 1
    assert goal["continuation"]["mode"] == "manual"
    assert evaluator.calls == 0


def test_failed_run_is_evaluated_by_rule_without_an_extra_model_call(tmp_path: Path) -> None:
    evaluator = ContinueEvaluator()
    app = create_app(
        coding_model_factory=FailingModel,
        coding_goal_evaluator_factory=evaluator,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
        coding_default_runtime_profile="legacy",
    )
    with TestClient(app) as client:
        session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
        client.put(
            f"/api/v1/coding/{session_id}/goal",
            json={
                "expected_revision": 0,
                "description": "失败后停止",
                "completion_criteria": ["完成一轮"],
            },
        )
        client.patch(
            f"/api/v1/coding/{session_id}/goal/continuation",
            json={
                "expected_revision": 1,
                "mode": "bounded_auto",
                "max_auto_followups": 1,
            },
        )
        with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
            websocket.send_json({"content": "触发失败", "thread_goal_revision": 2})
            events = _receive_until_terminal(websocket)
        assert events[-1]["status"] == "error"
        deadline = monotonic() + 2
        while True:
            goal = client.get(f"/api/v1/coding/{session_id}/goal").json()["goal"]
            if goal["revision"] == 3 or monotonic() >= deadline:
                break
            sleep(0.01)

    assert goal["evaluation"]["blocker"] == "run_failed"
    assert goal["continuation"]["auto_followups_started"] == 0
    assert evaluator.calls == 0


@pytest.mark.asyncio
async def test_default_goal_evaluator_records_real_provider_usage(tmp_path: Path) -> None:
    usage_store = UsageStore(tmp_path / "usage.sqlite3")
    runtime = SimpleNamespace(
        model=UsageEvaluationModel(),
        model_spec="test:evaluator",
        session_id="session-usage",
        usage_store=usage_store,
        _current_model_factory=lambda **kwargs: UsageEvaluationModel(),
    )
    request = build_thread_goal_evaluation_request(
        goal={
            "goal_id": "goal-usage",
            "revision": 1,
            "description": "记录评估用量",
            "completion_criteria": ["有真实统计"],
        },
        run_id="run-usage",
        events=[],
    )

    await _goal_evaluator(SimpleNamespace(state=SimpleNamespace()), runtime, "run-usage").evaluate(
        request
    )
    summary = usage_store.summary(days=1)

    assert summary["request_count"] == 1
    assert summary["input_tokens"] == 120
    assert summary["output_tokens"] == 30
    assert summary["total_tokens"] == 150


@pytest.mark.asyncio
async def test_mastery_outbox_commits_receipt_and_does_not_replay(tmp_path: Path) -> None:
    journal = SessionEventJournal(tmp_path / "sessions", "session-mastery")
    source_event = journal.append(
        run_id="thread-goal",
        kind="harness",
        status="completed",
        payload={
            "type": "thread_goal_evaluated",
            "version": 2,
            "source_run_id": "run-practice",
            "mastery_deposit": {
                "workspace_id": "workspace-a",
                "learning_goal_id": "goal-a",
                "learning_goal_revision": "goal-rev-1",
                "items": [
                    {
                        "evidence_id": "mastery-evidence-1",
                        "capability_id": "checkpoint",
                        "kind": "code_test",
                        "result": "pass",
                        "source_ref": "subagent://run-practice/result/1",
                        "source_evidence_id": "practice-1",
                        "session_id": "session-mastery",
                        "run_id": "run-practice",
                        "summary": "目标测试通过",
                        "metadata": {"exit_code": 0},
                        "created_at": "2026-07-20T00:00:01+00:00",
                    }
                ],
            },
        },
    )
    ledger = MasteryLedger(tmp_path / "mastery.sqlite3")
    app = SimpleNamespace(
        state=SimpleNamespace(
            mastery_ledger=ledger,
            coding_run_registry=SimpleNamespace(
                get=lambda session_id: SimpleNamespace(journal=journal)
            ),
            coding_sessions={"session-mastery": SimpleNamespace(owner_user_id="owner-a")},
        )
    )

    await _drain_mastery_outbox(app, "session-mastery")
    await _drain_mastery_outbox(app, "session-mastery")

    assert journal.pending_mastery_deposits() == ()
    receipts = [
        event
        for event in journal.events_for_run("run-practice")
        if event.payload.get("type") == "mastery_evidence_recorded"
    ]
    assert len(receipts) == 1
    assert receipts[0].payload["source_event_id"] == source_event.event_id
    projection = ledger.project(
        owner_id="owner-a",
        workspace_id="workspace-a",
        learning_goal_id="goal-a",
        learning_goal_revision="goal-rev-1",
        capabilities=(MasteryCapability("checkpoint", "Checkpoint", 1.0),),
    )
    assert projection.score == 0.5
    assert projection.evidence[0].evidence_id == "mastery-evidence-1"


def test_restart_resumes_one_durable_goal_followup_without_duplication(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# restart evidence\n", encoding="utf-8")
    storage_root = tmp_path / ".coding"
    options = {
        "coding_model_factory": TwoTurnModel,
        "coding_workspace_root": tmp_path,
        "coding_storage_root": storage_root,
        "coding_default_runtime_profile": "legacy",
    }
    with TestClient(create_app(**options)) as first_client:
        session_id = first_client.post("/api/v1/coding/session", json={}).json()["session_id"]
        first_client.put(
            f"/api/v1/coding/{session_id}/goal",
            json={
                "expected_revision": 0,
                "description": "恢复后继续收集证据",
                "completion_criteria": ["至少有一条工具证据"],
            },
        )
        goal = first_client.patch(
            f"/api/v1/coding/{session_id}/goal/continuation",
            json={
                "expected_revision": 1,
                "mode": "bounded_auto",
                "max_auto_followups": 1,
            },
        ).json()["goal"]
        journal = first_client.app.state.coding_run_registry.get(session_id).journal
        begun = journal.begin_run(
            "run-before-restart",
            owner_id="restart-test",
            owner_pid=os.getpid(),
            thread_goal=goal,
            expected_thread_goal_revision=2,
        )
        journal.append(
            run_id="run-before-restart",
            kind="tool",
            status="completed",
            payload={
                "type": "tool_result",
                "tool": "read_file",
                "summary": "restart evidence",
                "evidence_ref": "evidence:before-restart",
            },
            lease_owner_id="restart-test",
            fencing_token=begun.fencing_token,
        )
        journal.append_terminal_and_release(
            run_id="run-before-restart",
            status="completed",
            payload={"event": "run_completed"},
            lease_owner_id="restart-test",
            fencing_token=begun.fencing_token,
        )
        request = build_thread_goal_evaluation_request(
            goal=goal,
            run_id="run-before-restart",
            events=journal.events_for_run("run-before-restart"),
        )
        reserved = ThreadGoalService(journal).evaluate_post_turn(
            request=request,
            decision=GoalEvaluationDecision(
                status="continue",
                blocker="goal_not_met_yet",
                evidence_refs=("evidence:before-restart",),
                next_action="继续读取 README",
                criteria=(GoalCriterionDecision(index=0, status="unmet", evidence_refs=()),),
            ),
            terminal_status="completed",
        )
        assert reserved.reservation is not None

    evaluator = ContinueEvaluator()
    with TestClient(create_app(**options, coding_goal_evaluator_factory=evaluator)) as restarted:
        resumed = restarted.post(f"/api/v1/coding/session/{session_id}/resume")
        assert resumed.status_code == 200
        deadline = monotonic() + 3
        while True:
            goal = restarted.get(f"/api/v1/coding/{session_id}/goal").json()["goal"]
            if goal["revision"] >= 4 or monotonic() >= deadline:
                break
            sleep(0.01)
        assert goal["revision"] == 4
        assert goal["continuation"]["auto_followups_started"] == 1
        assert goal["continuation"]["stop_reason"] == "max_auto_followups"
        assert evaluator.calls == 1

        second_resume = restarted.post(f"/api/v1/coding/session/{session_id}/resume")
        assert second_resume.status_code == 200
        sleep(0.05)
        timeline = restarted.get(f"/api/v1/coding/session/{session_id}/timeline?limit=100").json()[
            "items"
        ]
        followup_starts = [
            item
            for item in timeline
            if item["payload"].get("event") == "run_started"
            and item["payload"].get("goal_followup") is not None
        ]
        assert len(followup_starts) == 1
