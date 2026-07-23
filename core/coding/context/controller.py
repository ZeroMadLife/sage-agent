"""Turn-boundary context pressure control without canonical-history mutation."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from core.coding.context.budget import ContextPolicy, ContextUsage, TokenCounter
from core.coding.context.compact import CompactManager
from core.coding.context.projection import ContextProjector
from core.coding.context.summary import CompactionCheckpoint, CompactionResult
from core.coding.engine.events import (
    ContextCompactionCompletedEvent,
    ContextCompactionFailedEvent,
    ContextCompactionStartedEvent,
    ContextUsageUpdatedEvent,
    RunEventBase,
)


class ContextBusyError(RuntimeError):
    """Manual compaction was requested while a run owns the session."""


class ContextLifecycleSinkError(RuntimeError):
    """A lifecycle event could not be durably accepted by its sink."""


Renderer = Callable[[list[dict[str, Any]], str], str]
HistoryProvider = Callable[[], list[dict[str, Any]]]
LifecycleSink = Callable[[RunEventBase, CompactionResult | None], Awaitable[None]]


@dataclass(frozen=True)
class PreparedContext:
    projected_history: list[dict[str, Any]]
    usage: ContextUsage
    allow_model_request: bool
    compaction_result: CompactionResult | None = None
    events: tuple[RunEventBase, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "projected_history", deepcopy(self.projected_history))

    @classmethod
    def create(
        cls,
        *,
        projected_history: list[dict[str, Any]],
        usage: ContextUsage,
        allow_model_request: bool,
        compaction_result: CompactionResult | None = None,
        events: tuple[RunEventBase, ...] = (),
    ) -> PreparedContext:
        return cls(
            projected_history=projected_history,
            usage=usage,
            allow_model_request=allow_model_request,
            compaction_result=compaction_result,
            events=events,
        )


class ContextController:
    """Prepare bounded model views and compact only at user-turn boundaries."""

    def __init__(
        self,
        *,
        session_id: str,
        policy: ContextPolicy,
        counter: TokenCounter,
        projector: ContextProjector,
        compactor: CompactManager,
        renderer: Renderer,
        history_provider: HistoryProvider | None = None,
        active_run_id: Callable[[], str | None] | None = None,
        lifecycle_sink: LifecycleSink | None = None,
    ) -> None:
        if not session_id.strip():
            raise ValueError("session_id must be non-empty")
        self.session_id = session_id
        self.policy = policy
        self.counter = counter
        self.projector = projector
        self.compactor = compactor
        self.renderer = renderer
        self.history_provider = history_provider
        self.active_run_id = active_run_id or (lambda: None)
        self.lifecycle_sink = lifecycle_sink
        self.current_user_message = ""
        self.last_usage: ContextUsage | None = None

    async def on_turn_start(
        self,
        history: list[dict[str, Any]],
        user_message: str,
        run_id: str,
        previous_checkpoint: CompactionCheckpoint | None = None,
        transcript_range: tuple[int, int] | None = None,
        current_message_id: str | None = None,
    ) -> PreparedContext:
        """Prepare prior history; current content is supplied only to ``renderer``.

        Callers must exclude the current user item. If it has already been
        archived, ``current_message_id`` removes exactly that stable identity.
        """
        self.current_user_message = user_message
        original = self._prior_history(history, current_message_id)
        projected, usage = self._project_and_count(original, user_message)
        result: CompactionResult | None = None
        events: list[RunEventBase] = []
        if usage.level in {"compact", "high", "emergency"}:
            compaction_id = f"compact-{uuid4().hex}"
            started = ContextCompactionStartedEvent(
                session_id=self.session_id,
                run_id=run_id,
                compaction_id=compaction_id,
                trigger="auto",
                before_tokens=usage.used_tokens,
            )
            events.append(started)
            await self._deliver_lifecycle(started, None)
            result = await self.compactor.compact(
                history=original,
                session_id=self.session_id,
                trigger="auto",
                focus=user_message,
                previous_checkpoint=previous_checkpoint,
                transcript_range=transcript_range,
                compaction_id=compaction_id,
            )
            if result.applied:
                projected, usage = self._project_and_count(result.projected_history, user_message)
                terminal: RunEventBase = ContextCompactionCompletedEvent(
                    session_id=self.session_id,
                    run_id=run_id,
                    compaction_id=compaction_id,
                    before_tokens=result.before_tokens,
                    after_tokens=result.after_tokens,
                    archived_items=result.archived_items,
                )
            else:
                projected, usage = self._project_and_count(original, user_message)
                terminal = ContextCompactionFailedEvent(
                    session_id=self.session_id,
                    run_id=run_id,
                    compaction_id=compaction_id,
                    reason=result.reason or "compaction_failed",
                    preserved_original=True,
                    retryable=result.retryable,
                )
            events.append(terminal)
            await self._deliver_lifecycle(terminal, result)
        self.last_usage = usage
        events.append(self._usage_event(usage, run_id))
        return PreparedContext.create(
            projected_history=projected,
            usage=usage,
            allow_model_request=usage.level != "emergency",
            compaction_result=result,
            events=tuple(events),
        )

    def before_model_request(
        self,
        history: list[dict[str, Any]],
        *,
        user_message: str | None = None,
        run_id: str = "",
        current_message_id: str | None = None,
    ) -> PreparedContext:
        current = self.current_user_message if user_message is None else user_message
        prior = self._prior_history(history, current_message_id)
        projected, usage = self._project_and_count(prior, current)
        self.last_usage = usage
        return PreparedContext.create(
            projected_history=projected,
            usage=usage,
            allow_model_request=usage.level != "emergency",
            events=(self._usage_event(usage, run_id),),
        )

    async def manual_compact(
        self,
        focus: str = "",
        *,
        history: list[dict[str, Any]] | None = None,
        previous_checkpoint: CompactionCheckpoint | None = None,
        transcript_range: tuple[int, int] | None = None,
        compaction_id: str | None = None,
    ) -> CompactionResult:
        if self.active_run_id():
            raise ContextBusyError("active run")
        source = history
        if source is None:
            if self.history_provider is None:
                raise ValueError("history is not configured")
            source = self.history_provider()
        return await self.compactor.compact(
            history=deepcopy(source),
            session_id=self.session_id,
            trigger="manual",
            focus=focus,
            previous_checkpoint=previous_checkpoint,
            transcript_range=transcript_range,
            compaction_id=compaction_id,
        )

    def _project_and_count(
        self, history: list[dict[str, Any]], user_message: str
    ) -> tuple[list[dict[str, Any]], ContextUsage]:
        initial = self._count(history, user_message)
        first = self.projector.project(history, initial.level)
        first_usage = self._count(first, user_message)
        second = self.projector.project(history, first_usage.level)
        return second, self._count(second, user_message)

    def _count(self, history: list[dict[str, Any]], user_message: str) -> ContextUsage:
        rendered = self.renderer(deepcopy(history), user_message)
        count = self.counter.count(rendered)
        return self.policy.usage(count.tokens, count.estimated)

    def _usage_event(self, usage: ContextUsage, run_id: str) -> ContextUsageUpdatedEvent:
        return ContextUsageUpdatedEvent(
            session_id=self.session_id,
            run_id=run_id,
            used_tokens=usage.used_tokens,
            model_limit_tokens=self.policy.context_window_tokens,
            output_reserve_tokens=self.policy.output_reserve_tokens,
            effective_limit_tokens=usage.effective_limit_tokens,
            usage_ratio=usage.usage_ratio,
            level=usage.level,
            estimated=usage.estimated,
            compactable=True,
        )

    async def _deliver_lifecycle(
        self, event: RunEventBase, result: CompactionResult | None
    ) -> None:
        if self.lifecycle_sink is None:
            return
        try:
            await self.lifecycle_sink(event, result)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ContextLifecycleSinkError("context lifecycle sink failed") from None

    @staticmethod
    def _prior_history(
        history: list[dict[str, Any]], current_message_id: str | None
    ) -> list[dict[str, Any]]:
        copied = deepcopy(history)
        if current_message_id is None:
            return copied
        matches = [
            index
            for index, item in enumerate(copied)
            if item.get("message_id") == current_message_id
        ]
        if len(matches) > 1:
            raise ValueError("current_message_id is not unique in history")
        if matches:
            copied.pop(matches[0])
        return copied
