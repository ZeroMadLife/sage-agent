"""Isolated FastAPI fixture for repository-owned Learning Playwright flows."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Response, status
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel, ConfigDict, Field
from sage_harness import KnowledgeEvidence, KnowledgeRetrievalResult, WebEvidence, WebSearchResult

from api.main import create_app
from core.coding.memory import workspace_id_from_path
from core.coding.persistence.session_event_journal import SessionEventJournal
from core.learning import LearningKickoffError

_OUTPUT_ROOT = Path("output/playwright").resolve()
_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
_RUNTIME_ROOT = Path(
    os.environ.get("SAGE_E2E_RUNTIME_ROOT")
    or tempfile.mkdtemp(prefix="learning-e2e-", dir=_OUTPUT_ROOT)
).resolve()
_WORKSPACE_ROOT = _RUNTIME_ROOT / "workspace"
_STORAGE_ROOT = _RUNTIME_ROOT / ".coding"
_WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)

_CONTROL: dict[str, Any] = {
    "activation_failures_remaining": 0,
    "hold_kickoff": False,
    "model_calls": 0,
    "research_model_calls": 0,
    "hold_knowledge": False,
    "knowledge_hold_started": False,
}


class _FakeModel(FakeMessagesListChatModel):
    """Local provider substitute supporting both Harness chat and worker protocols."""

    def __init__(self) -> None:
        super().__init__(responses=[AIMessage(content="已接受一次可恢复学习首轮。")])
        self._worker_calls = 0

    def bind_tools(self, tools: object, **kwargs: object) -> _FakeModel:
        del tools, kwargs
        return self

    def _generate(self, *args: Any, **kwargs: Any) -> Any:
        messages = args[0] if args else kwargs.get("messages", ())
        prompt = "\n".join(str(getattr(message, "content", "")) for message in messages)
        if "Research this learning gap using only read-only Web tools" in prompt:
            response = self._research_response(prompt)
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=response))])
        _CONTROL["model_calls"] += 1
        return super()._generate(*args, **kwargs)

    async def complete(self, prompt: str) -> str:
        return self._research_response(prompt)

    def _research_response(self, prompt: str) -> str:
        _CONTROL["research_model_calls"] += 1
        if "Research 失败" in prompt:
            raise RuntimeError("deterministic fake provider failure")
        self._worker_calls += 1
        if self._worker_calls == 1:
            query = (
                "Sage same URL conflict evidence"
                if "同 URL 冲突" in prompt
                else "Sage checkpoint evidence"
            )
            return f'<tool>{{"name":"search_web","args":{{"query":"{query}"}}}}</tool>'
        if "同 URL 冲突" in prompt:
            return (
                "<final>Conflicting public evidence [wcite_e2e_same_url_a] "
                "[wcite_e2e_same_url_b] was preserved.</final>"
            )
        return "<final>Public evidence [wcite_e2e_research] was collected.</final>"


class _FakeKnowledgePort:
    available = True
    workspace_id = "learning-e2e-knowledge"

    async def search(
        self,
        query: str,
        *,
        workspace_id: str,
        token_budget: int,
        top_k: int = 8,
    ) -> KnowledgeRetrievalResult:
        del top_k
        if "刷新 pending" in query and _CONTROL["hold_knowledge"]:
            _CONTROL["knowledge_hold_started"] = True
            while _CONTROL["hold_knowledge"]:
                await asyncio.sleep(0.02)
        evidence: tuple[KnowledgeEvidence, ...]
        if "Knowledge 已支持" in query:
            evidence = (
                KnowledgeEvidence(
                    citation_id="kcite-e2e-ready",
                    content="Checkpoint stores bounded revision state and opaque Artifact refs.",
                    page_revision="page-e2e-ready-r1",
                    source_revision="source-e2e-ready-r1",
                    metadata={"title": "Knowledge checkpoint guide"},
                ),
            )
        elif "Knowledge 冲突" in query:
            evidence = (
                KnowledgeEvidence(
                    citation_id="kcite-e2e-conflict-a",
                    content="Checkpoint contains the generated Learning Artifact body.",
                    page_revision="page-e2e-conflict-a",
                    source_revision="source-e2e-conflict-a",
                    metadata={"title": "Knowledge Source A", "conflict_group": "checkpoint-body"},
                ),
                KnowledgeEvidence(
                    citation_id="kcite-e2e-conflict-b",
                    content="Checkpoint stores only an opaque Learning Artifact ref.",
                    page_revision="page-e2e-conflict-b",
                    source_revision="source-e2e-conflict-b",
                    metadata={"title": "Knowledge Source B", "conflict_group": "checkpoint-body"},
                ),
            )
        else:
            evidence = ()
        return KnowledgeRetrievalResult(
            query=query,
            workspace_id=workspace_id,
            status="evidence_found" if evidence else "no_evidence",
            token_budget=token_budget,
            used_tokens=80 if evidence else 0,
            omitted_count=0,
            evidence=evidence,
        )


class _FakeWebSearchPort:
    provider = "learning-e2e-fake"
    available = True

    async def search(self, query: str, **kwargs: object) -> WebSearchResult:
        raw_token_budget = kwargs.get("token_budget", 2_000)
        token_budget = (
            raw_token_budget
            if isinstance(raw_token_budget, int) and not isinstance(raw_token_budget, bool)
            else 2_000
        )
        evidence = (
            (
                WebEvidence(
                    citation_id="wcite_e2e_same_url_a",
                    canonical_url="https://docs.example.com/checkpoint-conflict",
                    original_url="https://docs.example.com/checkpoint-conflict",
                    title="Checkpoint conflict revision A",
                    excerpt="Checkpoint includes the full generated body.",
                    provider=self.provider,
                    retrieved_at="2026-08-25T01:00:00Z",
                    content_hash="sha256:e2e-same-url-a",
                    rank=1,
                    metadata={"conflict_group": "same-url-checkpoint"},
                ),
                WebEvidence(
                    citation_id="wcite_e2e_same_url_b",
                    canonical_url="https://docs.example.com/checkpoint-conflict",
                    original_url="https://docs.example.com/checkpoint-conflict",
                    title="Checkpoint conflict revision B",
                    excerpt="Checkpoint stores only an opaque Artifact ref.",
                    provider=self.provider,
                    retrieved_at="2026-08-25T01:01:00Z",
                    content_hash="sha256:e2e-same-url-b",
                    rank=2,
                    metadata={"conflict_group": "same-url-checkpoint"},
                ),
            )
            if "same URL conflict" in query
            else (
                WebEvidence(
                    citation_id="wcite_e2e_research",
                    canonical_url="https://docs.example.com/checkpoint",
                    original_url="https://docs.example.com/checkpoint",
                    title="Checkpoint public docs",
                    excerpt="Research evidence confirms checkpoint persists bounded recovery state and an opaque Artifact ref.",
                    provider=self.provider,
                    retrieved_at="2026-08-25T01:00:00Z",
                    content_hash="sha256:e2e-web-content-r1",
                    rank=1,
                ),
            )
        )
        return WebSearchResult(
            query=query,
            provider=self.provider,
            status="evidence_found",
            token_budget=token_budget,
            used_tokens=60,
            evidence=evidence,
        )


class _ControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activation_failures: int = Field(default=0, ge=0, le=3)
    hold_kickoff: bool = False
    hold_knowledge: bool = False


def _model_factory(*args: object, **kwargs: object) -> _FakeModel:
    del args, kwargs
    return _FakeModel()


app = create_app(
    coding_model_factory=_model_factory,
    coding_workspace_root=_WORKSPACE_ROOT,
    coding_storage_root=_STORAGE_ROOT,
    coding_default_runtime_profile="deerflow_v2",
    coding_deerflow_v2_enabled=True,
    coding_sandbox_provider="local_workspace",
    coding_web_search_port=_FakeWebSearchPort(),
    learning_knowledge_port_factory=lambda runtime: _FakeKnowledgePort(),
    database_auto_migrate=False,
    knowledge_jobs_enabled=False,
    cloud_app_env="development",
)


def _activation_failure(point: str, record: object) -> None:
    del record
    if point != "after_intent" or _CONTROL["activation_failures_remaining"] <= 0:
        return
    _CONTROL["activation_failures_remaining"] -= 1
    raise RuntimeError("injected Playwright activation failure")


def _kickoff_failure(point: str, record: object) -> None:
    del record
    if point == "after_journal" and _CONTROL["hold_kickoff"]:
        raise RuntimeError("injected Playwright kickoff hold")


app.state.learning_activation_service.failure_injector = _activation_failure
app.state.learning_kickoff_service.failure_injector = _kickoff_failure


@app.post("/api/__e2e__/configure")
async def configure_e2e(payload: _ControlRequest) -> dict[str, object]:
    _CONTROL["activation_failures_remaining"] = payload.activation_failures
    _CONTROL["hold_kickoff"] = payload.hold_kickoff
    _CONTROL["hold_knowledge"] = payload.hold_knowledge
    _CONTROL["knowledge_hold_started"] = False
    return {"configured": True}


@app.post("/api/__e2e__/release-kickoff")
async def release_kickoff() -> dict[str, object]:
    _CONTROL["hold_kickoff"] = False
    return {"released": True}


@app.post("/api/__e2e__/release-knowledge")
async def release_knowledge() -> dict[str, object]:
    _CONTROL["hold_knowledge"] = False
    return {"released": True}


class _OrphanAdvanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    idempotency_key: str
    expected_checkpoint_revision: int = Field(ge=0)


@app.post("/api/__e2e__/orphan-advance")
async def orphan_advance(payload: _OrphanAdvanceRequest) -> dict[str, object]:
    workspace_id = workspace_id_from_path(_WORKSPACE_ROOT)
    task = app.state.learning_task_service.get(
        owner_id="local", workspace_id=workspace_id, task_id=payload.task_id
    )
    scope = app.state.learning_readonly_scope_resolver.resolve(
        owner_id="local", workspace_id=workspace_id, task_id=payload.task_id
    )
    claim = app.state.learning_artifact_store.claim_advance_request(
        owner_id="local",
        workspace_id=workspace_id,
        task=task,
        capability_revision=scope.capability_revision,
        catalog_revision=scope.catalog_revision,
        expected_checkpoint_revision=payload.expected_checkpoint_revision,
        idempotency_key=payload.idempotency_key,
    )
    with sqlite3.connect(_STORAGE_ROOT / "learning-artifacts.sqlite3") as connection:
        connection.execute(
            """UPDATE learning_advance_requests SET lease_expires_at = ?
               WHERE owner_id = ? AND workspace_id = ? AND task_id = ?
                 AND request_key_hash = ?""",
            (
                "2000-01-01T00:00:00Z",
                "local",
                workspace_id,
                payload.task_id,
                claim.request_key_hash,
            ),
        )
    return {"orphaned": True, "fencing_token": claim.fencing_token}


@app.post("/api/__e2e__/restart-process", status_code=status.HTTP_202_ACCEPTED)
async def restart_process(response: Response) -> dict[str, object]:
    response.headers["Cache-Control"] = "no-store"
    asyncio.get_running_loop().call_later(0.15, os._exit, 75)
    return {"restarting": True, "pid": os.getpid()}


@app.get("/api/__e2e__/stats")
async def e2e_stats() -> dict[str, object]:
    workspace_id = workspace_id_from_path(_WORKSPACE_ROOT)
    tasks = app.state.learning_task_service.list(owner_id="local", workspace_id=workspace_id)
    payload: dict[str, object] = {
        "task_count": len(tasks),
        "task_status": None,
        "session_id": None,
        "kickoff_status": None,
        "acceptance_count": 0,
        "turn_started_count": 0,
        "model_calls": _CONTROL["model_calls"],
        "research_model_calls": _CONTROL["research_model_calls"],
        "knowledge_hold_started": _CONTROL["knowledge_hold_started"],
        "pid": os.getpid(),
    }
    if not tasks:
        return payload
    task = tasks[0]
    payload["task_status"] = task.status
    try:
        receipt = app.state.learning_kickoff_service.get(
            owner_id="local", workspace_id=workspace_id, task_id=task.task_id
        )
    except LearningKickoffError as exc:
        if exc.code != "learning_kickoff_not_found":
            raise HTTPException(status_code=409, detail=exc.code) from exc
        return payload
    journal = SessionEventJournal(_STORAGE_ROOT, receipt.session_id)
    acceptance_events = journal.events_for_run(receipt.acceptance_run_id)
    turn_events = journal.events_for_run(receipt.turn_run_id)
    payload.update(
        {
            "session_id": receipt.session_id,
            "kickoff_status": receipt.receipt_status,
            "kickoff_stage": receipt.stage,
            "acceptance_count": len(acceptance_events),
            "turn_started_count": sum(
                event.payload.get("event") == "run_started" for event in turn_events
            ),
        }
    )
    database = _STORAGE_ROOT / "learning-artifacts.sqlite3"
    if database.exists():
        with sqlite3.connect(database) as connection:
            payload["artifact_count"] = connection.execute(
                "SELECT COUNT(*) FROM learning_artifacts"
            ).fetchone()[0]
            payload["research_receipt_count"] = connection.execute(
                "SELECT COUNT(*) FROM learning_research_receipts"
            ).fetchone()[0]
    return payload
