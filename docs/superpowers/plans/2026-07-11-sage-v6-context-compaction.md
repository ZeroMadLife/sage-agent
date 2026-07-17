# Sage V6.6 Context Compaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace character clipping with recoverable token-aware context projection, large-result artifacts, and safe turn-boundary compaction.

**Architecture:** Canonical transcript and tool artifacts remain append-only evidence. A `ContextController` creates a bounded model projection, applies deterministic pressure stages before each request, and invokes a validated structured summarizer only at a new user-turn boundary.

**Tech Stack:** Python 3.11, Pydantic v2, FastAPI, pytest, existing LangChain model clients, atomic JSON/JSONL files.

---

## File Structure

- Create `core/coding/context/budget.py`: model token window, counter fallback, and pressure stage.
- Create `core/coding/context/projection.py`: immutable active-history projection and tool-result reduction.
- Create `core/coding/context/summary.py`: structured compaction summary and validation.
- Replace internals of `core/coding/context/compact.py`: async compactor, repair, checkpoint, and circuit breaker.
- Create `core/coding/context/controller.py`: turn-boundary orchestration and usage snapshots.
- Create `core/coding/persistence/transcript_store.py`: append-only canonical session transcript.
- Create `core/coding/persistence/tool_result_store.py`: session/run-scoped full tool output artifacts.
- Modify `core/coding/context/manager.py`: render a pre-budgeted projection without clipping message boundaries.
- Integration Agent only: modify `core/coding/runtime.py`, `core/coding/engine/engine.py`, `core/coding/engine/events.py`, `api/coding.py`, and `api/schemas.py`.
- Create focused tests named in the tasks below.

### Task 1: Token Budget and Pressure Contract

**Files:**
- Create: `core/coding/context/budget.py`
- Create: `tests/core/coding/test_context_budget.py`

- [ ] **Step 1: Write the failing budget tests**

Create `tests/core/coding/test_context_budget.py`:

```python
"""Token-aware context budget tests."""

import pytest

from core.coding.context.budget import ContextPolicy, TokenCounter


def test_effective_limit_subtracts_output_reserve() -> None:
    policy = ContextPolicy(context_window_tokens=200_000, output_reserve_tokens=20_000)
    assert policy.effective_limit_tokens == 180_000


@pytest.mark.parametrize(
    ("tokens", "level"),
    [(89_999, "normal"), (90_000, "budget"), (108_000, "snip"),
     (117_000, "compact"), (126_000, "high"), (153_000, "emergency")],
)
def test_pressure_levels_use_effective_limit(tokens: int, level: str) -> None:
    policy = ContextPolicy(context_window_tokens=200_000, output_reserve_tokens=20_000)
    assert policy.usage(tokens).level == level


def test_invalid_threshold_order_is_rejected() -> None:
    with pytest.raises(ValueError, match="thresholds"):
        ContextPolicy(
            context_window_tokens=200_000,
            output_reserve_tokens=20_000,
            compact_ratio=0.40,
        )


def test_counter_fallback_is_conservative_and_labeled() -> None:
    count = TokenCounter().count("中文 context")
    assert count.tokens > 0
    assert count.estimated is True
```

- [ ] **Step 2: Run the tests and verify failure**

```bash
pytest tests/core/coding/test_context_budget.py -q
```

Expected: FAIL because `core.coding.context.budget` does not exist.

- [ ] **Step 3: Implement the policy and counter**

Create `core/coding/context/budget.py`:

