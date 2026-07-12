# Sage V6.8 Dream Reflection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delegate memory reflection to a constrained child agent that can only generate persistent, evidence-backed proposals for explicit user review.

**Architecture:** Completed run evidence is converted into a bounded, untrusted `EvidenceBundle`. A tool-less `MemoryReflectionRunner` returns structured candidate changes; a deterministic policy filters them, and a revisioned proposal store supports edit, approve, reject, idempotency, and rollback.

**Tech Stack:** Python 3.11, Pydantic v2, asyncio, atomic JSON storage, FastAPI, pytest, existing model factory.

---

## File Structure

- Extend `core/coding/memory/models.py`: proposal, change, transaction, and reflection models.
- Create `core/coding/memory/evidence.py`: bounded evidence bundle builder.
- Create `core/coding/memory/policy.py`: deterministic eligibility and secret/injection filters.
- Create `core/coding/memory/proposals.py`: persistent proposal state machine and rollback transactions.
- Create `core/coding/memory/reflection.py`: tool-less child model runner and schema repair.
- Create `core/coding/memory/scheduler.py`: trigger thresholds, cooldown, and one-job lease.
- Modify `core/coding/memory/manager.py`: facade methods only.
- Integration Agent only: modify Runtime, memory tool, shared events, API schemas/routes, and reconnect behavior.

### Task 1: Proposal and Transaction Models

**Files:**
- Modify: `core/coding/memory/models.py`
- Create: `tests/core/coding/test_memory_proposal_models.py`

- [ ] **Step 1: Write failing model tests**

```python
def test_fact_and_proposal_are_different_types() -> None:
    change = MemoryChange(
        change_id="chg_1",
        operation="add",
        after="Use pytest for backend tests",
        reason="Repeated successful test command",
        evidence_refs=[evidence("run_1"), evidence("run_2")],
        confidence=0.91,
    )
    proposal = MemoryProposal(
        proposal_id="prop_1",
        reflection_id="ref_1",
        workspace_id="ws_1",
        session_id="s1",
        parent_run_id="run_2",
        trigger="auto",
        base_revision=3,
        version=1,
        changes=[change],
        created_at="2026-07-11T00:00:00Z",
    )
    assert proposal.status == "pending"
    assert proposal.changes[0].operation == "add"


def test_invalid_confidence_is_rejected() -> None:
    with pytest.raises(ValidationError):
        MemoryChange(
            change_id="chg_1",
            operation="add",
            after="fact",
            reason="reason",
            confidence=1.5,
        )
```

- [ ] **Step 2: Add the models**

```python
class MemoryChange(BaseModel):
    change_id: str
    operation: Literal["add", "update", "merge", "archive"]
    fact_id: str = ""
    before: str = ""
    after: str
    reason: str
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    flags: list[str] = Field(default_factory=list)


class MemoryProposal(BaseModel):
    proposal_id: str
    reflection_id: str
    workspace_id: str
    session_id: str
    parent_run_id: str = ""
    trigger: Literal["manual", "auto", "approved_plan", "user_correction"]
    base_revision: int
    version: int = 1
    status: Literal["pending", "approved", "rejected"] = "pending"
    changes: list[MemoryChange] = Field(default_factory=list, max_length=5)
    created_at: str
    resolved_at: str = ""


class MemoryTransaction(BaseModel):
    transaction_id: str
    proposal_id: str
    from_revision: int
    to_revision: int
    applied_changes: list[MemoryChange]
    inverse_changes: list[MemoryChange]
    applied_at: str
    rolled_back_at: str = ""


class ProposalResolution(BaseModel):
    proposal_id: str
    proposal_version: int
    status: Literal["approved", "rejected"]
    transaction_id: str = ""
    resolved_at: str


class MemoryState(BaseModel):
    schema_version: int = 2
    revision: int = 0
    facts: list[MemoryFact] = Field(default_factory=list)
    transactions: list[MemoryTransaction] = Field(default_factory=list)
    proposal_resolutions: list[ProposalResolution] = Field(default_factory=list)
    migrated_legacy: bool = False
```

- [ ] **Step 3: Run tests and commit**

```bash
pytest tests/core/coding/test_memory_proposal_models.py -q
git add core/coding/memory/models.py tests/core/coding/test_memory_proposal_models.py
git commit -m "feat(sage-v6): define dream proposal contract"
```

### Task 2: Bounded Evidence Builder and Policy Gate

**Files:**
- Create: `core/coding/memory/evidence.py`
- Create: `core/coding/memory/policy.py`
- Create: `tests/core/coding/test_memory_policy.py`

- [ ] **Step 1: Write failing policy tests**

Cover:

```text
test_bundle_uses_raw_evidence_not_compact_summary
test_bundle_is_capped_at_twelve_thousand_chars
test_inferred_fact_requires_two_independent_run_ids
test_approved_plan_can_form_single_source_proposal
test_explicit_user_correction_can_form_single_source_proposal
test_derivable_code_fact_is_rejected
test_likely_secret_is_rejected
test_prompt_injection_text_is_data_not_instruction
test_policy_recomputes_confidence_and_drops_score_below_point_seven
test_policy_returns_at_most_five_changes
```

