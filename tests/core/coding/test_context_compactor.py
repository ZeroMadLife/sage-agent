"""Safe structured context compaction tests."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from typing import Any, cast

import pytest
from pydantic import ValidationError

from core.coding.context.budget import ContextPolicy, TokenCount, TokenCounter
from core.coding.context.compact import (
    CompactionBusyError,
    CompactionPolicy,
    CompactManager,
)
from core.coding.context.summary import CompactionCheckpoint, CompactionSummary


class QueueSummarizer:
    """Return queued values while recording structured compaction requests."""

    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def summarize(
        self,
        *,
        archived_history: list[dict[str, Any]],
        previous_summary: CompactionSummary | None,
        focus: str,
        max_tokens: int,
        source_transcript_range: tuple[int, int],
        repair_feedback: str | None,
    ) -> CompactionSummary | Mapping[str, Any]:
        request = {
            "archived_history": archived_history,
            "previous_summary": previous_summary,
            "focus": focus,
            "max_tokens": max_tokens,
            "source_transcript_range": source_transcript_range,
            "repair_feedback": repair_feedback,
        }
        self.calls.append(request)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return cast(CompactionSummary | Mapping[str, Any], response)


class ItemCounter(TokenCounter):
    """Count ten tokens per history item to make tail boundaries exact."""

    def count(self, text: str) -> TokenCount:
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return TokenCount(tokens=10, estimated=False)
        items = len(value) if isinstance(value, list) else 1
        return TokenCount(tokens=max(1, items * 10), estimated=False)


class ExplodingCounter(TokenCounter):
    def __init__(self) -> None:
        self.calls = 0

    def count(self, text: str) -> TokenCount:
        self.calls += 1
        if self.calls > 1:
            raise RuntimeError("secret counter detail")
        return TokenCount(tokens=100, estimated=False)


class ValueErrorCounter(ExplodingCounter):
    def count(self, text: str) -> TokenCount:
        self.calls += 1
        if self.calls > 1:
            raise ValueError("secret token failure")
        return TokenCount(tokens=100, estimated=False)


class ConcurrentSummarizer:
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0

    async def summarize(
        self,
        *,
        archived_history: list[dict[str, Any]],
        previous_summary: CompactionSummary | None,
        focus: str,
        max_tokens: int,
        source_transcript_range: tuple[int, int],
        repair_feedback: str | None,
    ) -> CompactionSummary | Mapping[str, Any]:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        return _summary(source_transcript_range)


class BrokenInvalidator:
    def invalidate_system_prompt(self) -> None:
        raise RuntimeError("cache secret")


class CancellingInvalidator:
    def invalidate_system_prompt(self) -> None:
        raise asyncio.CancelledError


def _policy() -> ContextPolicy:
    return ContextPolicy(context_window_tokens=1_000, output_reserve_tokens=100)


def _history(turns: int = 6, *, verbose: bool = True) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    padding = " context" * 30 if verbose else ""
    for index in range(turns):
        history.extend(
            [
                {
                    "role": "user",
                    "content": f"request {index}{padding}",
                    "run_id": f"run-{index}",
                    "turn_id": f"todo-{index}",
                },
                {
                    "role": "tool",
                    "name": "read_file",
                    "args": {"path": f"src/file-{index}.py"},
                    "content": f"tool result {index}{padding}",
                    "artifact_ref": f"artifact-{index}",
                    "run_id": f"run-{index}",
                },
                {
                    "role": "assistant",
                    "content": f"answer {index}{padding}",
                    "run_id": f"run-{index}",
                },
            ]
        )
    return history


def _sequenced_history(start: int = 0) -> list[dict[str, Any]]:
    history = _history()
    for sequence, item in enumerate(history, start=start):
        item["sequence"] = sequence
    return history


def _summary(
    source_range: tuple[int, int],
    *,
    goal: str = "finish request 2",
    active_todos: list[str] | None = None,
    source_run_ids: list[str] | None = None,
) -> CompactionSummary:
    return CompactionSummary(
        goal=goal,
        active_todos=tuple(active_todos or ()),
        source_transcript_range=source_range,
        source_run_ids=tuple(source_run_ids or ()),
    )


def _checkpoint() -> CompactionCheckpoint:
    summary = _summary((40, 44))
    evidence_hash = "old-evidence"
    summary_hash = hashlib.sha256(
        f"\n{evidence_hash}\n{summary.render_for_prompt()}".encode()
    ).hexdigest()
    return CompactionCheckpoint(
        compaction_id="compact-old",
        transcript_start=40,
        transcript_end=44,
        summary=summary,
        summary_hash=summary_hash,
        evidence_hash=evidence_hash,
    )


def _trusted_checkpoint(session_id: str, checkpoint: CompactionCheckpoint) -> bool:
    return session_id == "test" and bool(checkpoint.compaction_id)


async def test_compactor_preserves_recent_three_complete_turns() -> None:
    history = _history()
    summarizer = QueueSummarizer(_summary((0, 8), source_run_ids=["run-0", "run-1", "run-2"]))

    result = await CompactManager(
        summarizer=summarizer,
        policy=_policy(),
        checkpoint_verifier=_trusted_checkpoint,
    ).compact(
        session_id="test",
        history=history,
        trigger="auto",
    )

    assert result.applied is True
    assert result.archived_items == 9
    assert result.projected_history[1:] == history[-9:]
    assert result.projected_history[0]["kind"] == "compact_summary"
    assert result.projected_history[0]["content"].splitlines()[0] == (
        "Historical handoff only; the latest user message always wins."
    )


async def test_compactor_keeps_up_to_twelve_complete_turns_within_tail_budget() -> None:
    history = _history(turns=14, verbose=False)
    summarizer = QueueSummarizer(_summary((0, 5)))
    manager = CompactManager(
        summarizer=summarizer,
        policy=ContextPolicy(context_window_tokens=10_000, output_reserve_tokens=1_000),
        compaction_policy=CompactionPolicy(minimum_savings_ratio=0.0),
    )

    result = await manager.compact(session_id="test", history=history)

    assert result.applied is True
    assert result.archived_items == 6
    assert result.projected_history[1:] == history[-36:]


async def test_compactor_tail_budget_never_splits_a_turn() -> None:
    history = _history(turns=8, verbose=False)
    summarizer = QueueSummarizer(_summary((0, 14)))
    manager = CompactManager(
        summarizer=summarizer,
        policy=_policy(),
        compaction_policy=CompactionPolicy(tail_budget_ratio=0.20),
    )

    result = await manager.compact(session_id="test", history=history)

    assert result.applied is True
    assert result.archived_items == 15
    assert result.projected_history[1:] == history[-9:]
    assert result.projected_history[1]["role"] == "user"


async def test_tail_budget_uses_effective_compaction_budget() -> None:
    history = _history(turns=8, verbose=False)
    summarizer = QueueSummarizer(_summary((0, 14)))
    manager = CompactManager(
        summarizer=summarizer,
        policy=_policy(),
        counter=ItemCounter(),
        compaction_policy=CompactionPolicy(minimum_savings_ratio=0.0),
    )

    result = await manager.compact(session_id="test", history=history)

    assert result.applied is True
    assert result.projected_history[1:] == history[-9:]


async def test_schema_validation_error_is_repaired_once() -> None:
    invalid = {
        "goal": 123,
        "source_transcript_range": [0, 8],
        "unexpected": "forbidden",
    }
    summarizer = QueueSummarizer(invalid, _summary((0, 8)))

    result = await CompactManager(
        summarizer=summarizer,
        policy=_policy(),
        checkpoint_verifier=_trusted_checkpoint,
    ).compact(session_id="test", history=_history())

    assert result.applied is True
    assert len(summarizer.calls) == 2
    assert summarizer.calls[1]["repair_feedback"] == "summary_schema_invalid"


async def test_todo_evidence_requires_exact_identifier_match() -> None:
    history = _history()
    history[6]["turn_id"] = "todo-20"
    invalid = _summary((0, 8), active_todos=["todo-2"])
    summarizer = QueueSummarizer(invalid, invalid)

    result = await CompactManager(summarizer=summarizer, policy=_policy()).compact(
        session_id="test", history=history
    )

    assert result.applied is False
    assert result.reason == "summary_quality_invalid"


async def test_file_evidence_rejects_path_prefix_match() -> None:
    invalid = CompactionSummary(
        goal="finish",
        files_read=("src/file-2",),
        source_transcript_range=(0, 8),
    )
    summarizer = QueueSummarizer(invalid, invalid)

    result = await CompactManager(
        summarizer=summarizer,
        policy=_policy(),
        checkpoint_verifier=_trusted_checkpoint,
    ).compact(session_id="test", history=_history())

    assert result.applied is False
    assert result.reason == "summary_quality_invalid"


def test_summary_schema_rejects_coerced_scalars_and_oversized_lists() -> None:
    with pytest.raises(ValidationError):
        CompactionSummary.model_validate({"goal": 123, "source_transcript_range": [0, 1]})
    with pytest.raises(ValidationError):
        CompactionSummary.model_validate(
            {
                "goal": "bounded",
                "active_todos": [f"todo-{index}" for index in range(257)],
                "source_transcript_range": [0, 1],
            }
        )


async def test_explicit_transcript_range_must_match_archived_item_count() -> None:
    summarizer = QueueSummarizer(_summary((0, 9)))

    result = await CompactManager(summarizer=summarizer, policy=_policy()).compact(
        session_id="test", history=_history(), transcript_range=(0, 9)
    )

    assert result.applied is False
    assert result.reason == "invalid_transcript_range"
    assert summarizer.calls == []


async def test_real_contiguous_sequences_define_transcript_range() -> None:
    history = _history()
    for sequence, item in enumerate(history, start=100):
        item["sequence"] = sequence
    summarizer = QueueSummarizer(_summary((100, 108)))

    result = await CompactManager(summarizer=summarizer, policy=_policy()).compact(
        session_id="test", history=history
    )

    assert result.applied is True
    assert result.checkpoint is not None
    assert result.checkpoint.transcript_start == 100
    assert result.checkpoint.transcript_end == 108


@pytest.mark.parametrize("sequences", [list(range(-1, 17)), [100, None, *range(102, 118)]])
async def test_invalid_real_sequences_do_not_fall_back(
    sequences: list[int | None],
) -> None:
    history = _history()
    for sequence, item in zip(sequences, history, strict=True):
        item["sequence"] = sequence
    summarizer = QueueSummarizer(_summary((0, 8)))

    result = await CompactManager(summarizer=summarizer, policy=_policy()).compact(
        session_id="test", history=history
    )

    assert result.applied is False
    assert result.reason in {
        "invalid_transcript_range",
        "non_contiguous_transcript_range",
    }
    assert summarizer.calls == []


async def test_damaged_previous_checkpoint_hash_is_rejected() -> None:
    damaged = replace(_checkpoint(), summary_hash="tampered")
    summarizer = QueueSummarizer(_summary((45, 53)))

    result = await CompactManager(
        summarizer=summarizer,
        policy=_policy(),
        checkpoint_verifier=_trusted_checkpoint,
    ).compact(session_id="test", history=_history(), previous_checkpoint=damaged)

    assert result.applied is False
    assert result.reason == "invalid_previous_checkpoint"
    assert result.checkpoint is damaged
    assert summarizer.calls == []


@pytest.mark.parametrize("verifier", [None, lambda session_id, checkpoint: False])
async def test_previous_checkpoint_requires_external_trust(
    verifier: Any,
) -> None:
    summarizer = QueueSummarizer(_summary((45, 53)))
    manager = CompactManager(
        summarizer=summarizer,
        policy=_policy(),
        checkpoint_verifier=verifier,
    )

    result = await manager.compact(
        session_id="test", history=_history(), previous_checkpoint=_checkpoint()
    )

    assert result.applied is False
    assert result.reason == "invalid_previous_checkpoint"
    assert summarizer.calls == []


async def test_previous_checkpoint_metadata_must_match_its_summary_range() -> None:
    damaged = replace(_checkpoint(), transcript_end=53)
    summarizer = QueueSummarizer(_summary((54, 62)))

    result = await CompactManager(
        summarizer=summarizer,
        policy=_policy(),
        checkpoint_verifier=_trusted_checkpoint,
    ).compact(session_id="test", history=_history(), previous_checkpoint=damaged)

    assert result.applied is False
    assert result.reason == "invalid_previous_checkpoint"
    assert summarizer.calls == []


async def test_system_prefix_is_preserved_and_old_summary_is_removed() -> None:
    prefix = {
        "role": "system",
        "content": "project safety policy",
        "marker": 7,
    }
    stale = {"role": "system", "kind": "compact_summary", "content": "stale"}
    history: list[dict[str, Any]] = [prefix, stale, *_sequenced_history(45)]
    summarizer = QueueSummarizer(_summary((45, 53)))
    previous = replace(_checkpoint(), prefix_hash=CompactManager._prefix_hash([prefix]))

    result = await CompactManager(
        summarizer=summarizer,
        policy=_policy(),
        checkpoint_verifier=_trusted_checkpoint,
    ).compact(session_id="test", history=history, previous_checkpoint=previous)

    assert result.applied is True
    assert result.projected_history[0] == prefix
    assert result.projected_history[1]["kind"] == "compact_summary"
    assert all(item.get("content") != "stale" for item in result.projected_history)
    assert summarizer.calls[0]["archived_history"] == _sequenced_history(45)[:9]


async def test_previous_summary_and_checkpoint_summary_are_deep_copies() -> None:
    previous = _checkpoint()
    response = _summary((45, 53))
    summarizer = QueueSummarizer(response)

    result = await CompactManager(
        summarizer=summarizer,
        policy=_policy(),
        checkpoint_verifier=_trusted_checkpoint,
    ).compact(
        session_id="test",
        history=_sequenced_history(45),
        previous_checkpoint=previous,
    )

    assert result.applied is True
    assert summarizer.calls[0]["previous_summary"] == previous.summary
    assert summarizer.calls[0]["previous_summary"] is not previous.summary
    assert result.checkpoint is not None
    assert result.checkpoint.summary == response
    assert result.checkpoint.summary is not response
    assert isinstance(result.checkpoint.summary.active_todos, tuple)


async def test_post_split_exception_returns_stable_failure_metadata() -> None:
    history = _history(turns=4)
    manager = CompactManager(
        summarizer=QueueSummarizer(_summary((0, 2))),
        policy=_policy(),
        counter=ExplodingCounter(),
    )

    result = await manager.compact(session_id="test", history=history, trigger="auto")

    assert result.applied is False
    assert result.projected_history == history
    assert result.projected_history is not history
    assert result.reason == "compaction_failed"
    assert result.compaction_id.startswith("compact-")
    assert result.trigger == "auto"
    assert "secret" not in result.reason


async def test_cancelled_summarizer_propagates_cancellation() -> None:
    manager = CompactManager(summarizer=QueueSummarizer(asyncio.CancelledError()), policy=_policy())

    with pytest.raises(asyncio.CancelledError):
        await manager.compact(session_id="test", history=_history())


async def test_cancelled_attempt_does_not_change_session_circuit_state() -> None:
    history = _history(turns=4, verbose=False)
    ineffective = _summary((0, 2), goal="request 0" * 100)
    summarizer = QueueSummarizer(
        ineffective,
        asyncio.CancelledError(),
        ineffective,
    )
    manager = CompactManager(summarizer=summarizer, policy=_policy())

    first = await manager.compact(session_id="state", history=history, trigger="auto")
    with pytest.raises(asyncio.CancelledError):
        await manager.compact(session_id="state", history=history, trigger="manual")
    second = await manager.compact(session_id="state", history=history, trigger="auto")
    blocked = await manager.compact(session_id="state", history=history, trigger="auto")

    assert first.reason == second.reason == "ineffective_summary"
    assert blocked.reason == "auto_compaction_circuit_open"


async def test_session_id_is_required_and_non_empty() -> None:
    manager = CompactManager(summarizer=QueueSummarizer(_summary((0, 8))), policy=_policy())

    with pytest.raises(ValueError, match="session_id"):
        await manager.compact(session_id="", history=_history())


async def test_provider_failure_starts_session_cooldown_and_manual_bypasses() -> None:
    clock = [100.0]
    summarizer = QueueSummarizer(
        RuntimeError("provider secret"),
        _summary((0, 8)),
        _summary((0, 8)),
    )
    manager = CompactManager(
        summarizer=summarizer,
        policy=_policy(),
        monotonic=lambda: clock[0],
    )

    failed = await manager.compact(history=_history(), trigger="auto", session_id="a")
    cooled = await manager.compact(history=_history(), trigger="auto", session_id="a")
    other = await manager.compact(history=_history(), trigger="auto", session_id="b")
    manual = await manager.compact(history=_history(), trigger="manual", session_id="a")

    assert failed.reason == "summarizer_failed"
    assert failed.retryable is True
    assert failed.cooldown_until == 160.0
    assert cooled.reason == "cooldown_active"
    assert cooled.cooldown_until == 160.0
    assert other.applied is True
    assert manual.applied is True
    assert len(summarizer.calls) == 3


async def test_same_session_compactions_fail_fast_when_busy() -> None:
    summarizer = ConcurrentSummarizer()
    manager = CompactManager(summarizer=summarizer, policy=_policy())

    first_task = asyncio.create_task(manager.compact(history=_history(), session_id="same"))
    await asyncio.sleep(0)
    with pytest.raises(CompactionBusyError):
        await manager.compact(history=_history(), session_id="same")
    busy = await manager.compact(history=_history(), session_id="same", trigger="auto")
    first = await first_task

    assert first.applied is True
    assert busy.reason == "compaction_busy"
    assert busy.retryable is True
    assert summarizer.max_active == 1


async def test_different_session_compactions_do_not_share_lock() -> None:
    summarizer = ConcurrentSummarizer()
    manager = CompactManager(summarizer=summarizer, policy=_policy())

    first, second = await asyncio.gather(
        manager.compact(history=_history(), session_id="a"),
        manager.compact(history=_history(), session_id="b"),
    )

    assert first.applied is second.applied is True
    assert summarizer.max_active == 2


async def test_cache_invalidation_is_best_effort_after_success() -> None:
    result = await CompactManager(
        summarizer=QueueSummarizer(_summary((0, 8))), policy=_policy()
    ).compact(session_id="test", history=_history(), context_manager=BrokenInvalidator())

    assert result.applied is True
    assert result.reason == ""


async def test_checkpoint_hash_chain_binds_prior_evidence_and_summary() -> None:
    first_manager = CompactManager(summarizer=QueueSummarizer(_summary((0, 8))), policy=_policy())
    first = await first_manager.compact(session_id="test", history=_history())
    assert first.checkpoint is not None
    second_manager = CompactManager(
        summarizer=QueueSummarizer(_summary((9, 17))),
        policy=_policy(),
        checkpoint_verifier=_trusted_checkpoint,
    )

    second = await second_manager.compact(
        session_id="test",
        history=_sequenced_history(9),
        previous_checkpoint=first.checkpoint,
    )

    assert second.applied is True
    assert second.checkpoint is not None
    assert second.checkpoint.previous_summary_hash == first.checkpoint.summary_hash
    assert second.checkpoint.evidence_hash
    expected = CompactManager._summary_hash(
        second.checkpoint.previous_summary_hash,
        second.checkpoint.evidence_hash,
        second.checkpoint.summary.render_for_prompt(),
    )
    assert second.checkpoint.summary_hash == expected
    assert second.compaction_id == second.checkpoint.compaction_id


async def test_terminal_quality_failure_starts_cooldown() -> None:
    invalid = _summary((0, 8), active_todos=["not-evidence"])
    summarizer = QueueSummarizer(invalid, invalid)
    manager = CompactManager(summarizer=summarizer, policy=_policy(), monotonic=lambda: 10.0)

    failed = await manager.compact(session_id="test", history=_history(), trigger="auto")
    cooled = await manager.compact(session_id="test", history=_history(), trigger="auto")

    assert failed.reason == "summary_quality_invalid"
    assert failed.retryable is True
    assert failed.cooldown_until == 70.0
    assert cooled.reason == "cooldown_active"
    assert len(summarizer.calls) == 2


async def test_summary_render_exception_is_contained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_render(self: CompactionSummary) -> str:
        raise RuntimeError("render secret")

    monkeypatch.setattr(CompactionSummary, "render_for_prompt", fail_render)
    history = _history()

    result = await CompactManager(
        summarizer=QueueSummarizer(_summary((0, 8))), policy=_policy()
    ).compact(session_id="test", history=history)

    assert result.applied is False
    assert result.projected_history == history
    assert result.reason == "compaction_failed"


async def test_explicit_range_maps_full_source_history_to_archived_span() -> None:
    summarizer = QueueSummarizer(_summary((100, 108)))

    result = await CompactManager(summarizer=summarizer, policy=_policy()).compact(
        session_id="test", history=_history(), transcript_range=(100, 117)
    )

    assert result.applied is True
    assert result.checkpoint is not None
    assert (result.checkpoint.transcript_start, result.checkpoint.transcript_end) == (
        100,
        108,
    )


async def test_old_summary_does_not_consume_canonical_range_position() -> None:
    stale = {"role": "system", "kind": "compact_summary", "content": "stale"}
    history = [stale, *_sequenced_history(45)]
    summarizer = QueueSummarizer(_summary((45, 53)))

    result = await CompactManager(
        summarizer=summarizer,
        policy=_policy(),
        checkpoint_verifier=_trusted_checkpoint,
    ).compact(
        session_id="test",
        history=history,
        transcript_range=(45, 62),
        previous_checkpoint=_checkpoint(),
    )

    assert result.applied is True
    assert result.checkpoint is not None
    assert result.checkpoint.transcript_start == 45


async def test_explicit_range_cross_checks_item_sequences() -> None:
    history = _history()
    for sequence, item in enumerate(history, start=100):
        item["sequence"] = sequence
    history[5]["sequence"] = 999
    summarizer = QueueSummarizer(_summary((100, 108)))

    result = await CompactManager(summarizer=summarizer, policy=_policy()).compact(
        session_id="test", history=history, transcript_range=(100, 117)
    )

    assert result.reason == "non_contiguous_transcript_range"
    assert summarizer.calls == []


@pytest.mark.parametrize(
    ("tool_name", "args", "summary_field", "denial"),
    [
        (
            "read_file",
            {"path": "src/file-0.py"},
            {"files_read": ["src/file-0.py"]},
            {"status": "denied", "policy_reason": "outside workspace"},
        ),
        (
            "write_file",
            {"path": "src/file-0.py"},
            {"files_modified": ["src/file-0.py"]},
            {"ok": False},
        ),
        (
            "run_shell",
            {"command": "pytest tests/x.py"},
            {"tests": ["pytest tests/x.py"]},
            {"security_event_type": "command_denied"},
        ),
    ],
)
async def test_denied_tool_result_cannot_support_structured_evidence(
    tool_name: str,
    args: dict[str, str],
    summary_field: dict[str, list[str]],
    denial: dict[str, object],
) -> None:
    history = _history()
    history[1].update(name=tool_name, args=args, **denial)
    invalid = CompactionSummary.model_validate(
        {
            "goal": "finish",
            "source_transcript_range": [0, 8],
            **summary_field,
        }
    )
    summarizer = QueueSummarizer(invalid, invalid)

    result = await CompactManager(summarizer=summarizer, policy=_policy()).compact(
        session_id="test", history=history
    )

    assert result.applied is False
    assert result.reason == "summary_quality_invalid"


async def test_turn_id_is_not_todo_evidence() -> None:
    history = _history()
    history[0]["turn_id"] = "todo_7"
    invalid = _summary((0, 8), active_todos=["todo_7"])
    summarizer = QueueSummarizer(invalid, invalid)

    result = await CompactManager(summarizer=summarizer, policy=_policy()).compact(
        session_id="test", history=history
    )

    assert result.reason == "summary_quality_invalid"


async def test_denied_tool_run_id_is_not_source_evidence() -> None:
    history = _history()
    history[1].update(status="failed", run_id="denied-run")
    invalid = _summary((0, 8), source_run_ids=["denied-run"])
    summarizer = QueueSummarizer(invalid, invalid)

    result = await CompactManager(summarizer=summarizer, policy=_policy()).compact(
        session_id="test", history=history
    )

    assert result.reason == "summary_quality_invalid"


async def test_mid_history_system_item_fails_closed() -> None:
    protected = {"role": "system", "content": "mid-session safety", "marker": 9}
    history = _history()
    history.insert(4, protected)
    summarizer = QueueSummarizer(_summary((0, 8)))

    result = await CompactManager(summarizer=summarizer, policy=_policy()).compact(
        session_id="test", history=history
    )

    assert result.applied is False
    assert result.reason == "unsupported_control_layout"
    assert result.projected_history == history
    assert summarizer.calls == []


async def test_mid_history_control_role_fails_closed_without_reordering() -> None:
    protected = {"role": "control", "content": "approval boundary"}
    history = _history()
    history.insert(4, protected)
    summarizer = QueueSummarizer(_summary((0, 8)))

    result = await CompactManager(summarizer=summarizer, policy=_policy()).compact(
        session_id="test", history=history
    )

    assert result.applied is False
    assert result.reason == "unsupported_control_layout"
    assert result.projected_history == history
    assert result.projected_history[4] == protected
    assert summarizer.calls == []


async def test_success_event_with_empty_policy_fields_is_valid_evidence() -> None:
    history = _history()
    history[1].update(policy_reason=None, security_event_type="")
    summary = CompactionSummary(
        goal="finish",
        files_read=("src/file-0.py",),
        source_transcript_range=(0, 8),
    )

    result = await CompactManager(summarizer=QueueSummarizer(summary), policy=_policy()).compact(
        session_id="test", history=history
    )

    assert result.applied is True


async def test_archived_user_run_id_is_source_evidence() -> None:
    history = _history()
    history[0]["run_id"] = "user-only-run"
    summary = _summary((0, 8), source_run_ids=["user-only-run"])

    result = await CompactManager(summarizer=QueueSummarizer(summary), policy=_policy()).compact(
        session_id="test", history=history
    )

    assert result.applied is True


async def test_old_system_summary_without_trusted_checkpoint_is_not_removed() -> None:
    stale = {"role": "system", "kind": "compact_summary", "content": "stale"}
    history = [stale, *_history()]
    summarizer = QueueSummarizer(_summary((0, 8)))

    result = await CompactManager(summarizer=summarizer, policy=_policy()).compact(
        session_id="test", history=history
    )

    assert result.applied is False
    assert result.reason == "invalid_previous_checkpoint"
    assert result.projected_history == history
    assert summarizer.calls == []


async def test_old_summary_trust_check_precedes_insufficient_history() -> None:
    stale = {"role": "system", "kind": "compact_summary", "content": "stale"}
    history = [stale, *_history(turns=2)]
    summarizer = QueueSummarizer(_summary((0, 1)))

    result = await CompactManager(summarizer=summarizer, policy=_policy()).compact(
        session_id="test", history=history
    )

    assert result.reason == "invalid_previous_checkpoint"
    assert result.projected_history == history
    assert summarizer.calls == []


async def test_user_item_with_compact_summary_kind_is_canonical() -> None:
    history = _history()
    history[0]["kind"] = "compact_summary"
    summarizer = QueueSummarizer(_summary((0, 8)))

    result = await CompactManager(summarizer=summarizer, policy=_policy()).compact(
        session_id="test", history=history
    )

    assert result.applied is True
    assert history[0] in summarizer.calls[0]["archived_history"]


async def test_previous_checkpoint_without_sequences_fails_closed() -> None:
    summarizer = QueueSummarizer(_summary((45, 53)))
    manager = CompactManager(
        summarizer=summarizer,
        policy=_policy(),
        checkpoint_verifier=_trusted_checkpoint,
    )

    result = await manager.compact(
        session_id="test", history=_history(), previous_checkpoint=_checkpoint()
    )

    assert result.reason == "missing_transcript_sequence"
    assert summarizer.calls == []


async def test_verified_repeated_compaction_uses_real_sequences() -> None:
    prefix = {"role": "system", "content": "stable"}
    first_history = [prefix, *_sequenced_history(0)]
    first = await CompactManager(
        summarizer=QueueSummarizer(_summary((0, 8))), policy=_policy()
    ).compact(session_id="test", history=first_history)
    assert first.checkpoint is not None

    stale = {"role": "system", "kind": "compact_summary", "content": "old"}
    second_history = [deepcopy(prefix), stale, *_sequenced_history(9)]
    second = await CompactManager(
        summarizer=QueueSummarizer(_summary((9, 17))),
        policy=_policy(),
        checkpoint_verifier=_trusted_checkpoint,
    ).compact(
        session_id="test",
        history=second_history,
        previous_checkpoint=first.checkpoint,
    )

    assert second.applied is True
    assert second.checkpoint is not None
    assert (second.checkpoint.transcript_start, second.checkpoint.transcript_end) == (
        9,
        17,
    )


async def test_repeated_compaction_rejects_changed_stable_prefix() -> None:
    first_prefix = {"role": "system", "content": "stable"}
    first = await CompactManager(
        summarizer=QueueSummarizer(_summary((0, 8))), policy=_policy()
    ).compact(session_id="test", history=[first_prefix, *_sequenced_history(0)])
    assert first.checkpoint is not None
    changed_prefix = {"role": "system", "content": "tampered"}
    summarizer = QueueSummarizer(_summary((9, 17)))

    second = await CompactManager(
        summarizer=summarizer,
        policy=_policy(),
        checkpoint_verifier=_trusted_checkpoint,
    ).compact(
        session_id="test",
        history=[changed_prefix, *_sequenced_history(9)],
        previous_checkpoint=first.checkpoint,
    )

    assert second.applied is False
    assert second.reason == "prefix_changed"
    assert summarizer.calls == []


async def test_legacy_checkpoint_cannot_adopt_nonempty_prefix() -> None:
    prefix = {"role": "system", "content": "stable"}
    legacy = replace(_checkpoint(), prefix_hash="")
    summarizer = QueueSummarizer(_summary((45, 53)))

    result = await CompactManager(
        summarizer=summarizer,
        policy=_policy(),
        checkpoint_verifier=_trusted_checkpoint,
    ).compact(
        session_id="test",
        history=[prefix, *_sequenced_history(45)],
        previous_checkpoint=legacy,
    )

    assert result.reason == "prefix_changed"
    assert summarizer.calls == []


async def test_callback_cancellation_is_best_effort_after_commit() -> None:
    result = await CompactManager(
        summarizer=QueueSummarizer(_summary((0, 8))), policy=_policy()
    ).compact(session_id="test", history=_history(), context_manager=CancellingInvalidator())

    assert result.applied is True
    assert result.checkpoint is not None


async def test_auto_postprocessing_failure_starts_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_render(self: CompactionSummary) -> str:
        raise RuntimeError("render secret")

    monkeypatch.setattr(CompactionSummary, "render_for_prompt", fail_render)
    manager = CompactManager(
        summarizer=QueueSummarizer(_summary((0, 8)), _summary((0, 8))),
        policy=_policy(),
        monotonic=lambda: 20.0,
    )

    failed = await manager.compact(session_id="test", history=_history(), trigger="auto")
    cooled = await manager.compact(session_id="test", history=_history(), trigger="auto")

    assert failed.reason == "compaction_failed"
    assert failed.cooldown_until == 80.0
    assert cooled.reason == "cooldown_active"


async def test_auto_postprocessing_value_error_starts_cooldown() -> None:
    manager = CompactManager(
        summarizer=QueueSummarizer(_summary((0, 2))),
        policy=_policy(),
        counter=ValueErrorCounter(),
        monotonic=lambda: 30.0,
    )

    failed = await manager.compact(session_id="test", history=_history(turns=4), trigger="auto")
    cooled = await manager.compact(session_id="test", history=_history(turns=4), trigger="auto")

    assert failed.reason == "compaction_failed"
    assert failed.cooldown_until == 90.0
    assert cooled.reason == "cooldown_active"


async def test_compactor_never_changes_source_history() -> None:
    history = _history()
    original = deepcopy(history)
    summarizer = QueueSummarizer(_summary((0, 8)))

    result = await CompactManager(summarizer=summarizer, policy=_policy()).compact(
        session_id="test", history=history
    )

    assert history == original
    result.projected_history[-1]["content"] = "changed projection"
    assert history == original


async def test_compactor_updates_previous_summary_iteratively() -> None:
    previous = _checkpoint()
    summarizer = QueueSummarizer(_summary((45, 53)))

    result = await CompactManager(
        summarizer=summarizer,
        policy=_policy(),
        checkpoint_verifier=_trusted_checkpoint,
    ).compact(
        session_id="test",
        history=_sequenced_history(45),
        previous_checkpoint=previous,
        transcript_range=(45, 62),
    )

    assert result.applied is True
    assert summarizer.calls[0]["previous_summary"] == previous.summary
    assert result.checkpoint is not None
    assert result.checkpoint.summary != previous.summary
    assert result.checkpoint.transcript_start == 45
    assert result.checkpoint.transcript_end == 53


async def test_invalid_summary_keeps_previous_checkpoint() -> None:
    previous = _checkpoint()
    summarizer = QueueSummarizer(_summary((900, 999)), _summary((900, 999)))
    history = _sequenced_history(45)

    result = await CompactManager(
        summarizer=summarizer,
        policy=_policy(),
        checkpoint_verifier=_trusted_checkpoint,
    ).compact(
        session_id="test",
        history=history,
        previous_checkpoint=previous,
    )

    assert result.applied is False
    assert result.checkpoint is previous
    assert result.projected_history == history
    assert len(summarizer.calls) == 2
    assert summarizer.calls[1]["repair_feedback"]


async def test_summary_missing_todo_id_is_repaired_once() -> None:
    summarizer = QueueSummarizer(
        _summary((0, 8), active_todos=["todo-missing"]),
        _summary((0, 8), active_todos=["todo-2"]),
    )

    history = _history()
    history[7]["name"] = "todo_update"
    history[7]["args"] = {"todo_id": "todo-2"}
    result = await CompactManager(summarizer=summarizer, policy=_policy()).compact(
        session_id="test",
        history=history,
    )

    assert result.applied is True
    assert len(summarizer.calls) == 2
    assert "todo-missing" in summarizer.calls[1]["repair_feedback"]


async def test_summary_failure_preserves_original_context() -> None:
    previous = _checkpoint()
    history = _sequenced_history(45)
    summarizer = QueueSummarizer(RuntimeError("provider unavailable"))

    result = await CompactManager(
        summarizer=summarizer,
        policy=_policy(),
        checkpoint_verifier=_trusted_checkpoint,
    ).compact(
        session_id="test",
        history=history,
        previous_checkpoint=previous,
    )

    assert result.applied is False
    assert result.projected_history == history
    assert result.projected_history is not history
    assert result.checkpoint is previous
    assert result.reason == "summarizer_failed"


async def test_ineffective_summary_is_not_applied() -> None:
    history = _history(turns=4, verbose=False)
    summarizer = QueueSummarizer(_summary((0, 2), goal="request 0" * 100))

    result = await CompactManager(summarizer=summarizer, policy=_policy()).compact(
        session_id="test", history=history
    )

    assert result.applied is False
    assert result.projected_history == history
    assert result.reason == "ineffective_summary"


async def test_second_ineffective_result_opens_circuit() -> None:
    history = _history(turns=4, verbose=False)
    summarizer = QueueSummarizer(
        _summary((0, 2), goal="request 0" * 100),
        _summary((0, 2), goal="request 0" * 100),
        _summary((0, 2), goal="request 0" * 100),
    )
    manager = CompactManager(summarizer=summarizer, policy=_policy())

    first = await manager.compact(session_id="test", history=history, trigger="auto")
    second = await manager.compact(session_id="test", history=history, trigger="auto")
    blocked = await manager.compact(session_id="test", history=history, trigger="auto")
    manual = await manager.compact(session_id="test", history=history, trigger="manual")

    assert first.reason == second.reason == "ineffective_summary"
    assert blocked.applied is False
    assert blocked.reason == "auto_compaction_circuit_open"
    assert manual.reason == "ineffective_summary"
    assert len(summarizer.calls) == 3


async def test_success_resets_failure_counter() -> None:
    short = _history(turns=4, verbose=False)
    long = _history()
    summarizer = QueueSummarizer(
        _summary((0, 2), goal="request 0" * 100),
        _summary((0, 8)),
        _summary((0, 2), goal="request 0" * 100),
    )
    manager = CompactManager(summarizer=summarizer, policy=_policy())

    first = await manager.compact(session_id="test", history=short, trigger="auto")
    success = await manager.compact(session_id="test", history=long, trigger="auto")
    after_reset = await manager.compact(session_id="test", history=short, trigger="auto")

    assert first.reason == after_reset.reason == "ineffective_summary"
    assert success.applied is True
    assert len(summarizer.calls) == 3