```python
"""Model-window token budget and pressure stages."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

ContextLevel = Literal["normal", "budget", "snip", "compact", "high", "emergency"]


@dataclass(frozen=True)
class TokenCount:
    tokens: int
    estimated: bool


@dataclass(frozen=True)
class ContextUsage:
    used_tokens: int
    effective_limit_tokens: int
    usage_ratio: float
    level: ContextLevel
    estimated: bool


@dataclass(frozen=True)
class ContextPolicy:
    context_window_tokens: int
    output_reserve_tokens: int = 20_000
    budget_ratio: float = 0.50
    snip_ratio: float = 0.60
    compact_ratio: float = 0.65
    high_ratio: float = 0.70
    cache_override_ratio: float = 0.75
    emergency_ratio: float = 0.85

    def __post_init__(self) -> None:
        thresholds = (
            self.budget_ratio,
            self.snip_ratio,
            self.compact_ratio,
            self.high_ratio,
            self.cache_override_ratio,
            self.emergency_ratio,
        )
        if self.context_window_tokens <= self.output_reserve_tokens:
            raise ValueError("output reserve must be smaller than context window")
        if thresholds != tuple(sorted(thresholds)) or not 0 < thresholds[0] < thresholds[-1] < 1:
            raise ValueError("context thresholds must be ordered between zero and one")

    @property
    def effective_limit_tokens(self) -> int:
        return self.context_window_tokens - self.output_reserve_tokens

    def usage(self, used_tokens: int, *, estimated: bool = True) -> ContextUsage:
        ratio = used_tokens / self.effective_limit_tokens
        level: ContextLevel = "normal"
        for threshold, candidate in (
            (self.budget_ratio, "budget"),
            (self.snip_ratio, "snip"),
            (self.compact_ratio, "compact"),
            (self.high_ratio, "high"),
            (self.emergency_ratio, "emergency"),
        ):
            if ratio >= threshold:
                level = candidate  # type: ignore[assignment]
        return ContextUsage(used_tokens, self.effective_limit_tokens, ratio, level, estimated)


class TokenCounter:
    def __init__(self, model: Any | None = None) -> None:
        self.model = model

    def count(self, text: str) -> TokenCount:
        counter = getattr(self.model, "get_num_tokens", None)
        if callable(counter):
            try:
                return TokenCount(max(1, int(counter(text))), False)
            except Exception:
                pass
        return TokenCount(max(1, math.ceil(len(text.encode("utf-8")) / 4)), True)
```

- [ ] **Step 4: Run tests and commit**

```bash
pytest tests/core/coding/test_context_budget.py -q
git add core/coding/context/budget.py tests/core/coding/test_context_budget.py
git commit -m "feat(sage-v6): add token context policy"
```

Expected: all budget tests pass.

### Task 2: Canonical Transcript and Tool Result Artifacts

**Files:**
- Create: `core/coding/persistence/transcript_store.py`
- Create: `core/coding/persistence/tool_result_store.py`
- Create: `tests/core/coding/test_transcript_store.py`
- Create: `tests/core/coding/test_tool_result_store.py`

- [ ] **Step 1: Write failing persistence tests**

```python
def test_transcript_append_is_idempotent(tmp_path: Path) -> None:
    store = TranscriptStore(tmp_path, "s1")
    item = TranscriptItem(message_id="m1", role="user", content="hello")
    store.append(item)
    store.append(item)
    assert [entry.message_id for entry in store.read_all()] == ["m1"]


def test_large_tool_result_is_written_before_preview(tmp_path: Path) -> None:
    store = ToolResultStore(tmp_path, "s1", "run_1")
    result = store.archive("call_1", "x" * 20_000)
    assert result.artifact_path.is_file()
    assert result.artifact_path.read_text(encoding="utf-8") == "x" * 20_000
    assert result.preview.endswith("[full result: call_1]")
    assert len(result.preview) < 20_000
```

- [ ] **Step 2: Run and verify failure**

```bash
pytest tests/core/coding/test_transcript_store.py tests/core/coding/test_tool_result_store.py -q
```

Expected: FAIL because both stores are absent.

- [ ] **Step 3: Implement append-only transcript records**

Use this public contract in `transcript_store.py`:

```python
import json
import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class TranscriptItem:
    message_id: str
    role: str
    content: str
    run_id: str = ""
    turn_id: str = ""
    call_id: str = ""
    artifact_ref: str = ""
    created_at: str = ""


class TranscriptStore:
    def __init__(self, root: Path, session_id: str) -> None:
        if not session_id or "/" in session_id or "\\" in session_id:
            raise ValueError("invalid session id")
        self.path = root / "evidence" / session_id / "transcript.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._ids = {item.message_id for item in self.read_all()}

    def append(self, item: TranscriptItem) -> bool:
        with self._lock:
            if item.message_id in self._ids:
                return False
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(asdict(item), ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._ids.add(item.message_id)
            return True

    def read_all(self) -> list[TranscriptItem]:
        if not self.path.is_file():
            return []
        return [
            TranscriptItem(**json.loads(line))
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
```

`append()` must lock, scan existing message IDs once per process, append one JSON line, flush, and return `False` for a duplicate. It must never rewrite prior lines.

- [ ] **Step 4: Implement bounded tool artifacts**

Use this contract in `tool_result_store.py`:

```python
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ArchivedToolResult:
    artifact_ref: str
    artifact_path: Path
    preview: str
    original_chars: int
    truncated: bool


class ToolResultStore:
    PERSIST_THRESHOLD_BYTES = 16 * 1024
    PREVIEW_LINES = 200
    PREVIEW_CHARS = 12_000

    def __init__(self, root: Path, session_id: str, run_id: str) -> None:
        for value in (session_id, run_id):
            if not value or "/" in value or "\\" in value:
                raise ValueError("invalid evidence scope id")
        self.root = root / "evidence" / session_id / "runs" / run_id / "tool-results"

    def archive(self, call_id: str, content: str) -> ArchivedToolResult:
        if not call_id or "/" in call_id or "\\" in call_id:
            raise ValueError("invalid tool call id")
        path = self.root / f"{call_id}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(content, encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        lines = content.splitlines()
        line_preview = "\n".join(lines[:120] + lines[-80:])
        preview = line_preview[: self.PREVIEW_CHARS]
        truncated = preview != content
        if truncated:
            preview = f"{preview}\n[full result: {call_id}]"
        return ArchivedToolResult(call_id, path, preview, len(content), truncated)
```

Resolve the artifact path from safe IDs only, write through a temporary file and `os.replace`, and set file mode `0o600`. Preserve a short head and tail for error traces. Reject path separators in IDs.

- [ ] **Step 5: Run tests and commit**

```bash
pytest tests/core/coding/test_transcript_store.py tests/core/coding/test_tool_result_store.py -q
git add core/coding/persistence/transcript_store.py core/coding/persistence/tool_result_store.py tests/core/coding/test_transcript_store.py tests/core/coding/test_tool_result_store.py
git commit -m "feat(sage-v6): archive transcript and tool results"
```

### Task 3: Immutable Context Projection

**Files:**
- Create: `core/coding/context/projection.py`
- Create: `tests/core/coding/test_context_projection.py`
- Modify: `core/coding/context/manager.py`

- [ ] **Step 1: Write projection tests**

```python
def test_projection_never_mutates_history() -> None:
    history = fixture_history_with_repeated_reads()
    before = deepcopy(history)
    projected = ContextProjector().project(history, level="snip")
    assert history == before
    assert projected != history


def test_projection_keeps_latest_three_tool_results() -> None:
    projected = ContextProjector().project(fixture_history_with_five_tools(), level="high")
    assert [item["content"] for item in projected if item["role"] == "tool"][-3:] == [
        "result 3", "result 4", "result 5"
    ]


def test_context_manager_does_not_clip_current_request() -> None:
    request = "current:" + "x" * 2000
    prompt, metadata = ContextManager(total_budget=1000).build(request, history=[])
    assert request in prompt
    assert metadata["prompt_over_budget"] is True
```

- [ ] **Step 2: Run and verify failure**

```bash
pytest tests/core/coding/test_context_projection.py tests/core/coding/test_context_compact.py -q
```

Expected: new projection tests fail.

- [ ] **Step 3: Implement projector stages**

Create `ContextProjector.project(history, level)` as a pure function:

```python
class ContextProjector:
    def project(self, history: list[dict[str, Any]], level: ContextLevel) -> list[dict[str, Any]]:
        projected = deepcopy(history)
        cap = (
            50_000
            if level == "normal"
            else 30_000
            if level in {"budget", "snip", "compact"}
            else 15_000
        )
        tool_indexes = [i for i, item in enumerate(projected) if item.get("role") == "tool"]
        protected = set(tool_indexes[-3:])
        seen_reads: set[tuple[str, str]] = set()
        for index in reversed(tool_indexes):
            item = projected[index]
            signature = (str(item.get("name", "")), str(item.get("args", {}).get("path", "")))
            duplicate_read = signature[0] in {"read_file", "search", "list_files"} and signature in seen_reads
            if level in {"snip", "compact", "high", "emergency"} and index not in protected and duplicate_read:
                item["content"] = f"[older duplicate result removed; artifact={item.get('artifact_ref', '')}]"
            else:
                if signature[0] in {"read_file", "search", "list_files"}:
                    seen_reads.add(signature)
                item["content"] = _bounded_preview(str(item.get("content", "")), cap)
        return projected
```

Limit duplicate removal to read/search tools. Never clip user messages, assistant finals, system constraints, the current request, or compact summary objects.

- [ ] **Step 4: Make ContextManager reject unsafe over-budget input**

Remove final string-level clipping of current request and system prefix. `ContextManager` may report `prompt_over_budget=True`; `ContextController` decides whether to compact or stop before a model call.

- [ ] **Step 5: Run tests and commit**

