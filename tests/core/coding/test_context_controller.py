from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from core.coding.context.budget import ContextPolicy, TokenCount, TokenCounter
from core.coding.context.compact import CompactManager
from core.coding.context.controller import ContextBusyError, ContextController, PreparedContext
from core.coding.context.manager import ContextManager
from core.coding.context.model_capabilities import ModelCapabilityRegistry
from core.coding.context.projection import ContextProjector
from core.coding.context.summarizer import StructuredSummarizer
from core.coding.context.summary import CompactionResult


class LengthCounter(TokenCounter):
    def count(self, text: str) -> TokenCount:
        return TokenCount(tokens=len(text), estimated=False)


class RecordingRenderer:
    def __init__(self) -> None:
        self.calls: list[tuple[list[dict[str, Any]], str]] = []

    def __call__(self, history: list[dict[str, Any]], user_message: str) -> str:
        self.calls.append((deepcopy(history), user_message))
        return json.dumps(history, sort_keys=True) + user_message


class RecordingCompactor:
    def __init__(self, result: CompactionResult) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def compact(self, history: list[dict[str, Any]], **kwargs: Any) -> CompactionResult:
        self.calls.append({"history": deepcopy(history), **kwargs})
        return self.result


class LifecycleSink:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def __call__(self, event: Any, result: CompactionResult | None) -> None:
        del result
        self.events.append(event)


def _policy() -> ContextPolicy:
    return ContextPolicy(context_window_tokens=300, output_reserve_tokens=100)


def _result(history: list[dict[str, Any]], *, applied: bool = True) -> CompactionResult:
    projected = [{"role": "system", "kind": "compact_summary", "content": "short"}]
    return CompactionResult(
        applied=applied,
        projected_history=projected if applied else deepcopy(history),
        checkpoint=None,
        before_tokens=180,
        after_tokens=40 if applied else 180,
        archived_items=3 if applied else 0,
        reason="" if applied else "summarizer_failed",
        compaction_id="ignored-manager-id",
        trigger="auto",
        retryable=not applied,
    )


async def test_turn_start_compacts_before_model_and_recounts_with_same_attempt_id() -> None:
    history = [{"role": "user", "content": "x" * 140}]
    compactor = RecordingCompactor(_result(history))
    renderer = RecordingRenderer()
    sink = LifecycleSink()
    controller = ContextController(
        session_id="s1",
        policy=_policy(),
        counter=LengthCounter(),
        projector=ContextProjector(),
        compactor=compactor,  # type: ignore[arg-type]
        renderer=renderer,
        active_run_id=lambda: None,
        lifecycle_sink=sink,
    )

    prepared = await controller.on_turn_start(
        history, "current", "run-1", previous_checkpoint=None, transcript_range=(4, 4)
    )

    assert prepared.allow_model_request is True
    assert prepared.compaction_result is compactor.result
    assert compactor.calls[0]["history"] == history
    assert compactor.calls[0]["session_id"] == "s1"
    assert compactor.calls[0]["previous_checkpoint"] is None
    assert compactor.calls[0]["transcript_range"] == (4, 4)
    ids = {event.compaction_id for event in prepared.events if hasattr(event, "compaction_id")}
    assert ids == {compactor.calls[0]["compaction_id"]}
    assert [event.type for event in prepared.events] == [
        "context_compaction_started",
        "context_compaction_completed",
        "context_usage_updated",
    ]
    assert [event.type for event in sink.events] == [
        "context_compaction_started",
        "context_compaction_completed",
    ]
    assert prepared.events[1].saved_ratio == pytest.approx((180 - 40) / 180)
    assert renderer.calls[-1][1] == "current"
    assert all(item.get("content") != "current" for item in history)