- [ ] **Step 2: Implement the evidence bundle**

```python
class EvidenceItem(BaseModel):
    ref: EvidenceRef
    kind: str
    content: str
    eligible: bool = True


class EvidenceBundle(BaseModel):
    workspace_id: str
    session_id: str
    parent_run_id: str
    items: list[EvidenceItem]
    active_fact_headers: list[dict[str, str]]
    input_hash: str
```

Build only from explicit user statements, approved-plan evidence, successful terminal run events, test evidence, and current active fact headers. Exclude raw secrets, failed/cancelled runs, and compact-summary-only claims. Clip items deterministically to 12000 total characters.

- [ ] **Step 3: Implement deterministic policy evaluation**

`MemoryPolicy.evaluate(change, bundle)` must:

1. reject repository facts derivable from files/Git/AST;
2. reject likely credentials and instruction-injection phrasing;
3. verify every `EvidenceRef` exists in the bundle;
4. require 2 independent run IDs for inferred facts;
5. permit one approved plan or explicit user correction as proposal evidence;
6. compute its own confidence from source class, independence, repetition, and conflict;
7. discard confidence below 0.70;
8. return at most 5 stable ordered changes.

- [ ] **Step 4: Run tests and commit**

```bash
pytest tests/core/coding/test_memory_policy.py -q
git add core/coding/memory/evidence.py core/coding/memory/policy.py tests/core/coding/test_memory_policy.py
git commit -m "feat(sage-v6): gate dream evidence"
```

### Task 3: Persistent Proposal State Machine

**Files:**
- Create: `core/coding/memory/proposals.py`
- Create: `tests/core/coding/test_memory_proposals.py`

- [ ] **Step 1: Write failing state-machine tests**

Cover:

```text
test_proposal_survives_new_runtime_instance
test_create_persists_before_return
test_edit_requires_pending_status_and_matching_version
test_approve_selected_changes_is_atomic
test_repeat_approve_is_idempotent
test_stale_base_revision_returns_conflict
test_reject_never_changes_memory_revision
test_repeat_reject_is_idempotent
test_rollback_restores_exact_previous_revision
test_repeat_rollback_is_idempotent
test_non_latest_rollback_returns_conflict
```

- [ ] **Step 2: Implement proposal operations**

`ProposalStore` exposes:

```text
create(proposal)
get(proposal_id)
list_pending(workspace_id)
edit(proposal_id, expected_version, changes)
approve(proposal_id, expected_version, expected_revision, selected_change_ids)
reject(proposal_id, expected_version)
rollback(transaction_id, expected_revision)
```

Each mutation locks the workspace state and re-reads proposal and memory revision. Approval writes facts, transaction, and `ProposalResolution` into the same atomic `state.json` replacement. The separate proposal JSON is then regenerated as a recoverable view. Startup reconciles proposal views from canonical resolution records after an interrupted write. Store an operation idempotency key in the proposal/transaction so network retries return the first result.

- [ ] **Step 3: Render inverse operations explicitly**

For each approved operation, create an inverse:

```text
add -> archive the created fact
update -> restore the previous content/version
merge -> reactivate source facts and archive merged fact
archive -> reactivate the prior fact
```

- [ ] **Step 4: Run tests and commit**

```bash
pytest tests/core/coding/test_memory_proposals.py -q
git add core/coding/memory/proposals.py tests/core/coding/test_memory_proposals.py
git commit -m "feat(sage-v6): persist dream proposals"
```

### Task 4: Tool-Less Reflection Child Agent

**Files:**
- Create: `core/coding/memory/reflection.py`
- Create: `tests/core/coding/test_memory_reflection.py`

- [ ] **Step 1: Write failing runner tests**

```python
async def test_reflection_runner_exposes_no_tools() -> None:
    model = ScriptedReflectionModel(valid_response())
    runner = MemoryReflectionRunner(model=model, timeout_seconds=30)
    await runner.run(bundle_fixture())
    assert model.received_tools == []


async def test_timeout_creates_no_candidate() -> None:
    runner = MemoryReflectionRunner(model=NeverReturnsModel(), timeout_seconds=0.01)
    result = await runner.run(bundle_fixture())
    assert result.status == "failed"
    assert result.changes == []
    assert result.reason == "timeout"
```

Also cover one schema repair, invalid evidence IDs, repository injection, no nested delegation, and model error isolation.

- [ ] **Step 2: Implement the runner**

The runner receives a model client directly and calls `ainvoke`/`complete` with one fixed system instruction and one fenced evidence payload. It does not instantiate `Engine`, `WorkerManager`, `ToolExecutor`, a tool registry, memory recall, or a writable workspace.

The model response schema is:

```python
class ReflectionOutput(BaseModel):
    changes: list[MemoryChange] = Field(default_factory=list, max_length=10)
```

