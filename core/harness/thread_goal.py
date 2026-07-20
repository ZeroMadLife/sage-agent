"""Revisioned Thread Goal lifecycle on the shared Chat Harness journal."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

from core.coding.persistence.session_event_journal import (
    SessionEventJournal,
    SessionRunLeaseConflictError,
    SessionThreadGoalConflictError,
)
from core.harness.thread_goal_evaluator import (
    GoalEvaluationDecision,
    ThreadGoalEvaluationRequest,
)

GoalEvaluationStatus = Literal["satisfied", "blocked", "continue"]
GoalBlocker = Literal[
    "missing_evidence",
    "needs_user_input",
    "run_failed",
    "external_wait",
    "goal_not_met_yet",
    "no_progress",
]
GoalContinuationMode = Literal["manual", "bounded_auto"]

_REFERENCE_KEYS = frozenset(
    {
        "evidence_ref",
        "evidence_refs",
        "citation_id",
        "artifact_ref",
        "result_ref",
        "source_ref",
        "page_revision",
        "revision_ref",
    }
)


class ThreadGoalError(RuntimeError):
    """Base Thread Goal service error."""


class ThreadGoalNotFoundError(ThreadGoalError):
    """No primary Goal is configured for this Thread."""


class ThreadGoalBusyError(ThreadGoalError):
    """The Goal cannot mutate while its session owns an active run lease."""


@dataclass(frozen=True, slots=True)
class ThreadGoalContinue:
    goal_id: str
    goal_revision: int
    prompt: str


@dataclass(frozen=True, slots=True)
class ThreadGoalPostTurn:
    goal: dict[str, Any]
    reservation: dict[str, Any] | None


class ThreadGoalService:
    """Validate and persist a single session-level Goal without a second runtime."""

    def __init__(self, journal: SessionEventJournal) -> None:
        self.journal = journal

    def get(self) -> dict[str, Any] | None:
        current = self.journal.current_thread_goal()
        return _normalized_goal(current) if current is not None else None

    def upsert(
        self,
        *,
        description: str,
        completion_criteria: Iterable[str],
        expected_revision: int,
    ) -> dict[str, Any]:
        self._require_idle()
        description_text = _bounded_text(description, field="description", maximum=2_000)
        criteria = _criteria(completion_criteria)
        current = self.get()
        actual_revision = self.journal.current_thread_goal_revision()
        if actual_revision != expected_revision:
            # Journal remains the final atomic CAS authority. This early branch
            # avoids generating a new identity for an already-stale create.
            raise SessionThreadGoalConflictError(actual_revision)
        now = datetime.now(UTC).isoformat()
        goal_id = (
            str(current.get("goal_id"))
            if current
            else "goal-" + hashlib.sha256(self.journal.session_id.encode("utf-8")).hexdigest()[:20]
        )
        evaluation = {
            "status": "continue",
            "blocker": "goal_not_met_yet",
            "evidence_refs": [],
            "next_action": "开始执行当前目标并收集可验证证据",
            "source_run_id": None,
            "evaluated_at": now,
        }
        goal: dict[str, Any] = {
            "goal_id": goal_id,
            "revision": expected_revision + 1,
            "description": description_text,
            "completion_criteria": criteria,
            "status": "active",
            "evaluation": evaluation,
            "continuation": _reset_continuation(current),
            "created_at": str(current.get("created_at")) if current else now,
            "updated_at": now,
        }
        try:
            stored = self.journal.append_thread_goal(
                event_type="thread_goal_updated",
                expected_revision=expected_revision,
                goal=goal,
            )
        except SessionRunLeaseConflictError as exc:
            raise ThreadGoalBusyError("Thread Goal cannot change while a run is active") from exc
        return dict(stored.payload["goal"])

    def configure_continuation(
        self,
        *,
        expected_revision: int,
        mode: GoalContinuationMode,
        max_auto_followups: int,
    ) -> dict[str, Any]:
        """Change the explicit follow-up policy and reset all loop counters."""
        self._require_idle()
        current = self._current_at(expected_revision)
        if mode not in {"manual", "bounded_auto"}:
            raise ValueError("continuation mode must be manual or bounded_auto")
        if isinstance(max_auto_followups, bool) or not 1 <= max_auto_followups <= 4:
            raise ValueError("max_auto_followups must be between 1 and 4")
        now = datetime.now(UTC).isoformat()
        goal = {
            **current,
            "revision": expected_revision + 1,
            "continuation": {
                "mode": mode,
                "max_auto_followups": max_auto_followups,
                "auto_followups_started": 0,
                "no_progress_streak": 0,
                "last_progress_fingerprint": "",
                "stop_reason": None,
            },
            "updated_at": now,
        }
        try:
            stored = self.journal.append_thread_goal(
                event_type="thread_goal_policy_updated",
                expected_revision=expected_revision,
                goal=goal,
            )
        except SessionRunLeaseConflictError as exc:
            raise ThreadGoalBusyError("Thread Goal cannot change while a run is active") from exc
        return dict(stored.payload["goal"])

    def evaluate_post_turn(
        self,
        *,
        request: ThreadGoalEvaluationRequest,
        decision: GoalEvaluationDecision,
        terminal_status: str,
    ) -> ThreadGoalPostTurn:
        """Persist one guarded evaluation and reserve at most one safe continuation."""
        current = self._current_at(request.goal_revision)
        continuation = _continuation(current)
        now = datetime.now(UTC).isoformat()
        status: GoalEvaluationStatus = decision.status
        blocker: GoalBlocker | None = decision.blocker
        next_action = _bounded_text(
            decision.next_action,
            field="next_action",
            maximum=1_000,
        )
        if terminal_status != "completed":
            status = "blocked"
            blocker = "run_failed" if terminal_status == "error" else "external_wait"
            next_action = "上一轮未正常完成；请检查运行状态后由用户决定是否继续"

        previous_fingerprint = str(continuation["last_progress_fingerprint"])
        current_fingerprint = request.progress_fingerprint
        if current_fingerprint and current_fingerprint != previous_fingerprint:
            no_progress_streak = 0
        else:
            no_progress_streak = int(continuation["no_progress_streak"]) + 1
        attempts = int(continuation["auto_followups_started"])
        max_attempts = int(continuation["max_auto_followups"])
        mode = str(continuation["mode"])
        stop_reason: str | None = None
        schedule = (
            terminal_status == "completed"
            and mode == "bounded_auto"
            and status == "continue"
            and blocker == "goal_not_met_yet"
        )
        if schedule and no_progress_streak >= 2:
            schedule = False
            status = "blocked"
            blocker = "no_progress"
            next_action = "连续两轮没有新增可验证证据；请用户调整目标或提供输入"
            stop_reason = "no_progress"
        elif schedule and attempts >= max_attempts:
            schedule = False
            stop_reason = "max_auto_followups"
            next_action = "自动跟进次数已用完；请用户检查证据并决定是否继续"
        elif mode == "manual":
            stop_reason = "manual"
        elif status != "continue" or blocker != "goal_not_met_yet":
            stop_reason = str(blocker or status)

        next_revision = request.goal_revision + 1
        updated_continuation = {
            **continuation,
            "auto_followups_started": attempts + (1 if schedule else 0),
            "no_progress_streak": no_progress_streak,
            "last_progress_fingerprint": current_fingerprint or previous_fingerprint,
            "stop_reason": stop_reason,
        }
        evaluation = {
            "status": status,
            "blocker": blocker,
            "evidence_refs": list(decision.evidence_refs),
            "next_action": next_action,
            "source_run_id": request.source_run_id,
            "evaluated_at": now,
            "criteria": [
                {
                    "index": item.index,
                    "status": item.status,
                    "evidence_refs": list(item.evidence_refs),
                }
                for item in decision.criteria
            ],
        }
        goal = {
            **current,
            "revision": next_revision,
            "status": (
                "satisfied"
                if status == "satisfied"
                else "blocked"
                if status == "blocked"
                else "active"
            ),
            "evaluation": evaluation,
            "continuation": updated_continuation,
            "updated_at": now,
        }
        reservation = None
        if schedule:
            run_id = _followup_run_id(
                self.journal.session_id,
                request.source_run_id,
                next_revision,
            )
            reservation = {
                "reservation_id": f"followup-{run_id[9:]}",
                "run_id": run_id,
                "source_run_id": request.source_run_id,
                "goal_id": str(current["goal_id"]),
                "goal_revision": next_revision,
                "prompt": _continuation_prompt(goal),
                "created_at": now,
            }
        try:
            evaluated, scheduled = self.journal.append_thread_goal_post_turn(
                source_run_id=request.source_run_id,
                expected_revision=request.goal_revision,
                goal=goal,
                reservation=reservation,
            )
        except SessionRunLeaseConflictError as exc:
            raise ThreadGoalBusyError("Thread Goal cannot evaluate while a run is active") from exc
        stored_goal = dict(evaluated.payload["goal"])
        stored_reservation = (
            {
                key: value
                for key, value in scheduled.payload.items()
                if key not in {"type", "version"}
            }
            if scheduled is not None
            else None
        )
        return ThreadGoalPostTurn(goal=stored_goal, reservation=stored_reservation)

    def clear(self, *, expected_revision: int) -> None:
        self._require_idle()
        if self.get() is None:
            raise ThreadGoalNotFoundError("Thread Goal is not configured")
        try:
            self.journal.clear_thread_goal(expected_revision=expected_revision)
        except SessionRunLeaseConflictError as exc:
            raise ThreadGoalBusyError("Thread Goal cannot change while a run is active") from exc

    def evaluate(self, *, expected_revision: int) -> dict[str, Any]:
        self._require_idle()
        current = self._current_at(expected_revision)
        terminal = self.journal.latest_terminal_event()
        now = datetime.now(UTC).isoformat()
        if terminal is None:
            status: GoalEvaluationStatus = "continue"
            blocker: GoalBlocker = "goal_not_met_yet"
            next_action = "开始执行当前目标并收集可验证证据"
            source_run_id = None
            refs: list[str] = []
        elif terminal.status == "completed":
            status = "continue"
            blocker = "goal_not_met_yet"
            next_action = "继续目标并补齐完成标准所需的可验证证据"
            source_run_id = terminal.run_id
            refs = _evidence_refs(self.journal.events_for_run(terminal.run_id))
        else:
            status = "blocked"
            blocker = "run_failed"
            next_action = "上一轮运行失败；检查错误证据后重新执行目标"
            source_run_id = terminal.run_id
            refs = _evidence_refs(self.journal.events_for_run(terminal.run_id))
        evaluation = {
            "status": status,
            "blocker": blocker,
            "evidence_refs": refs,
            "next_action": next_action,
            "source_run_id": source_run_id,
            "evaluated_at": now,
        }
        goal = {
            **current,
            "revision": expected_revision + 1,
            "status": "blocked" if status == "blocked" else "active",
            "evaluation": evaluation,
            "updated_at": now,
        }
        try:
            stored = self.journal.append_thread_goal(
                event_type="thread_goal_evaluated",
                expected_revision=expected_revision,
                goal=goal,
            )
        except SessionRunLeaseConflictError as exc:
            raise ThreadGoalBusyError("Thread Goal cannot change while a run is active") from exc
        return dict(stored.payload["goal"])

    def prepare_continue(self, *, expected_revision: int) -> ThreadGoalContinue:
        self._require_idle()
        current = self._current_at(expected_revision)
        criteria = "\n".join(
            f"- {item}" for item in current.get("completion_criteria", []) if str(item).strip()
        )
        evaluation = current.get("evaluation")
        next_action = (
            str(evaluation.get("next_action", "")).strip()
            if isinstance(evaluation, Mapping)
            else ""
        )
        prompt = (
            "继续当前 Thread Goal。只推进当前目标，不创建新目标。\n\n"
            f"目标：{current['description']}\n"
            f"完成标准：\n{criteria or '- 尚未补充'}\n"
            f"下一步：{next_action or '继续收集可验证证据'}\n\n"
            "请基于已有 timeline 和证据继续；需要外部输入或能力不可用时明确阻塞原因。"
        )
        return ThreadGoalContinue(
            goal_id=str(current["goal_id"]),
            goal_revision=int(current["revision"]),
            prompt=prompt,
        )

    def _current_at(self, expected_revision: int) -> dict[str, Any]:
        current = self.get()
        if current is None:
            raise ThreadGoalNotFoundError("Thread Goal is not configured")
        actual = int(current.get("revision", 0))
        if actual != expected_revision:
            raise SessionThreadGoalConflictError(actual)
        return current

    def _require_idle(self) -> None:
        if self.journal.active_run_id() is not None:
            raise ThreadGoalBusyError("Thread Goal cannot change while a run is active")


def _bounded_text(value: object, *, field: str, maximum: int) -> str:
    text = re.sub(r"\s+", " ", str(value)).strip()
    if not text:
        raise ValueError(f"{field} must not be empty")
    if len(text) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters")
    return text


def _criteria(values: Iterable[str]) -> list[str]:
    items = [_bounded_text(value, field="completion criterion", maximum=500) for value in values]
    if not 1 <= len(items) <= 8:
        raise ValueError("completion_criteria must contain between 1 and 8 items")
    return items


def _evidence_refs(events: Iterable[object]) -> list[str]:
    refs: list[str] = []

    def visit(value: object, key: str = "") -> None:
        if len(refs) >= 32:
            return
        if isinstance(value, Mapping):
            for child_key, child in value.items():
                visit(child, str(child_key))
            return
        if isinstance(value, list | tuple):
            for child in value:
                visit(child, key)
            return
        if key in _REFERENCE_KEYS and isinstance(value, str):
            candidate = value.strip()[:512]
            if candidate and candidate not in refs:
                refs.append(candidate)

    for event in events:
        visit(getattr(event, "payload", None))
    return refs


def _default_continuation() -> dict[str, Any]:
    return {
        "mode": "manual",
        "max_auto_followups": 1,
        "auto_followups_started": 0,
        "no_progress_streak": 0,
        "last_progress_fingerprint": "",
        "stop_reason": None,
    }


def _continuation(goal: Mapping[str, Any]) -> dict[str, Any]:
    default = _default_continuation()
    value = goal.get("continuation")
    if not isinstance(value, Mapping):
        return default
    mode = str(value.get("mode", "manual"))
    default["mode"] = mode if mode in {"manual", "bounded_auto"} else "manual"
    maximum = value.get("max_auto_followups", 1)
    default["max_auto_followups"] = (
        maximum if type(maximum) is int and 1 <= maximum <= 4 else 1
    )
    for key in ("auto_followups_started", "no_progress_streak"):
        item = value.get(key, 0)
        default[key] = item if type(item) is int and item >= 0 else 0
    default["last_progress_fingerprint"] = str(
        value.get("last_progress_fingerprint", "")
    )[:128]
    stop_reason = value.get("stop_reason")
    default["stop_reason"] = str(stop_reason)[:64] if stop_reason else None
    return default


def _reset_continuation(current: Mapping[str, Any] | None) -> dict[str, Any]:
    continuation = _continuation(current or {})
    return {
        **continuation,
        "auto_followups_started": 0,
        "no_progress_streak": 0,
        "last_progress_fingerprint": "",
        "stop_reason": None,
    }


def _normalized_goal(goal: Mapping[str, Any]) -> dict[str, Any]:
    return {**goal, "continuation": _continuation(goal)}


def _followup_run_id(session_id: str, source_run_id: str, revision: int) -> str:
    value = f"sage:goal-followup:{session_id}:{source_run_id}:{revision}"
    return f"run_goal_{uuid5(NAMESPACE_URL, value).hex[:20]}"


def _continuation_prompt(goal: Mapping[str, Any]) -> str:
    criteria = "\n".join(
        f"- {item}" for item in goal.get("completion_criteria", []) if str(item).strip()
    )
    evaluation = goal.get("evaluation")
    next_action = (
        str(evaluation.get("next_action", "")).strip()
        if isinstance(evaluation, Mapping)
        else ""
    )
    return (
        "这是用户已显式启用的受限自动跟进。继续同一个 Thread Goal，不创建新目标。\n\n"
        f"目标：{goal['description']}\n"
        f"完成标准：\n{criteria or '- 尚未补充'}\n"
        f"下一步：{next_action or '继续收集可验证证据'}\n\n"
        "只使用当前可用能力推进；需要审批、用户输入或外部能力不可用时立即停止。"
    )


__all__ = [
    "GoalBlocker",
    "GoalContinuationMode",
    "GoalEvaluationStatus",
    "ThreadGoalBusyError",
    "ThreadGoalContinue",
    "ThreadGoalError",
    "ThreadGoalNotFoundError",
    "ThreadGoalPostTurn",
    "ThreadGoalService",
]