async def test_compaction_failure_preserves_bounded_projection_and_emergency_stops() -> None:
    history = [
        {
            "role": "tool",
            "name": "read_file",
            "args": {"path": "same.py"},
            "content": "x" * 20_000,
            "artifact_ref": f"artifact-{index}",
        }
        for index in range(5)
    ]
    original = deepcopy(history)
    compactor = RecordingCompactor(_result(history, applied=False))
    controller = ContextController(
        session_id="s1",
        policy=_policy(),
        counter=LengthCounter(),
        projector=ContextProjector(),
        compactor=compactor,  # type: ignore[arg-type]
        renderer=RecordingRenderer(),
        active_run_id=lambda: None,
    )

    prepared = await controller.on_turn_start(history, "current", "run-1")

    assert prepared.projected_history != history
    assert prepared.projected_history is not history
    assert history == original
    assert all(len(str(item["content"])) <= 15_000 for item in prepared.projected_history)
    assert prepared.allow_model_request is False
    assert prepared.events[1].type == "context_compaction_failed"
    assert prepared.events[1].preserved_original is True


async def test_started_sink_runs_before_compactor_and_terminal_sink_after_result() -> None:
    order: list[str] = []
    history = [{"role": "user", "content": "x" * 180}]

    class OrderedCompactor(RecordingCompactor):
        async def compact(self, history: list[dict[str, Any]], **kwargs: Any) -> CompactionResult:
            assert order == ["context_compaction_started"]
            order.append("compactor")
            return await super().compact(history, **kwargs)

    async def sink(event: Any, result: CompactionResult | None) -> None:
        if event.type == "context_compaction_started":
            assert result is None
        else:
            assert result is compactor.result
        order.append(event.type)

    compactor = OrderedCompactor(_result(history))
    controller = ContextController(
        session_id="s1",
        policy=_policy(),
        counter=LengthCounter(),
        projector=ContextProjector(),
        compactor=compactor,
        renderer=RecordingRenderer(),
        lifecycle_sink=sink,
    )

    await controller.on_turn_start(history, "current", "run-1")
    assert order == ["context_compaction_started", "compactor", "context_compaction_completed"]


async def test_started_sink_failure_is_fail_closed_before_compactor() -> None:
    history = [{"role": "user", "content": "x" * 180}]
    compactor = RecordingCompactor(_result(history))

    async def broken_sink(event: Any, result: CompactionResult | None) -> None:
        del event, result
        raise OSError("database secret")

    controller = ContextController(
        session_id="s1",
        policy=_policy(),
        counter=LengthCounter(),
        projector=ContextProjector(),
        compactor=compactor,
        renderer=RecordingRenderer(),
        lifecycle_sink=broken_sink,
    )

    with pytest.raises(RuntimeError, match="context lifecycle sink failed") as exc_info:
        await controller.on_turn_start(history, "current", "run-1")
    assert "secret" not in str(exc_info.value)
    assert compactor.calls == []


async def test_terminal_sink_failure_surfaces_after_compaction() -> None:
    history = [{"role": "user", "content": "x" * 180}]
    compactor = RecordingCompactor(_result(history))
    calls = 0

    async def broken_terminal_sink(event: Any, result: CompactionResult | None) -> None:
        del event, result
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("database unavailable")

    controller = ContextController(
        session_id="s1",
        policy=_policy(),
        counter=LengthCounter(),
        projector=ContextProjector(),
        compactor=compactor,
        renderer=RecordingRenderer(),
        lifecycle_sink=broken_terminal_sink,
    )

    with pytest.raises(RuntimeError, match="context lifecycle sink failed"):
        await controller.on_turn_start(history, "current", "run-1")
    assert len(compactor.calls) == 1


def test_before_model_request_projects_twice_but_never_semantically_compacts() -> None:
    history = [{"role": "tool", "name": "read_file", "content": "x" * 300}]
    compactor = RecordingCompactor(_result(history))
    controller = ContextController(
        session_id="s1",
        policy=_policy(),
        counter=LengthCounter(),
        projector=ContextProjector(),
        compactor=compactor,  # type: ignore[arg-type]
        renderer=RecordingRenderer(),
        active_run_id=lambda: None,
    )

    prepared = controller.before_model_request(history, user_message="current", run_id="run-1")

    assert compactor.calls == []
    assert prepared.allow_model_request is False
    assert prepared.usage.effective_limit_tokens == 200
    assert prepared.events[-1].type == "context_usage_updated"


