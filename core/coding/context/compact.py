"""Safe structured history compaction for coding sessions."""

from __future__ import annotations

import asyncio
import hashlib
import json
import posixpath
import re
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

from pydantic import ValidationError

from core.coding.context.budget import ContextPolicy, TokenCounter
from core.coding.context.summary import (
    CompactionCheckpoint,
    CompactionResult,
    CompactionSummary,
)
from core.coding.context.workspace import now


class Summarizer(Protocol):
    async def summarize(
        self,
        *,
        archived_history: list[dict[str, Any]],
        previous_summary: CompactionSummary | None,
        focus: str,
        max_tokens: int,
        source_transcript_range: tuple[int, int],
        repair_feedback: str | None,
    ) -> CompactionSummary | Mapping[str, Any]: ...


class CacheInvalidator(Protocol):
    def invalidate_system_prompt(self) -> None: ...


class CheckpointVerifier(Protocol):
    def __call__(self, session_id: str, checkpoint: CompactionCheckpoint) -> bool: ...


class CompactionBusyError(RuntimeError):
    """A manual compaction was requested while the session was already compacting."""


@dataclass(frozen=True)
class CompactionPolicy:
    min_recent_turns: int = 3
    max_recent_turns: int = 12
    tail_budget_ratio: float = 0.20
    minimum_savings_ratio: float = 0.10
    ineffective_limit: int = 2
    cooldown_seconds: float = 60.0

    def __post_init__(self) -> None:
        if self.min_recent_turns < 1:
            raise ValueError("min_recent_turns must be at least one")
        if self.max_recent_turns < self.min_recent_turns:
            raise ValueError("max_recent_turns must not be less than min_recent_turns")
        if not 0.0 < self.tail_budget_ratio <= 1.0:
            raise ValueError("tail_budget_ratio must be within (0, 1]")
        if not 0.0 <= self.minimum_savings_ratio <= 1.0:
            raise ValueError("minimum_savings_ratio must be within [0, 1]")
        if self.ineffective_limit < 1:
            raise ValueError("ineffective_limit must be at least one")
        if self.cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be non-negative")


@dataclass
class _SessionState:
    ineffective_results: int = 0
    auto_circuit_open: bool = False
    cooldown_until: float | None = None
    in_progress: bool = False


