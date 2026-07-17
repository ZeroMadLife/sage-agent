from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from core.coding.context import (
    CompactionCheckpoint,
    CompactionResult,
    CompactionSummary,
    CompactManager,
    ContextBusyError,
    ContextPolicy,
    ModelCapabilityRegistry,
    PreparedContext,
)
from core.coding.context.budget import ContextUsage
from core.coding.engine.events import (
    ContextCompactionCompletedEvent,
    ContextCompactionFailedEvent,
    ContextCompactionStartedEvent,
    ContextUsageUpdatedEvent,
)
from core.coding.persistence import TranscriptStore
from core.coding.runtime import CodingRuntime


class RecordingModel:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.responses.pop(0)


class FailingModel:
    async def complete(self, prompt: str) -> str:
        del prompt
        raise RuntimeError("model unavailable")


def _usage() -> ContextUsage:
    return ContextUsage(
        used_tokens=100,
        effective_limit_tokens=90_000,
        usage_ratio=100 / 90_000,
        level="normal",
        estimated=False,
    )


def _checkpoint(compaction_id: str, start: int, end: int) -> CompactionCheckpoint:
    summary = CompactionSummary(
        goal="keep working",
        source_transcript_range=(start, end),
    )
    evidence_hash = hashlib.sha256(f"{start}:{end}".encode()).hexdigest()
    summary_hash = hashlib.sha256(
        f"\n{evidence_hash}\n{summary.render_for_prompt()}".encode()
    ).hexdigest()
    return CompactionCheckpoint(
        compaction_id=compaction_id,
        transcript_start=start,
        transcript_end=end,
        summary=summary,
        summary_hash=summary_hash,
        evidence_hash=evidence_hash,
        prefix_hash=hashlib.sha256(b"[]").hexdigest(),
    )


def _bind_checkpoint_to_evidence(
    checkpoint: CompactionCheckpoint, evidence: list[dict[str, Any]]
) -> CompactionCheckpoint:
    evidence_hash = CompactManager._evidence_hash(evidence)
    summary_hash = CompactManager._summary_hash(
        checkpoint.previous_summary_hash,
        evidence_hash,
        checkpoint.summary.render_for_prompt(),
    )
    return replace(
        checkpoint,
        evidence_hash=evidence_hash,
        summary_hash=summary_hash,
        prefix_hash=CompactManager._prefix_hash([]),
    )
class LifecycleController:
    def __init__(self, *, applied: bool) -> None:
        self.lifecycle_sink: Any = None
        self.applied = applied
        self.on_turn_start_calls = 0
        self.before_model_calls = 0
        self.model: RecordingModel | None = None

    async def on_turn_start(
        self,
        history: list[dict[str, Any]],
        user_message: str,
        run_id: str,
        **kwargs: Any,
    ) -> PreparedContext:
        del user_message, kwargs
        self.on_turn_start_calls += 1
        assert self.model is not None and self.model.prompts == []
        compaction_id = "compact-runtime-test"
        started = ContextCompactionStartedEvent(
            session_id="s-compact-order",
            run_id=run_id,
            compaction_id=compaction_id,
            trigger="auto",
            before_tokens=100,
        )
        await self.lifecycle_sink(started, None)
        checkpoint = _checkpoint(compaction_id, 1, max(1, len(history))) if self.applied else None
        projection = (
            [
                {
                    "role": "system",
                    "kind": "compact_summary",
                    "content": checkpoint.summary.render_for_prompt(),
                }
            ]
            if checkpoint is not None
            else deepcopy(history)
        )
        result = CompactionResult(
            applied=self.applied,
            projected_history=projection,
            checkpoint=checkpoint,
            before_tokens=100,
            after_tokens=20 if self.applied else 100,
            archived_items=len(history) if self.applied else 0,
            reason="" if self.applied else "summarizer_failed",
            compaction_id=compaction_id,
            trigger="auto",
        )
        terminal: Any
        if self.applied:
            terminal = ContextCompactionCompletedEvent(
                session_id="s-compact-order",
                run_id=run_id,
                compaction_id=compaction_id,
                before_tokens=100,
                after_tokens=20,
                archived_items=len(history),
            )
        else:
            terminal = ContextCompactionFailedEvent(
                session_id="s-compact-order",
                run_id=run_id,
                compaction_id=compaction_id,
                reason="summarizer_failed",
            )
        await self.lifecycle_sink(terminal, result)
        usage_event = ContextUsageUpdatedEvent(
            session_id="s-compact-order",
            run_id=run_id,
            used_tokens=20,
            model_limit_tokens=100_000,
            output_reserve_tokens=10_000,
            effective_limit_tokens=90_000,
            usage_ratio=20 / 90_000,
            level="normal",
            estimated=False,
            compactable=True,
        )
        return PreparedContext.create(
            projected_history=projection,
            usage=_usage(),
            allow_model_request=True,
            compaction_result=result,
            events=(started, terminal, usage_event),
        )

    def before_model_request(self, history: list[dict[str, Any]], **kwargs: Any) -> PreparedContext:
        del kwargs
        self.before_model_calls += 1
        return PreparedContext.create(
            projected_history=history,
            usage=_usage(),
            allow_model_request=True,
        )