def test_configured_controller_is_compactable_at_normal_pressure() -> None:
    controller = ContextController(
        session_id="s1",
        policy=_policy(),
        counter=LengthCounter(),
        projector=ContextProjector(),
        compactor=RecordingCompactor(_result([])),
        renderer=RecordingRenderer(),
    )
    prepared = controller.before_model_request([], user_message="hi", run_id="run-1")
    assert prepared.usage.level == "normal"
    assert prepared.events[-1].compactable is True


async def test_context_manager_renders_current_request_exactly_once_and_not_in_history() -> None:
    current = "UNIQUE-CURRENT-REQUEST"
    manager = ContextManager(total_budget=100_000)
    rendered_prompts: list[str] = []

    def render(history: list[dict[str, Any]], user_message: str) -> str:
        prompt, _ = manager.build(user_message=user_message, history=history)
        rendered_prompts.append(prompt)
        return prompt

    history = [{"role": "user", "content": "prior request", "message_id": "m-old"}]
    controller = ContextController(
        session_id="s1",
        policy=ContextPolicy(200_000, 20_000),
        counter=LengthCounter(),
        projector=ContextProjector(),
        compactor=RecordingCompactor(_result(history)),
        renderer=render,
    )

    prepared = await controller.on_turn_start(
        history, current, "run-1", current_message_id="m-current"
    )
    assert all(prompt.count(current) == 1 for prompt in rendered_prompts)
    assert all(item.get("content") != current for item in prepared.projected_history)


async def test_current_message_id_is_removed_from_controller_history() -> None:
    current = {"role": "user", "content": "current", "message_id": "m-current"}
    history = [{"role": "user", "content": "older", "message_id": "m-old"}, current]
    controller = ContextController(
        session_id="s1",
        policy=_policy(),
        counter=LengthCounter(),
        projector=ContextProjector(),
        compactor=RecordingCompactor(_result(history)),
        renderer=RecordingRenderer(),
    )
    prepared = await controller.on_turn_start(
        history, "current", "run-1", current_message_id="m-current"
    )
    assert [item["message_id"] for item in prepared.projected_history] == ["m-old"]


def test_before_model_request_applies_same_current_message_identity_filter() -> None:
    controller = ContextController(
        session_id="s1",
        policy=_policy(),
        counter=LengthCounter(),
        projector=ContextProjector(),
        compactor=RecordingCompactor(_result([])),
        renderer=RecordingRenderer(),
    )
    prepared = controller.before_model_request(
        [{"role": "user", "content": "current", "message_id": "m-current"}],
        user_message="current",
        run_id="run-1",
        current_message_id="m-current",
    )
    assert prepared.projected_history == []


async def test_manual_compact_rejects_an_active_run() -> None:
    history: list[dict[str, Any]] = []
    controller = ContextController(
        session_id="s1",
        policy=_policy(),
        counter=LengthCounter(),
        projector=ContextProjector(),
        compactor=RecordingCompactor(_result(history)),  # type: ignore[arg-type]
        renderer=RecordingRenderer(),
        history_provider=lambda: history,
        active_run_id=lambda: "run-1",
    )

    with pytest.raises(ContextBusyError):
        await controller.manual_compact()


def test_prepared_context_is_frozen_and_deep_copies_history() -> None:
    source = [{"role": "user", "content": "hello"}]
    prepared = PreparedContext.create(
        projected_history=source,
        usage=_policy().usage(1),
        allow_model_request=True,
    )
    source[0]["content"] = "changed"

    assert prepared.projected_history[0]["content"] == "hello"
    with pytest.raises(FrozenInstanceError):
        prepared.allow_model_request = False  # type: ignore[misc]


def test_prepared_context_direct_constructor_deep_copies_history() -> None:
    source = [{"role": "user", "content": "hello"}]
    prepared = PreparedContext(source, _policy().usage(1), True)
    source[0]["content"] = "changed"

    assert prepared.projected_history[0]["content"] == "hello"


