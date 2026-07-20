from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.coding.persistence.session_event_journal import SessionEventJournal
from core.harness.thread_goal_evaluator import (
    StructuredThreadGoalEvaluator,
    build_thread_goal_evaluation_request,
)


class StaticModel:
    def __init__(self, response: object) -> None:
        self.response = response
        self.prompts: list[str] = []

    async def complete(self, prompt: str, *, max_tokens: int) -> object:
        self.prompts.append(prompt)
        assert max_tokens == 600
        return self.response


def _goal() -> dict[str, object]:
    return {
        "goal_id": "goal-1",
        "revision": 3,
        "description": "解释 checkpoint 恢复",
        "completion_criteria": ["给出官方引用", "说明 thread 与 checkpoint 的区别"],
    }


def _request(tmp_path: Path):
    journal = SessionEventJournal(tmp_path, "session-1")
    journal.append(
        run_id="run-1",
        kind="tool",
        status="completed",
        payload={
            "type": "tool_result",
            "tool": "search_web",
            "citation_id": "wcite_official",
            "content": "官方文档说明 checkpoint 保存线程状态。",
        },
        event_id="tool-search-1",
    )
    journal.append(
        run_id="run-1",
        kind="assistant",
        status="completed",
        payload={"type": "text_delta", "delta": "thread 是逻辑会话，checkpoint 是状态快照。"},
        event_id="assistant-1",
    )
    return build_thread_goal_evaluation_request(
        goal=_goal(),
        run_id="run-1",
        events=journal.events_for_run("run-1"),
    )


@pytest.mark.asyncio
async def test_structured_goal_evaluator_accepts_fully_evidenced_satisfaction(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    model = StaticModel(
        json.dumps(
            {
                "status": "satisfied",
                "blocker": None,
                "evidence_refs": ["wcite_official", "run:run-1:assistant"],
                "next_action": "保持当前成果",
                "criteria": [
                    {"index": 0, "status": "met", "evidence_refs": ["wcite_official"]},
                    {
                        "index": 1,
                        "status": "met",
                        "evidence_refs": ["run:run-1:assistant"],
                    },
                ],
            },
            ensure_ascii=False,
        )
    )

    decision = await StructuredThreadGoalEvaluator(model).evaluate(request)

    assert decision.status == "satisfied"
    assert decision.blocker is None
    assert decision.evidence_refs == ("wcite_official", "run:run-1:assistant")
    assert request.progress_fingerprint
    assert "untrusted_evidence" in model.prompts[0]


@pytest.mark.asyncio
async def test_structured_goal_evaluator_deduplicates_refs_across_criteria(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    model = StaticModel(
        json.dumps(
            {
                "status": "continue",
                "blocker": "goal_not_met_yet",
                "evidence_refs": ["wcite_official"],
                "next_action": "补充证据",
                "criteria": [
                    {"index": 0, "status": "met", "evidence_refs": ["wcite_official"]},
                    {"index": 1, "status": "unmet", "evidence_refs": ["wcite_official"]},
                ],
            }
        )
    )

    decision = await StructuredThreadGoalEvaluator(model).evaluate(request)

    assert decision.evidence_refs == ("wcite_official",)


@pytest.mark.asyncio
async def test_structured_goal_evaluator_downgrades_unsupported_satisfaction(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    model = StaticModel(
        json.dumps(
            {
                "status": "satisfied",
                "blocker": None,
                "evidence_refs": ["forged-secret-ref"],
                "next_action": "完成",
                "criteria": [
                    {"index": 0, "status": "met", "evidence_refs": ["forged-secret-ref"]},
                    {"index": 1, "status": "met", "evidence_refs": []},
                ],
            }
        )
    )

    decision = await StructuredThreadGoalEvaluator(model).evaluate(request)

    assert decision.status == "continue"
    assert decision.blocker == "missing_evidence"
    assert decision.evidence_refs == ()


@pytest.mark.asyncio
async def test_structured_goal_evaluator_rejects_protocol_text_and_duplicate_keys(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)

    with pytest.raises(ValueError, match="exactly one JSON object"):
        await StructuredThreadGoalEvaluator(StaticModel('thought\n{"status":"continue"}')).evaluate(
            request
        )

    duplicate = (
        '{"status":"continue","status":"satisfied","blocker":"goal_not_met_yet",'
        '"evidence_refs":[],"next_action":"x","criteria":[]}'
    )
    with pytest.raises(ValueError, match="duplicate JSON key"):
        await StructuredThreadGoalEvaluator(StaticModel(duplicate)).evaluate(request)


def test_goal_evaluation_request_bounds_public_trace_and_ignores_user_text(
    tmp_path: Path,
) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    journal.append(
        run_id="run-1",
        kind="user",
        status="completed",
        payload={"type": "user", "content": "private user prompt"},
    )
    journal.append(
        run_id="run-1",
        kind="assistant",
        status="completed",
        payload={"type": "text_delta", "delta": "x" * 20_000},
    )

    request = build_thread_goal_evaluation_request(
        goal=_goal(), run_id="run-1", events=journal.events_for_run("run-1")
    )

    assert "private user prompt" not in request.public_trace
    assert len(request.public_trace) <= 16_000
    assert request.allowed_evidence_refs == frozenset({"run:run-1:assistant"})
    assert request.progress_fingerprint == ""


def test_full_evidence_catalog_does_not_allow_an_unseen_assistant_ref(
    tmp_path: Path,
) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    for index in range(32):
        journal.append(
            run_id="run-1",
            kind="tool",
            status="completed",
            payload={"type": "tool_result", "summary": f"evidence {index}"},
            event_id=f"tool-{index}",
        )
    journal.append(
        run_id="run-1",
        kind="assistant",
        status="completed",
        payload={"type": "text_delta", "delta": "assistant conclusion"},
    )

    request = build_thread_goal_evaluation_request(
        goal=_goal(), run_id="run-1", events=journal.events_for_run("run-1")
    )

    assert len(request.evidence) == 32
    assert "run:run-1:assistant" not in request.allowed_evidence_refs
    assert all(item.ref in request.public_trace for item in request.evidence)


def test_large_public_evidence_trace_remains_valid_json_and_whitelist_matches(
    tmp_path: Path,
) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    for index in range(32):
        journal.append(
            run_id="run-1",
            kind="tool",
            status="completed",
            payload={"type": "tool_result", "summary": f"{index}:" + "x" * 1_100},
            event_id=f"large-tool-{index}",
        )

    request = build_thread_goal_evaluation_request(
        goal=_goal(), run_id="run-1", events=journal.events_for_run("run-1")
    )
    decoded = json.loads(request.public_trace)
    visible_refs = {item["ref"] for item in decoded["untrusted_evidence"]}

    assert len(request.public_trace) <= 16_000
    assert visible_refs == request.allowed_evidence_refs
    assert visible_refs == {item.ref for item in request.evidence}