```bash
pytest tests/core/coding/test_context_projection.py tests/core/coding/test_context_compact.py -q
git add core/coding/context/projection.py core/coding/context/manager.py tests/core/coding/test_context_projection.py
git commit -m "feat(sage-v6): add immutable context projection"
```

### Task 4: Structured Compactor and Circuit Breaker

**Files:**
- Create: `core/coding/context/summary.py`
- Modify: `core/coding/context/compact.py`
- Create: `tests/core/coding/test_context_compactor.py`

- [ ] **Step 1: Write failing compactor tests**

Cover these exact cases:

```text
test_compactor_preserves_recent_three_complete_turns
test_compactor_never_changes_source_history
test_compactor_updates_previous_summary_iteratively
test_invalid_summary_keeps_previous_checkpoint
test_summary_missing_todo_id_is_repaired_once
test_summary_failure_preserves_original_context
test_ineffective_summary_is_not_applied
test_second_ineffective_result_opens_circuit
test_success_resets_failure_counter
```

- [ ] **Step 2: Define the summary schema**

Create `core/coding/context/summary.py`:

```python
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field


class CompactionSummary(BaseModel):
    goal: str
    user_constraints: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    completed_work: list[str] = Field(default_factory=list)
    active_todos: list[str] = Field(default_factory=list)
    files_read: list[str] = Field(default_factory=list)
    files_modified: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    source_transcript_range: tuple[int, int]
    source_run_ids: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class CompactionCheckpoint:
    compaction_id: str
    transcript_start: int
    transcript_end: int
    summary: CompactionSummary
    summary_hash: str


@dataclass(frozen=True)
class CompactionResult:
    applied: bool
    projected_history: list[dict[str, Any]]
    checkpoint: CompactionCheckpoint | None
    before_tokens: int
    after_tokens: int
    archived_items: int
    reason: str = ""
```

Add `render_for_prompt()` with a fixed first line: `Historical handoff only; the latest user message always wins.`

- [ ] **Step 3: Implement async compaction**

`CompactManager.compact()` becomes async and receives a summarizer protocol, previous checkpoint, completed turns, policy, and transcript range. It must:

1. choose a tail using token budget and complete user turns;
2. call the summarizer at most once plus one repair;
3. validate referenced todo/path/artifact IDs against input evidence;
4. calculate before/after tokens and require at least 10% savings;
5. return a new projection and checkpoint without mutating input history;
6. return failure metadata with the original projection on any exception.

- [ ] **Step 4: Run tests and commit**

```bash
pytest tests/core/coding/test_context_compactor.py -q
git add core/coding/context/summary.py core/coding/context/compact.py tests/core/coding/test_context_compactor.py
git commit -m "feat(sage-v6): add safe structured compaction"
```

### Task 5: Runtime, Events, and Context API Integration

**Files:**
- Create: `core/coding/context/controller.py`
- Integration Agent modify: `core/coding/runtime.py`
- Integration Agent modify: `core/coding/engine/engine.py`
- Integration Agent modify: `core/coding/engine/events.py`
- Integration Agent modify: `api/coding.py`
- Integration Agent modify: `api/schemas.py`
- Create: `tests/core/coding/test_context_runtime.py`
- Create: `tests/api/test_coding_context_routes.py`

- [ ] **Step 1: Add failing runtime tests**

Cover:

```text
test_runtime_compacts_before_first_model_request
test_runtime_does_not_compact_mid_tool_loop
test_runtime_emergency_stops_before_next_model_call
test_compaction_failure_does_not_lose_history
test_disconnect_does_not_start_async_compaction_in_finally
test_resume_restores_compaction_checkpoint
test_context_usage_event_uses_effective_limit
test_manual_compact_rejects_active_run
```

- [ ] **Step 2: Implement the controller contract**