async def test_compact_manager_rejects_unsafe_preallocated_attempt_id() -> None:
    class NeverSummarizer:
        async def summarize(self, **kwargs: Any) -> dict[str, Any]:
            raise AssertionError("must validate before summarization")

    manager = CompactManager(summarizer=NeverSummarizer(), policy=_policy())
    with pytest.raises(ValueError, match="compaction_id"):
        await manager.compact([], session_id="s1", compaction_id="../cmp")


def test_model_registry_resolves_explicit_mapping_and_model_attributes() -> None:
    registry = ModelCapabilityRegistry(
        {"model-a": {"context_window_tokens": 200_000, "output_reserve_tokens": 20_000}}
    )

    assert registry.resolve("model-a") == ContextPolicy(200_000, 20_000)
    assert registry.resolve("unknown") is None

    class ExplicitModel:
        context_window_tokens = 100_000
        output_reserve_tokens = 10_000

    assert ModelCapabilityRegistry.from_model(ExplicitModel()) == ContextPolicy(100_000, 10_000)


@pytest.mark.parametrize(
    "payload",
    ["[]", '{"m":0}', '{"m":{"context_window_tokens":10,"output_reserve_tokens":10}}'],
)
def test_model_registry_rejects_invalid_environment(
    monkeypatch: pytest.MonkeyPatch, payload: str
) -> None:
    monkeypatch.setenv("SAGE_MODEL_CONTEXT_WINDOWS", payload)
    with pytest.raises(ValueError):
        ModelCapabilityRegistry.from_env()


def test_model_registry_loads_integer_environment_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SAGE_MODEL_CONTEXT_WINDOWS", '{"model-a":200000}')
    policy = ModelCapabilityRegistry.from_env().resolve("model-a")
    assert policy is not None
    assert policy.context_window_tokens == 200_000
    assert policy.output_reserve_tokens < policy.context_window_tokens


@pytest.mark.parametrize(
    "payload",
    [
        '{"model-a":200000,"model-a":300000}',
        '{"model-a":99999999999999999999}',
        "{" + ",".join(f'"m{i}":200000' for i in range(257)) + "}",
        '{"model-a":200000,"padding":"' + ("x" * 70_000) + '"}',
    ],
)
def test_model_registry_rejects_ambiguous_or_oversized_environment(payload: str) -> None:
    with pytest.raises(ValueError):
        ModelCapabilityRegistry.from_env(payload)


class CompleteModel:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []
        self.max_tokens: list[int] = []

    async def complete(self, prompt: str, *, max_tokens: int) -> str:
        self.prompts.append(prompt)
        self.max_tokens.append(max_tokens)
        return self.response


def _summary_payload() -> dict[str, Any]:
    return {"goal": "ship", "source_transcript_range": [1, 2]}


async def test_structured_summarizer_uses_canonical_json_and_parses_fence() -> None:
    model = CompleteModel("```json\n" + json.dumps(_summary_payload()) + "\n```")
    summarizer = StructuredSummarizer(model)

    result = await summarizer.summarize(
        archived_history=[{"role": "user", "content": "old"}],
        previous_summary=None,
        focus="current",
        max_tokens=100,
        source_transcript_range=(1, 2),
        repair_feedback="bad schema",
    )

    assert result == _summary_payload()
    request = json.loads(model.prompts[0].split("\n", 1)[1])
    assert set(request) == {"archived", "previous", "focus", "range", "repair", "max_tokens"}
    assert request["focus"] == "current"
    assert request["repair"] == "bad schema"
    assert model.max_tokens == [100]


@pytest.mark.parametrize("response", ['before {"goal":"x"}', '{"a":1}{"b":2}'])
async def test_structured_summarizer_rejects_prose_and_repeated_objects(response: str) -> None:
    with pytest.raises(ValueError, match="JSON object"):
        await StructuredSummarizer(CompleteModel(response)).summarize(
            archived_history=[],
            previous_summary=None,
            focus="",
            max_tokens=1,
            source_transcript_range=(0, 0),
            repair_feedback=None,
        )