class CompactManager:
    def __init__(
        self,
        *,
        summarizer: Summarizer,
        policy: ContextPolicy,
        counter: TokenCounter | None = None,
        compaction_policy: CompactionPolicy | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        checkpoint_verifier: CheckpointVerifier | None = None,
    ) -> None:
        self.summarizer = summarizer
        self.policy = policy
        self.counter = counter or TokenCounter()
        self.compaction_policy = compaction_policy or CompactionPolicy()
        self.monotonic = monotonic
        self.checkpoint_verifier = checkpoint_verifier
        self._states: dict[str, _SessionState] = {}

    async def compact(
        self,
        history: list[dict[str, Any]],
        *,
        session_id: str,
        trigger: str = "manual",
        focus: str = "",
        previous_checkpoint: CompactionCheckpoint | None = None,
        transcript_range: tuple[int, int] | None = None,
        context_manager: CacheInvalidator | None = None,
    ) -> CompactionResult:
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be non-empty")
        compaction_id = f"compact-{uuid4().hex}"
        original = deepcopy(history)
        before_tokens = self._safe_count(original)
        state = self._states.setdefault(session_id, _SessionState())
        if state.in_progress:
            if trigger == "manual":
                raise CompactionBusyError("compaction already in progress")
            return self._unchanged(
                original,
                previous_checkpoint,
                before_tokens,
                "compaction_busy",
                compaction_id,
                trigger,
                retryable=True,
            )
        state.in_progress = True
        try:
            return await self._compact_locked(
                original=original,
                before_tokens=before_tokens,
                compaction_id=compaction_id,
                trigger=trigger,
                focus=focus,
                previous_checkpoint=previous_checkpoint,
                transcript_range=transcript_range,
                context_manager=context_manager,
                session_id=session_id,
                state=state,
            )
        finally:
            state.in_progress = False

    async def _compact_locked(
        self,
        *,
        original: list[dict[str, Any]],
        before_tokens: int,
        compaction_id: str,
        trigger: str,
        focus: str,
        previous_checkpoint: CompactionCheckpoint | None,
        transcript_range: tuple[int, int] | None,
        context_manager: CacheInvalidator | None,
        session_id: str,
        state: _SessionState,
    ) -> CompactionResult:
        current_time = self.monotonic()
        if trigger == "auto":
            if state.cooldown_until is not None and current_time < state.cooldown_until:
                return self._unchanged(
                    original,
                    previous_checkpoint,
                    before_tokens,
                    "cooldown_active",
                    compaction_id,
                    trigger,
                    retryable=True,
                    cooldown_until=state.cooldown_until,
                )
            if state.auto_circuit_open:
                return self._unchanged(
                    original,
                    previous_checkpoint,
                    before_tokens,
                    "auto_compaction_circuit_open",
                    compaction_id,
                    trigger,
                )

        try:
            prefix, archived, tail, canonical, has_old_summary = self._split_history(original)
            previous_summary = self._validated_previous_summary(previous_checkpoint, session_id)
            if has_old_summary and previous_checkpoint is None:
                raise ValueError("invalid_previous_checkpoint")
            prefix_hash = self._prefix_hash(prefix)
            if previous_checkpoint is not None and prefix_hash != previous_checkpoint.prefix_hash:
                raise ValueError("prefix_changed")
            if not archived:
                return self._unchanged(
                    original,
                    previous_checkpoint,
                    before_tokens,
                    "insufficient_history",
                    compaction_id,
                    trigger,
                )
            archived_range = self._resolve_transcript_range(
                archived,
                canonical,
                transcript_range,
                previous_checkpoint,
            )
            evidence_hash = self._evidence_hash(archived)
            max_tokens = max(
                1,
                min(int(self.policy.context_window_tokens * 0.05), 12_000),
            )
            summary, failure_reason = await self._produce_summary(
                archived=archived,
                previous_summary=previous_summary,
                focus=focus,
                max_tokens=max_tokens,
                archived_range=archived_range,
            )
            if summary is None:
                cooldown_until = self._start_cooldown(state)
                return self._unchanged(
                    original,
                    previous_checkpoint,
                    before_tokens,
                    failure_reason,
                    compaction_id,
                    trigger,
                    retryable=True,
                    cooldown_until=cooldown_until,
                )

            rendered_summary = summary.render_for_prompt()
            summary_item = {
                "role": "system",
                "kind": "compact_summary",
                "content": rendered_summary,
                "created_at": now(),
            }
            projected = [*deepcopy(prefix), summary_item, *deepcopy(tail)]
            after_tokens = self._count_history(projected)
            savings_ratio = (before_tokens - after_tokens) / before_tokens
            if savings_ratio < self.compaction_policy.minimum_savings_ratio:
                state.ineffective_results += 1
                if state.ineffective_results >= self.compaction_policy.ineffective_limit:
                    state.auto_circuit_open = True
                return CompactionResult(
                    applied=False,
                    projected_history=deepcopy(original),
                    checkpoint=previous_checkpoint,
                    before_tokens=before_tokens,
                    after_tokens=after_tokens,
                    archived_items=0,
                    reason="ineffective_summary",
                    compaction_id=compaction_id,
                    trigger=trigger,
                )

            previous_hash = previous_checkpoint.summary_hash if previous_checkpoint else ""
            summary_hash = self._summary_hash(previous_hash, evidence_hash, rendered_summary)
            checkpoint = CompactionCheckpoint(
                compaction_id=compaction_id,
                transcript_start=archived_range[0],
                transcript_end=archived_range[1],
                summary=summary.model_copy(deep=True),
                summary_hash=summary_hash,
                previous_summary_hash=previous_hash,
                evidence_hash=evidence_hash,
                prefix_hash=prefix_hash,
            )
            state.ineffective_results = 0
            state.auto_circuit_open = False
            state.cooldown_until = None
            if context_manager is not None:
                with suppress(BaseException):
                    context_manager.invalidate_system_prompt()
            return CompactionResult(
                applied=True,
                projected_history=projected,
                checkpoint=checkpoint,
                before_tokens=before_tokens,
                after_tokens=after_tokens,
                archived_items=len(archived),
                compaction_id=compaction_id,
                trigger=trigger,
            )
        except asyncio.CancelledError:
            raise
        except ValueError as exc:
            known_reason = str(exc) in {
                "invalid_transcript_range",
                "non_contiguous_transcript_range",
                "invalid_previous_checkpoint",
                "missing_transcript_sequence",
                "prefix_changed",
                "unsupported_control_layout",
            }
            reason = str(exc) if known_reason else "compaction_failed"
            value_error_cooldown: float | None = (
                self._start_cooldown(state) if trigger == "auto" and not known_reason else None
            )
            return self._unchanged(
                original,
                previous_checkpoint,
                before_tokens,
                reason,
                compaction_id,
                trigger,
                retryable=trigger == "auto" and not known_reason,
                cooldown_until=value_error_cooldown,
            )
        except Exception:
            exception_cooldown: float | None = (
                self._start_cooldown(state) if trigger == "auto" else None
            )
            return self._unchanged(
                original,
                previous_checkpoint,
                before_tokens,
                "compaction_failed",
                compaction_id,
                trigger,
                retryable=trigger == "auto",
                cooldown_until=exception_cooldown,
            )

    async def _produce_summary(
        self,
        *,
        archived: list[dict[str, Any]],
        previous_summary: CompactionSummary | None,
        focus: str,
        max_tokens: int,
        archived_range: tuple[int, int],
    ) -> tuple[CompactionSummary | None, str]:
        repair_feedback: str | None = None
        for attempt in range(2):
            try:
                raw_summary = await self.summarizer.summarize(
                    archived_history=deepcopy(archived),
                    previous_summary=deepcopy(previous_summary),
                    focus=focus,
                    max_tokens=max_tokens,
                    source_transcript_range=archived_range,
                    repair_feedback=repair_feedback,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                return None, "summarizer_failed"
            try:
                summary = CompactionSummary.model_validate(raw_summary)
            except ValidationError:
                if attempt == 0:
                    repair_feedback = "summary_schema_invalid"
                    continue
                return None, "summary_schema_invalid"
            quality_feedback = self._validate_summary(summary, archived, archived_range)
            if not quality_feedback:
                return summary, ""
            if attempt == 0:
                repair_feedback = quality_feedback
                continue
            return None, "summary_quality_invalid"
        return None, "summary_schema_invalid"

    def _split_history(
        self, history: list[dict[str, Any]]
    ) -> tuple[
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        bool,
    ]:
        prefix: list[dict[str, Any]] = []
        active: list[dict[str, Any]] = []
        seen_user = False
        has_old_summary = False
        supported_roles = {"user", "assistant", "tool"}
        for item in history:
            role = item.get("role")
            is_old_summary = role == "system" and item.get("kind") == "compact_summary"
            if not seen_user:
                if is_old_summary:
                    has_old_summary = True
                elif role == "user":
                    seen_user = True
                    active.append(item)
                elif role == "system" or role not in supported_roles:
                    prefix.append(item)
                else:
                    raise ValueError("unsupported_control_layout")
                continue
            if role == "system" or role not in supported_roles:
                raise ValueError("unsupported_control_layout")
            active.append(item)

        turns: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] | None = None
        for item in active:
            role = item.get("role")
            if role == "user":
                if current is not None:
                    turns.append(current)
                current = [item]
            else:
                if current is None:
                    raise ValueError("unsupported_control_layout")
                current.append(item)
        if current is not None:
            turns.append(current)
        keep = min(len(turns), self.compaction_policy.min_recent_turns)
        max_keep = min(len(turns), self.compaction_policy.max_recent_turns)
        tail_budget = int(
            self.policy.effective_limit_tokens
            * self.policy.compact_ratio
            * self.compaction_policy.tail_budget_ratio
        )
        while keep < max_keep:
            candidate = [item for turn in turns[-(keep + 1) :] for item in turn]
            if self._count_history(candidate) > tail_budget:
                break
            keep += 1
        archived_turns = turns[:-keep] if keep else turns
        tail_turns = turns[-keep:] if keep else []
        return (
            prefix,
            [item for turn in archived_turns for item in turn],
            [item for turn in tail_turns for item in turn],
            active,
            has_old_summary,
        )

    @staticmethod
    def _resolve_transcript_range(
        archived: list[dict[str, Any]],
        canonical: list[dict[str, Any]],
        supplied: tuple[int, int] | None,
        previous: CompactionCheckpoint | None,
    ) -> tuple[int, int]:
        positions = {id(item): index for index, item in enumerate(canonical)}
        archived_positions = [positions[id(item)] for item in archived]
        if archived_positions != list(range(archived_positions[0], archived_positions[-1] + 1)):
            raise ValueError("non_contiguous_transcript_range")
        if previous is not None:
            sequenced_items = canonical
            if not all(
                isinstance(item.get("sequence"), int) and not isinstance(item.get("sequence"), bool)
                for item in sequenced_items
            ):
                raise ValueError("missing_transcript_sequence")
            if any(int(item["sequence"]) < 0 for item in sequenced_items):
                raise ValueError("invalid_transcript_range")
        if supplied is not None:
            if (
                len(supplied) != 2
                or isinstance(supplied[0], bool)
                or isinstance(supplied[1], bool)
                or not isinstance(supplied[0], int)
                or not isinstance(supplied[1], int)
                or supplied[0] < 0
                or supplied[1] < supplied[0]
                or supplied[1] - supplied[0] + 1 != len(canonical)
            ):
                raise ValueError("invalid_transcript_range")
            source_start = supplied[0]
            mapped = [source_start + index for index in range(len(canonical))]
            for index, item in enumerate(canonical):
                sequence = item.get("sequence")
                if sequence is not None and (
                    isinstance(sequence, bool)
                    or not isinstance(sequence, int)
                    or sequence != mapped[index]
                ):
                    raise ValueError("non_contiguous_transcript_range")
        else:
            sequences = [item.get("sequence") for item in canonical]
            has_real_sequence = any(value is not None for value in sequences)
            if has_real_sequence:
                if not all(
                    isinstance(value, int) and not isinstance(value, bool) for value in sequences
                ):
                    raise ValueError("non_contiguous_transcript_range")
                numeric = [
                    value
                    for value in sequences
                    if isinstance(value, int) and not isinstance(value, bool)
                ]
                if numeric[0] < 0:
                    raise ValueError("invalid_transcript_range")
                if numeric != list(range(numeric[0], numeric[0] + len(numeric))):
                    raise ValueError("non_contiguous_transcript_range")
                source_start = numeric[0]
                mapped = numeric
            else:
                source_start = previous.transcript_end + 1 if previous is not None else 0
                mapped = [source_start + index for index in range(len(canonical))]
        if previous is not None and source_start != previous.transcript_end + 1:
            raise ValueError("non_contiguous_transcript_range")
        resolved = (mapped[archived_positions[0]], mapped[archived_positions[-1]])
        if resolved[1] - resolved[0] + 1 != len(archived):
            raise ValueError("non_contiguous_transcript_range")
        return resolved

    def _validated_previous_summary(
        self, checkpoint: CompactionCheckpoint | None, session_id: str
    ) -> CompactionSummary | None:
        if checkpoint is None:
            return None
        if self.checkpoint_verifier is None:
            raise ValueError("invalid_previous_checkpoint")
        try:
            trusted = self.checkpoint_verifier(session_id, checkpoint)
        except Exception:
            trusted = False
        if not trusted:
            raise ValueError("invalid_previous_checkpoint")
        if (
            checkpoint.transcript_start < 0
            or checkpoint.transcript_end < checkpoint.transcript_start
            or checkpoint.summary.source_transcript_range
            != (checkpoint.transcript_start, checkpoint.transcript_end)
        ):
            raise ValueError("invalid_previous_checkpoint")
        expected = self._summary_hash(
            checkpoint.previous_summary_hash,
            checkpoint.evidence_hash,
            checkpoint.summary.render_for_prompt(),
        )
        if not checkpoint.evidence_hash or expected != checkpoint.summary_hash:
            raise ValueError("invalid_previous_checkpoint")
        return checkpoint.summary.model_copy(deep=True)

    @staticmethod
    def _successful_tool(item: Mapping[str, Any]) -> bool:
        if item.get("role") != "tool":
            return False
        status = str(item.get("status", "")).lower()
        if status in {"error", "failed", "denied", "cancelled"}:
            return False
        if item.get("is_error") or item.get("ok") is False:
            return False
        if item.get("policy_reason") not in (None, ""):
            return False
        return item.get("security_event_type") in (None, "")

    @staticmethod
    def _normalize_path(value: str) -> str:
        return posixpath.normpath(value.replace("\\", "/"))

    @classmethod
    def _validate_summary(
        cls,
        summary: CompactionSummary,
        archived: list[dict[str, Any]],
        archived_range: tuple[int, int],
    ) -> str:
        if summary.source_transcript_range != archived_range:
            return "source_transcript_range_invalid"
        todo_ids: set[str] = set()
        files_read: set[str] = set()
        files_modified: set[str] = set()
        tests: set[str] = set()
        artifacts: set[str] = set()
        run_ids: set[str] = set()
        for item in archived:
            role = item.get("role")
            run_id = item.get("run_id")
            if role in {"user", "assistant"} and isinstance(run_id, str):
                run_ids.add(run_id)
            if not cls._successful_tool(item):
                continue
            if isinstance(run_id, str):
                run_ids.add(run_id)
            raw_args = item.get("args")
            args: Mapping[str, Any] = raw_args if isinstance(raw_args, Mapping) else {}
            name = item.get("name")
            if name == "todo_update":
                todo_id = args.get("todo_id")
                if isinstance(todo_id, str):
                    todo_ids.add(todo_id)
            elif name in {"todo_add", "todo_list"}:
                todo_id = item.get("todo_id")
                if isinstance(todo_id, str):
                    todo_ids.add(todo_id)
                content = item.get("content")
                if isinstance(content, str):
                    todo_ids.update(re.findall(r"\btodo_\d+\b", content))
            path = args.get("path")
            if isinstance(path, str) and name in {"read_file", "search", "list_files"}:
                files_read.add(cls._normalize_path(path))
            if isinstance(path, str) and name in {"write_file", "patch_file"}:
                files_modified.add(cls._normalize_path(path))
            command = args.get("command") if name == "run_shell" else None
            if isinstance(command, str):
                tests.add(command)
            for value in (item.get("test_command"), args.get("test_command")):
                if isinstance(value, str):
                    tests.add(value)
            artifact = item.get("artifact_ref")
            if isinstance(artifact, str):
                artifacts.add(artifact)

        checks = (
            (summary.active_todos, todo_ids, False),
            (summary.files_read, files_read, True),
            (summary.files_modified, files_modified, True),
            (summary.tests, tests, False),
            (summary.artifact_refs, artifacts, False),
            (summary.source_run_ids, run_ids, False),
        )
        for references, evidence, normalize_paths in checks:
            for reference in references:
                candidate = cls._normalize_path(reference) if normalize_paths else reference
                if candidate not in evidence:
                    return f"summary_reference_missing:{reference}"
        return ""

    def _count_history(self, history: list[dict[str, Any]]) -> int:
        rendered = json.dumps(history, ensure_ascii=False, sort_keys=True, default=str)
        return self.counter.count(rendered).tokens

    def _safe_count(self, history: list[dict[str, Any]]) -> int:
        try:
            return self._count_history(history)
        except asyncio.CancelledError:
            raise
        except Exception:
            return 1

    @staticmethod
    def _evidence_hash(history: list[dict[str, Any]]) -> str:
        rendered = json.dumps(history, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(rendered.encode("utf-8")).hexdigest()

    @staticmethod
    def _prefix_hash(prefix: list[dict[str, Any]]) -> str:
        if not prefix:
            return ""
        rendered = json.dumps(prefix, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(rendered.encode("utf-8")).hexdigest()

    @staticmethod
    def _summary_hash(previous_hash: str, evidence_hash: str, rendered: str) -> str:
        payload = f"{previous_hash}\n{evidence_hash}\n{rendered}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _start_cooldown(self, state: _SessionState) -> float:
        state.cooldown_until = self.monotonic() + self.compaction_policy.cooldown_seconds
        return state.cooldown_until

    @staticmethod
    def _unchanged(
        history: list[dict[str, Any]],
        checkpoint: CompactionCheckpoint | None,
        tokens: int,
        reason: str,
        compaction_id: str,
        trigger: str,
        *,
        retryable: bool = False,
        cooldown_until: float | None = None,
    ) -> CompactionResult:
        return CompactionResult(
            False,
            deepcopy(history),
            checkpoint,
            tokens,
            tokens,
            0,
            reason,
            compaction_id,
            trigger,
            retryable,
            cooldown_until,
        )