```python
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from core.coding.context.budget import ContextPolicy, ContextUsage, TokenCounter
from core.coding.context.compact import CompactManager
from core.coding.context.projection import ContextProjector
from core.coding.context.summary import CompactionResult


class ContextBusyError(RuntimeError):
    """Manual compaction was requested while a run owns the session."""


@dataclass(frozen=True)
class PreparedContext:
    projected_history: list[dict[str, Any]]
    usage: ContextUsage
    allow_model_request: bool


class ContextController:
    def __init__(
        self,
        *,
        policy: ContextPolicy,
        counter: TokenCounter,
        projector: ContextProjector,
        compactor: CompactManager,
        renderer: Callable[[list[dict[str, Any]], str], str],
        history: list[dict[str, Any]],
        active_run_id: Callable[[], str | None],
    ) -> None:
        self.policy = policy
        self.counter = counter
        self.projector = projector
        self.compactor = compactor
        self.renderer = renderer
        self.history = history
        self.active_run_id = active_run_id
        self.current_user_message = ""
        self.last_usage: ContextUsage | None = None

    async def on_turn_start(
        self,
        history: list[dict[str, Any]],
        user_message: str,
        run_id: str,
    ) -> PreparedContext:
        self.current_user_message = user_message
        projected = self.projector.project(history, level="normal")
        rendered = self.renderer(projected, user_message)
        token_count = self.counter.count(rendered)
        usage = self.policy.usage(token_count.tokens, estimated=token_count.estimated)
        if usage.level in {"compact", "high", "emergency"}:
            result = await self.compactor.compact(
                history=history,
                trigger="auto",
                focus=user_message,
            )
            if result.applied:
                projected = result.projected_history
                rendered = self.renderer(projected, user_message)
                token_count = self.counter.count(rendered)
                usage = self.policy.usage(token_count.tokens, estimated=token_count.estimated)
        self.last_usage = usage
        return PreparedContext(projected, usage, usage.level != "emergency")

    def before_model_request(
        self,
        history: list[dict[str, Any]],
        *,
        first_request_of_turn: bool,
    ) -> PreparedContext:
        level = self.last_usage.level if self.last_usage is not None else "normal"
        projected = self.projector.project(history, level=level)
        rendered = self.renderer(projected, self.current_user_message)
        token_count = self.counter.count(rendered)
        usage = self.policy.usage(token_count.tokens, estimated=token_count.estimated)
        self.last_usage = usage
        return PreparedContext(projected, usage, usage.level != "emergency")

    async def manual_compact(self, focus: str = "") -> CompactionResult:
        if self.active_run_id():
            raise ContextBusyError("active run")
        return await self.compactor.compact(
            history=self.history,
            trigger="manual",
            focus=focus,
        )
```

`PreparedContext` contains projected history, current usage, optional compaction events, and `allow_model_request`.

- [ ] **Step 3: Move current user append to Runtime**

Runtime appends and archives the current user item once before `Engine` starts. Change the Engine entrypoint to:

```python
async def run_turn(
    self,
    user_message: str,
    *,
    prepared_context: PreparedContext,
    append_user: bool = True,
    skill_prompt: str | None = None,
    memory_block: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    if append_user:
        self.history.append({"role": "user", "content": user_message, "created_at": now()})
    self.active_projection = prepared_context.projected_history
```

Runtime passes `append_user=False`. The prompt must contain the current request once.

- [ ] **Step 4: Add typed events and endpoints**

Implement the exact event names and fields from the approved design. Add:

```text
GET  /api/v1/coding/{session_id}/context
POST /api/v1/coding/{session_id}/context/compact
```

The POST body is `{ "focus": "optional text" }`. Return `409` for active run/compaction and `422` for an unconfigured model window.

- [ ] **Step 5: Run integration tests and commit**

```bash
pytest tests/core/coding/test_context_*.py tests/core/coding/test_engine.py tests/core/coding/test_runtime_run_lifecycle.py tests/api/test_coding_context_routes.py -q
git add core/coding api tests/core/coding tests/api/test_coding_context_routes.py
git commit -m "feat(sage-v6): integrate turn-boundary compaction"
```

### Task 6: Benchmark Context Continuity

**Files:**
- Modify: `evals/coding/scenarios.py`
- Modify: `evals/coding/assertions.py`
- Modify: `evals/coding/metrics.py`
- Modify: `tests/evals/test_benchmark.py`

- [ ] **Step 1: Add deterministic scenarios**

Add scripted scenarios for long-session continuation, large tool artifacts, compaction failure, tool-boundary preservation, and circuit breaker behavior. Use a deliberately small configured context window so tests remain fast.

- [ ] **Step 2: Add metrics**

```text
compaction_continuity_rate
transcript_recovery_rate
tool_artifact_recovery_rate
context_limit_violation_count
```

- [ ] **Step 3: Run benchmark tests**

```bash
pytest tests/evals/test_benchmark.py -q
python -m evals.coding.runner
```

Expected: all deterministic context scenarios pass, and no prompt exceeds the effective limit.

- [ ] **Step 4: Commit**

```bash
git add evals/coding tests/evals/test_benchmark.py
git commit -m "test(sage-v6): benchmark context continuity"
```