async def test_structured_summarizer_times_out_without_leaking_model_error() -> None:
    class SlowModel:
        async def complete(self, prompt: str, *, max_tokens: int) -> str:
            await asyncio.sleep(1)
            return "{}"

    with pytest.raises(TimeoutError, match="timed out"):
        await StructuredSummarizer(SlowModel(), timeout_seconds=0.01).summarize(
            archived_history=[],
            previous_summary=None,
            focus="",
            max_tokens=1,
            source_transcript_range=(0, 0),
            repair_feedback=None,
        )


async def test_structured_summarizer_timeout_is_a_hard_deadline() -> None:
    class CancellationResistantModel:
        async def complete(self, prompt: str, *, max_tokens: int) -> str:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                await asyncio.sleep(10)
            return "{}"

    loop = asyncio.get_running_loop()
    started = loop.time()
    with pytest.raises(TimeoutError, match="timed out"):
        await StructuredSummarizer(CancellationResistantModel(), timeout_seconds=0.01).summarize(
            archived_history=[],
            previous_summary=None,
            focus="",
            max_tokens=1,
            source_transcript_range=(0, 0),
            repair_feedback=None,
        )
    assert loop.time() - started < 0.2


async def test_structured_summarizer_propagates_cancellation() -> None:
    class CancelModel:
        async def complete(self, prompt: str, *, max_tokens: int) -> str:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await StructuredSummarizer(CancelModel()).summarize(
            archived_history=[],
            previous_summary=None,
            focus="",
            max_tokens=1,
            source_transcript_range=(0, 0),
            repair_feedback=None,
        )


async def test_structured_summarizer_rejects_duplicate_json_keys() -> None:
    model = CompleteModel('{"goal":"first","goal":"second"}')
    with pytest.raises(ValueError, match="duplicate"):
        await StructuredSummarizer(model).summarize(
            archived_history=[],
            previous_summary=None,
            focus="",
            max_tokens=100,
            source_transcript_range=(0, 0),
            repair_feedback=None,
        )


async def test_structured_summarizer_rejects_oversized_archived_input_before_model() -> None:
    model = CompleteModel("{}")
    with pytest.raises(ValueError, match="archived"):
        await StructuredSummarizer(model).summarize(
            archived_history=[{"role": "tool", "content": "x" * 1_100_000}],
            previous_summary=None,
            focus="",
            max_tokens=100,
            source_transcript_range=(0, 0),
            repair_feedback=None,
        )
    assert model.prompts == []


async def test_structured_summarizer_rejects_oversized_raw_output() -> None:
    model = CompleteModel('{"goal":"' + ("x" * 300_000) + '"}')
    with pytest.raises(ValueError, match="output"):
        await StructuredSummarizer(model).summarize(
            archived_history=[],
            previous_summary=None,
            focus="",
            max_tokens=20_000,
            source_transcript_range=(0, 0),
            repair_feedback=None,
        )


async def test_structured_summarizer_supports_max_completion_tokens_provider() -> None:
    class CompletionTokensModel:
        def __init__(self) -> None:
            self.received: int | None = None

        async def complete(self, prompt: str, *, max_completion_tokens: int) -> str:
            self.received = max_completion_tokens
            return json.dumps(_summary_payload())

    model = CompletionTokensModel()
    result = await StructuredSummarizer(model).summarize(
        archived_history=[],
        previous_summary=None,
        focus="",
        max_tokens=123,
        source_transcript_range=(0, 0),
        repair_feedback=None,
    )
    assert result == _summary_payload()
    assert model.received == 123


async def test_structured_summarizer_caps_combined_request_input() -> None:
    model = CompleteModel("{}")
    with pytest.raises(ValueError, match="request"):
        await StructuredSummarizer(model).summarize(
            archived_history=[],
            previous_summary=None,
            focus="f" * 1_100_000,
            max_tokens=100,
            source_transcript_range=(0, 0),
            repair_feedback="r" * 1_100_000,
        )
    assert model.prompts == []