Parse JSON, attempt one repair prompt if parsing fails, pass changes through `MemoryPolicy`, and return a typed failure instead of raising into the parent run.

- [ ] **Step 3: Run tests and commit**

```bash
pytest tests/core/coding/test_memory_reflection.py -q
git add core/coding/memory/reflection.py tests/core/coding/test_memory_reflection.py
git commit -m "feat(sage-v6): add constrained dream reflection"
```

### Task 5: Scheduler and Non-Blocking Runtime Integration

**Files:**
- Create: `core/coding/memory/scheduler.py`
- Modify: `core/coding/memory/manager.py`
- Integration Agent modify: `core/coding/runtime.py`
- Integration Agent modify: `core/coding/tools/memory_tools.py`
- Create: `tests/core/coding/test_memory_scheduler.py`
- Create: `tests/core/coding/test_runtime_memory_reflection.py`

- [ ] **Step 1: Write scheduler tests**

Cover:

```text
test_manual_dream_starts_when_run_is_idle
test_manual_dream_returns_existing_pending_proposal
test_auto_reflection_disabled_by_default
test_auto_trigger_requires_three_successful_runs_or_six_evidence_items
test_auto_trigger_obeys_thirty_minute_cooldown
test_only_one_reflection_job_per_workspace
test_failed_cancelled_and_step_limit_runs_do_not_count
test_run_finished_does_not_wait_for_reflection
```

- [ ] **Step 2: Implement trigger state**

Persist scheduler cursor with:

```text
last_evidence_index
successful_runs_since_review
eligible_items_since_review
last_reflection_at
active_reflection_id
pending_proposal_id
```

The default configuration is `SAGE_MEMORY_AUTO_REFLECTION=false`. When enabled, trigger at 3 successful runs or 6 eligible evidence items, with a 30-minute workspace cooldown.

- [ ] **Step 3: Integrate after terminal persistence**

Runtime emits `run_finished` before scheduling background review. Scheduling must not be awaited by the WebSocket turn. The child completion callback persists its reflection/proposal before emitting a session-level event.

- [ ] **Step 4: Wire `/dream` to the scheduler**

The deferred `dream` tool requests manual reflection and returns `reflection_id`/current pending `proposal_id`. It does not run synchronous consolidation inside the main Engine loop and does not access private manager fields.

- [ ] **Step 5: Run tests and commit**

```bash
pytest tests/core/coding/test_memory_scheduler.py tests/core/coding/test_runtime_memory_reflection.py -q
git add core/coding/memory core/coding/runtime.py core/coding/tools/memory_tools.py tests/core/coding
git commit -m "feat(sage-v6): schedule nonblocking dream review"
```

### Task 6: Proposal APIs, Events, Reconnect, and Benchmark

**Files:**
- Integration Agent modify: `core/coding/engine/events.py`
- Integration Agent modify: `api/schemas.py`
- Integration Agent modify: `api/coding.py`
- Create: `tests/api/test_coding_memory_proposal_routes.py`
- Modify: `tests/evals/test_benchmark.py`

- [ ] **Step 1: Add typed session events**

Implement:

```text
memory_reflection_started
memory_proposal_ready
memory_reflection_failed
memory_proposal_resolved
memory_revision_rolled_back
```

Persist reflection/proposal state before emitting. Reconnect loads pending proposals from `ProposalStore` and re-sends `memory_proposal_ready`.

- [ ] **Step 2: Add resource APIs**

```text
POST  /api/v1/coding/{session_id}/memory/reflections
GET   /api/v1/coding/{session_id}/memory
GET   /api/v1/coding/{session_id}/memory/proposals
GET   /api/v1/coding/{session_id}/memory/proposals/{proposal_id}
PATCH /api/v1/coding/{session_id}/memory/proposals/{proposal_id}
POST  /api/v1/coding/{session_id}/memory/proposals/{proposal_id}/approve
POST  /api/v1/coding/{session_id}/memory/proposals/{proposal_id}/reject
POST  /api/v1/coding/{session_id}/memory/transactions/{transaction_id}/rollback
```

Return `404` for unknown scoped resources, `409` for stale revision/version, and `422` for invalid or sensitive edited content.

- [ ] **Step 3: Add API tests**

Cover proposal ID binding, workspace isolation, edit/approve/reject idempotency, stale conflicts, rollback, persist-before-event ordering, and reconnect recovery.

- [ ] **Step 4: Add benchmark scenarios**

Add metrics:

```text
proposal_precision
proposal_provenance_rate
preapproval_mutation_count
reflection_parent_run_latency_ms
rollback_success_rate
```

`preapproval_mutation_count` must remain zero.

- [ ] **Step 5: Verify and commit**

```bash
pytest tests/core/coding/test_memory_*.py tests/api/test_coding_memory_proposal_routes.py tests/evals/test_benchmark.py -q
python -m evals.coding.runner
git add core/coding api tests evals/coding
git commit -m "test(sage-v6): verify dream proposal governance"
```
