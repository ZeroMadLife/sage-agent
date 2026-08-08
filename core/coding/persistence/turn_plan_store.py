"""不可变 Turn Context Plan 的持久化逻辑存储。"""

from __future__ import annotations

from pathlib import Path

from core.coding.persistence.session_event_journal import SessionEventJournal
from core.harness.turn_context_plan import TurnContextPlan, TurnContextPlanValidationError


class TurnPlanStoreError(RuntimeError):
    """Plan 持久化基础错误。"""


class TurnPlanConflictError(TurnPlanStoreError):
    """同一 run 已经存在不同的不可变 Plan。"""


class TurnPlanCorruptionError(TurnPlanStoreError):
    """存储中的 Plan 不再匹配 canonical 身份或 hash。"""


class TurnPlanStore:
    """暴露 Plan 语义，但不向调用方暴露 Timeline SQLite 连接。"""

    def __init__(self, storage_root: Path, session_id: str) -> None:
        """绑定 session 作用域，并复用已有 Journal 的安全 SQLite 根。"""
        self._journal = SessionEventJournal(storage_root, session_id)
        self.session_id = session_id
        self.path = self._journal.path

    def put_if_absent(self, plan: TurnContextPlan) -> TurnContextPlan:
        """幂等保存 Plan；同 run 不允许不同 hash 覆盖。"""
        if plan.session_id != self.session_id:
            raise TurnPlanConflictError("turn context plan session scope mismatch")
        stored = self._journal.put_turn_context_plan(
            plan_id=plan.plan_id,
            run_id=plan.run_id,
            version=1,
            plan_hash=plan.plan_hash,
            owner_fingerprint=plan.owner_fingerprint,
            workspace_id=plan.workspace_id,
            surface=plan.surface,
            checkpoint_thread_id=str(plan.to_payload()["resume"].get("checkpoint_thread_id", "")),
            checkpoint_namespace=str(plan.to_payload()["resume"].get("checkpoint_namespace", "")),
            payload_json=plan.payload_json,
            created_at=plan.created_at,
        )
        if stored["plan_hash"] != plan.plan_hash:
            raise TurnPlanConflictError(f"turn context plan conflict for run {plan.run_id}")
        return self._decode(stored)

    def load_for_run(self, run_id: str) -> TurnContextPlan | None:
        """按 session/run 读取并重新验证完整 Plan。"""
        stored = self._journal.load_turn_context_plan(run_id)
        if stored is None:
            return None
        return self._decode(stored)

    def _decode(self, stored: dict[str, object]) -> TurnContextPlan:
        """把数据库行解码为完整 Plan；任何篡改都转成统一损坏错误。"""
        try:
            return TurnContextPlan.from_stored(
                plan_id=_stored_text(stored, "plan_id"),
                session_id=self.session_id,
                run_id=_stored_text(stored, "run_id"),
                version=_stored_int(stored, "version"),
                plan_hash=_stored_text(stored, "plan_hash"),
                owner_fingerprint=_stored_text(stored, "owner_fingerprint"),
                workspace_id=_stored_text(stored, "workspace_id"),
                surface=_stored_text(stored, "surface"),
                checkpoint_thread_id=_stored_text(stored, "checkpoint_thread_id"),
                checkpoint_namespace=_stored_text(stored, "checkpoint_namespace"),
                payload_json=_stored_text(stored, "payload_json"),
                created_at=_stored_text(stored, "created_at"),
            )
        except (KeyError, TypeError, ValueError, TurnContextPlanValidationError) as exc:
            raise TurnPlanCorruptionError(str(exc)) from exc


def _stored_text(stored: dict[str, object], field: str) -> str:
    value = stored[field]
    if not isinstance(value, str):
        raise TypeError(f"stored turn context plan {field} must be text")
    return value


def _stored_int(stored: dict[str, object], field: str) -> int:
    value = stored[field]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"stored turn context plan {field} must be an integer")
    return value


__all__ = [
    "TurnPlanConflictError",
    "TurnPlanCorruptionError",
    "TurnPlanStore",
    "TurnPlanStoreError",
]
