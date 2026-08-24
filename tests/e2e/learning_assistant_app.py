"""Isolated FastAPI fixture for the repository Playwright learning flow."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from pydantic import BaseModel, ConfigDict, Field

from api.main import create_app
from core.coding.memory import workspace_id_from_path
from core.coding.persistence.session_event_journal import SessionEventJournal
from core.learning import LearningKickoffError

_OUTPUT_ROOT = Path("output/playwright").resolve()
_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
_RUNTIME_ROOT = Path(tempfile.mkdtemp(prefix="learning-e2e-", dir=_OUTPUT_ROOT))
_WORKSPACE_ROOT = _RUNTIME_ROOT / "workspace"
_STORAGE_ROOT = _RUNTIME_ROOT / ".coding"
_WORKSPACE_ROOT.mkdir()

_CONTROL: dict[str, Any] = {
    "activation_failures_remaining": 0,
    "hold_kickoff": False,
    "model_calls": 0,
}


class _FakeModel(FakeMessagesListChatModel):
    """Deterministic local provider; it never reads credentials or the network."""

    def __init__(self) -> None:
        super().__init__(responses=[AIMessage(content="已接受一次可恢复学习首轮。")])

    def bind_tools(self, tools: object, **kwargs: object) -> _FakeModel:
        del tools, kwargs
        return self

    def _generate(self, *args: Any, **kwargs: Any) -> Any:
        _CONTROL["model_calls"] += 1
        return super()._generate(*args, **kwargs)


class _ControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activation_failures: int = Field(default=0, ge=0, le=3)
    hold_kickoff: bool = False


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
    return {"configured": True}


@app.post("/api/__e2e__/release-kickoff")
async def release_kickoff() -> dict[str, object]:
    _CONTROL["hold_kickoff"] = False
    return {"released": True}


@app.get("/api/__e2e__/stats")
async def e2e_stats() -> dict[str, object]:
    workspace_id = workspace_id_from_path(_WORKSPACE_ROOT)
    tasks = app.state.learning_task_service.list(
        owner_id="local",
        workspace_id=workspace_id,
    )
    if not tasks:
        return {
            "task_status": None,
            "kickoff_status": None,
            "acceptance_count": 0,
            "turn_started_count": 0,
            "model_calls": _CONTROL["model_calls"],
        }
    task = tasks[0]
    try:
        receipt = app.state.learning_kickoff_service.get(
            owner_id="local",
            workspace_id=workspace_id,
            task_id=task.task_id,
        )
    except LearningKickoffError as exc:
        if exc.code != "learning_kickoff_not_found":
            raise HTTPException(status_code=409, detail=exc.code) from exc
        return {
            "task_status": task.status,
            "kickoff_status": None,
            "acceptance_count": 0,
            "turn_started_count": 0,
            "model_calls": _CONTROL["model_calls"],
        }
    journal = SessionEventJournal(_STORAGE_ROOT, receipt.session_id)
    acceptance_events = journal.events_for_run(receipt.acceptance_run_id)
    turn_events = journal.events_for_run(receipt.turn_run_id)
    return {
        "task_status": task.status,
        "kickoff_status": receipt.receipt_status,
        "kickoff_stage": receipt.stage,
        "acceptance_count": len(acceptance_events),
        "turn_started_count": sum(
            event.payload.get("event") == "run_started" for event in turn_events
        ),
        "model_calls": _CONTROL["model_calls"],
    }