@pytest.mark.asyncio
async def test_runtime_persists_current_user_once_with_stable_sequence(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    model = RecordingModel(["<final>done</final>"])
    runtime = CodingRuntime(
        session_id="s-context",
        workspace_root=workspace,
        model=model,
        storage_root=tmp_path / ".coding",
        context_policy=ContextPolicy(
            context_window_tokens=100_000,
            output_reserve_tokens=10_000,
        ),
        checkpoint_anchor_key=b"k" * 32,
    )

    events = [event async for event in runtime.run_turn("unique-current-request")]

    transcript = runtime.transcript_store.read_all()
    user_items = [item for item in transcript if item.role == "user"]
    assert len(user_items) == 1
    assert user_items[0].sequence == 1
    assert runtime.session["history"][0]["sequence"] == 1
    assert model.prompts[0].count("unique-current-request") == 1
    usage = next(event for event in events if event["type"] == "context_usage_updated")
    assert usage["effective_limit_tokens"] == 90_000


@pytest.mark.asyncio
async def test_runtime_compacts_before_first_model_and_emits_each_event_once(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    model = RecordingModel(["<final>done</final>"])
    controller = LifecycleController(applied=True)
    controller.model = model
    runtime = CodingRuntime(
        session_id="s-compact-order",
        workspace_root=workspace,
        model=model,
        storage_root=tmp_path / ".coding",
        session_state={
            "id": "s-compact-order",
            "workspace_root": str(workspace),
            "history": [{"role": "user", "content": "old"}],
        },
        context_controller=controller,  # type: ignore[arg-type]
        checkpoint_anchor_key=b"k" * 32,
    )

    events = [event async for event in runtime.run_turn("new")]

    assert controller.on_turn_start_calls == 1
    assert model.prompts
    for event_type in (
        "context_compaction_started",
        "context_compaction_completed",
        "context_usage_updated",
    ):
        assert sum(event["type"] == event_type for event in events) == 1
    records = [
        json.loads(line)
        for line in runtime.session_event_bus.path.read_text(encoding="utf-8").splitlines()
    ]
    for event_type in (
        "context_compaction_started",
        "context_compaction_completed",
        "context_usage_updated",
    ):
        assert sum(record["event"] == event_type for record in records) == 1
    assert runtime.session["history"][0]["content"] == "old"
    assert runtime.session["history"][1]["content"] == "new"


@pytest.mark.asyncio
async def test_compaction_failure_keeps_canonical_and_tool_loop_never_recompacts(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("hello", encoding="utf-8")
    model = RecordingModel(
        [
            '<tool>{"name":"read_file","args":{"path":"README.md"}}</tool>',
            "<final>done</final>",
        ]
    )
    controller = LifecycleController(applied=False)
    controller.model = model
    runtime = CodingRuntime(
        session_id="s-compact-order",
        workspace_root=workspace,
        model=model,
        storage_root=tmp_path / ".coding",
        session_state={
            "id": "s-compact-order",
            "workspace_root": str(workspace),
            "history": [{"role": "user", "content": "canonical-old"}],
        },
        context_controller=controller,  # type: ignore[arg-type]
        checkpoint_anchor_key=b"k" * 32,
    )

    _ = [event async for event in runtime.run_turn("new")]

    assert controller.on_turn_start_calls == 1
    assert controller.before_model_calls == 2
    second_transcript = model.prompts[1].split("Transcript:\n", 1)[1]
    assert second_transcript.count("new") == 1
    assert second_transcript.index("user: new") < second_transcript.index("tool:read_file:")
    assert [item["content"] for item in runtime.session["history"][:2]] == [
        "canonical-old",
        "new",
    ]
    attempt = runtime.compaction_store.load_latest_attempt("s-compact-order")
    assert attempt is not None and attempt["status"] == "failed"


def test_resume_restores_only_authenticated_completed_checkpoint(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    storage = tmp_path / ".coding"
    key = b"r" * 32
    original = CodingRuntime(
        session_id="s-resume-context",
        workspace_root=workspace,
        model=RecordingModel([]),
        storage_root=storage,
        checkpoint_anchor_key=key,
    )
    for content in ("one", "two", "tail"):
        original._append_canonical_item({"role": "user", "content": content})
    checkpoint = _bind_checkpoint_to_evidence(
        _checkpoint("compact-resume", 1, 2),
        original.session["history"][:2],
    )
    projected = [
        {
            "role": "system",
            "kind": "compact_summary",
            "content": checkpoint.summary.render_for_prompt(),
        },
        deepcopy(original.session["history"][2]),
    ]
    result = CompactionResult(
        applied=True,
        projected_history=projected,
        checkpoint=checkpoint,
        before_tokens=300,
        after_tokens=80,
        archived_items=2,
        compaction_id="compact-resume",
    )
    original.compaction_store.begin(
        "s-resume-context", "compact-resume", {"trigger": "auto"}
    )
    original.compaction_store.complete(
        "s-resume-context", "compact-resume", result
    )
    original._save_session()

    resumed = CodingRuntime.resume(
        session_id="s-resume-context",
        model=RecordingModel([]),
        storage_root=storage,
        checkpoint_anchor_key=key,
    )

    assert resumed.session["context_state"]["resume_status"] == "checkpoint_restored"
    assert resumed._active_checkpoint == checkpoint
    assert resumed._active_projection[0]["kind"] == "compact_summary"
    assert resumed._active_projection[1]["content"] == "tail"
    assert [item["content"] for item in resumed.session["history"]] == [
        "one",
        "two",
        "tail",
    ]

    no_key = CodingRuntime.resume(
        session_id="s-resume-context",
        model=RecordingModel([]),
        storage_root=storage,
    )
    assert no_key.session["context_state"]["resume_status"] == "disabled_missing_anchor_key"
    assert no_key._active_checkpoint is None
    assert no_key._active_projection == no_key.session["history"]

    with sqlite3.connect(original.transcript_store.path) as connection:
        connection.execute(
            "UPDATE transcript SET content = ? WHERE sequence = 1", ("tampered",)
        )
    replaced = CodingRuntime.resume(
        session_id="s-resume-context",
        model=RecordingModel([]),
        storage_root=storage,
        checkpoint_anchor_key=key,
    )
    assert replaced.session["context_state"]["resume_status"] == "canonical_fallback"
    assert replaced._active_checkpoint is None

    with sqlite3.connect(original.transcript_store.path) as connection:
        connection.execute("UPDATE transcript SET content = ? WHERE sequence = 1", ("one",))
        connection.execute("DELETE FROM transcript WHERE sequence = 2")
    truncated = CodingRuntime.resume(
        session_id="s-resume-context",
        model=RecordingModel([]),
        storage_root=storage,
        checkpoint_anchor_key=key,
    )
    assert truncated.session["context_state"]["resume_status"] == "canonical_fallback"
    assert truncated._active_checkpoint is None


@pytest.mark.asyncio
async def test_runtime_persists_structured_tool_evidence(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("hello", encoding="utf-8")
    model = RecordingModel(
        [
            '<tool>{"name":"read_file","args":{"path":"README.md"}}</tool>',
            "<final>done</final>",
        ]
    )
    runtime = CodingRuntime(
        session_id="s-tool-evidence",
        workspace_root=workspace,
        model=model,
        storage_root=tmp_path / ".coding",
    )

    _ = [event async for event in runtime.run_turn("read it")]

    tool = next(item for item in runtime.transcript_store.read_all() if item.role == "tool")
    assert tool.name == "read_file"
    assert dict(tool.args) == {"path": "README.md", "start": 1, "end": 200}
    assert tool.run_id
    assert tool.turn_id


@pytest.mark.asyncio
async def test_runtime_persists_model_error_as_canonical_evidence(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = CodingRuntime(
        session_id="s-model-error",
        workspace_root=workspace,
        model=FailingModel(),
        storage_root=tmp_path / ".coding",
    )

    events = [event async for event in runtime.run_turn("hello")]

    assert any(event["type"] == "error" for event in events)
    transcript = runtime.transcript_store.read_all()
    assert [(item.role, item.is_error) for item in transcript] == [
        ("user", False),
        ("assistant", True),
    ]
    assert "model unavailable" not in transcript[-1].content


@pytest.mark.asyncio
async def test_context_lifecycle_persistence_failure_closes_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    model = RecordingModel(["<final>must not run</final>"])
    controller = LifecycleController(applied=True)
    controller.model = model
    runtime = CodingRuntime(
        session_id="s-context-persistence-failure",
        workspace_root=workspace,
        model=model,
        storage_root=tmp_path / ".coding",
        context_controller=controller,  # type: ignore[arg-type]
        checkpoint_anchor_key=b"k" * 32,
    )

    def fail_begin(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise OSError("provider-secret-token")

    monkeypatch.setattr(runtime.compaction_store, "begin", fail_begin)

    events = [event async for event in runtime.run_turn("new")]

    assert [event["type"] for event in events] == [
        "error",
        "run_finished",
        "turn_finished",
    ]
    assert events[1]["status"] == "error"
    assert "provider-secret-token" not in events[0]["message"]
    assert runtime.run_store.run_status(events[0]["run_id"]) == "error"
    assert runtime.active_run_id is None
    assert model.prompts == []


@pytest.mark.asyncio
async def test_runtime_disconnect_releases_lease_without_compaction(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = CodingRuntime(
        session_id="s-close",
        workspace_root=workspace,
        model=RecordingModel(["<final>done</final>"]),
        storage_root=tmp_path / ".coding",
        context_policy=ContextPolicy(
            context_window_tokens=100_000,
            output_reserve_tokens=10_000,
        ),
        checkpoint_anchor_key=b"k" * 32,
    )
    stream = runtime.run_turn("hello")
    first = await anext(stream)
    assert first["type"] in {"context_usage_updated", "turn_started"}

    await stream.aclose()

    assert runtime.active_run_id is None
    assert runtime.compaction_store.load_latest_attempt("s-close") is None


def test_request_stop_without_run_id_is_legacy_only_but_matching_is_strict(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = CodingRuntime(
        session_id="s-stop",
        workspace_root=workspace,
        model=RecordingModel([]),
        storage_root=tmp_path / ".coding",
    )
    runtime.active_run_id = "run-current"

    assert runtime.request_stop(run_id="run-stale") is False
    assert runtime.stop_requested is False
    assert runtime.request_stop(run_id="run-current") is True


@pytest.mark.asyncio
async def test_manual_compact_rejects_active_run_and_unconfigured_window(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = CodingRuntime(
        session_id="s-manual",
        workspace_root=workspace,
        model=RecordingModel([]),
        storage_root=tmp_path / ".coding",
    )
    with pytest.raises(ValueError, match="context window is not configured"):
        await runtime.manual_compact()

    configured = CodingRuntime(
        session_id="s-manual-configured",
        workspace_root=workspace,
        model=RecordingModel([]),
        storage_root=tmp_path / ".coding-2",
        context_policy=ContextPolicy(
            context_window_tokens=100_000,
            output_reserve_tokens=10_000,
        ),
        checkpoint_anchor_key=b"k" * 32,
    )
    configured.active_run_id = "run-active"
    with pytest.raises(Exception, match="active run"):
        await configured.manual_compact()


@pytest.mark.asyncio
async def test_manual_compact_mutex_rejects_loser_and_failure_closes_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = CodingRuntime(
        session_id="s-manual-race",
        workspace_root=workspace,
        model=RecordingModel([]),
        storage_root=tmp_path / ".coding",
        context_policy=ContextPolicy(
            context_window_tokens=100_000, output_reserve_tokens=10_000
        ),
        checkpoint_anchor_key=b"m" * 32,
    )
    entered = asyncio.Event()
    release = asyncio.Event()

    async def fail_compaction(*args: Any, **kwargs: Any) -> CompactionResult:
        del args, kwargs
        entered.set()
        await release.wait()
        raise RuntimeError("summarizer-secret")

    assert runtime.context_controller is not None
    monkeypatch.setattr(runtime.context_controller, "manual_compact", fail_compaction)
    winner = asyncio.create_task(runtime.manual_compact("first"))
    await entered.wait()

    with pytest.raises(ContextBusyError, match="context operation"):
        await runtime.manual_compact("loser")
    attempts = list(
        (runtime.compaction_store._root / "evidence" / "s-manual-race" / "compactions").glob(
            "*.json"
        )
    )
    assert len(attempts) == 1

    release.set()
    with pytest.raises(RuntimeError, match="manual compaction failed"):
        await winner
    attempt = runtime.compaction_store.load_latest_attempt("s-manual-race")
    assert attempt is not None and attempt["status"] == "failed"
    assert "summarizer-secret" not in json.dumps(attempt)
    assert runtime._context_operation_lock.locked() is False


def test_switch_model_rebuilds_or_disables_context_controller(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    registry = ModelCapabilityRegistry(
        {
            "model-a": {"context_window_tokens": 100_000, "output_reserve_tokens": 10_000},
            "model-b": {"context_window_tokens": 200_000, "output_reserve_tokens": 20_000},
        }
    )
    model_a = RecordingModel([])
    runtime = CodingRuntime(
        session_id="s-switch-context",
        workspace_root=workspace,
        model=model_a,
        storage_root=tmp_path / ".coding",
        model_capabilities=registry,
    )
    assert runtime.context_controller is None

    model_b = RecordingModel([])
    runtime.switch_model("model-b", lambda: model_b)
    assert runtime.context_policy is not None
    assert runtime.context_policy.context_window_tokens == 200_000
    assert runtime.context_controller is not None
    assert runtime.context_controller.counter.model is model_b
    assert runtime.context_controller.compactor.summarizer.model is model_b

    runtime.switch_model("unknown", lambda: RecordingModel([]))
    assert runtime.context_policy is None
    assert runtime.context_controller is None

    replacement_a = RecordingModel([])
    runtime.switch_model("model-a", lambda: replacement_a)
    assert runtime.context_policy is not None
    assert runtime.context_policy.context_window_tokens == 100_000
    assert runtime.context_controller is not None
    assert runtime.context_controller.counter.model is replacement_a


def test_legacy_backfill_failure_is_atomic_and_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    storage = tmp_path / ".coding"
    state = {
        "id": "s-backfill-atomic",
        "workspace_root": str(workspace),
        "history": [
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "two"},
        ],
    }
    original = TranscriptStore.append_many

    def fail_backfill(self: TranscriptStore, items: Any) -> list[int]:
        del self, items
        raise OSError("sqlite unavailable")

    monkeypatch.setattr(TranscriptStore, "append_many", fail_backfill)
    with pytest.raises(OSError, match="sqlite unavailable"):
        CodingRuntime(
            session_id="s-backfill-atomic",
            workspace_root=workspace,
            model=RecordingModel([]),
            storage_root=storage,
            session_state=state,
        )
    assert state["history"] == [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "two"},
    ]

    monkeypatch.setattr(TranscriptStore, "append_many", original)
    retried = CodingRuntime(
        session_id="s-backfill-atomic",
        workspace_root=workspace,
        model=RecordingModel([]),
        storage_root=storage,
        session_state=state,
    )
    assert [item.content for item in retried.transcript_store.read_all()] == ["one", "two"]
