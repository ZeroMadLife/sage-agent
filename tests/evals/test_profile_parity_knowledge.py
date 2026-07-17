"""Revision-bound Knowledge citation parity over the public timeline."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from api.main import create_app
from core.knowledge import KnowledgeSourceRoot, KnowledgeStore
from evals.coding.profile_parity import (
    ProfileParityReport,
    RuntimeProfile,
    project_profile_timeline,
)
from tests.core.coding.scripted_api_client import ScriptedApiClient

_QUERY = "phoenix checkpoint revision"
_TOKEN_BUDGET = 3_000


class DeerflowKnowledgeModel(FakeMessagesListChatModel):
    def __init__(self, citation_id: str) -> None:
        super().__init__(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "knowledge_search",
                            "args": {
                                "query": _QUERY,
                                "top_k": 8,
                                "token_budget": _TOKEN_BUDGET,
                            },
                            "id": "call-knowledge-current-revision",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content=f"当前证据可追溯到 [{citation_id}]。"),
            ]
        )

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # type: ignore[no-untyped-def]
        _ = tools, tool_choice, kwargs
        return self


def _ingest(store: KnowledgeStore, vault: Path, content: str) -> None:
    (vault / "phoenix.md").write_text(content, encoding="utf-8")
    proposal = store.ingest("notes", "phoenix.md")
    store.evaluate_and_apply_policy(proposal.proposal_id)


def _prepare_store(root: Path) -> tuple[KnowledgeStore, Path, str, str]:
    vault = root / "vault"
    vault.mkdir(parents=True)
    knowledge = root / "knowledge"
    knowledge.mkdir()
    database = root / "knowledge.sqlite3"
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=knowledge,
        check=True,
        capture_output=True,
        text=True,
    )
    source_roots = {
        "notes": KnowledgeSourceRoot(
            root_id="notes",
            kind="obsidian",
            label="Notes",
            path=vault,
        )
    }
    store = KnowledgeStore(knowledge, database, source_roots)
    _ingest(
        store,
        vault,
        "# Phoenix\n\nThe old phoenix checkpoint used an unversioned citation.\n",
    )
    old_bundle = store.retrieve(_QUERY, top_k=8, token_budget=_TOKEN_BUDGET)
    old_citation_id = old_bundle.evidence[0].hit.citation_id

    current_body = "\n\n".join(
        f"Phoenix checkpoint revision {index} preserves durable citation evidence."
        for index in range(180)
    )
    _ingest(store, vault, f"# Phoenix Harness\n\n{current_body}\n")
    current_bundle = store.retrieve(_QUERY, top_k=8, token_budget=_TOKEN_BUDGET)
    current_citation_id = current_bundle.evidence[0].hit.citation_id

    assert current_citation_id != old_citation_id
    with pytest.raises((KeyError, ValueError), match="stale|unknown"):
        store.citation(old_citation_id)
    return store, vault, old_citation_id, current_citation_id


def _payloads(events: list[dict[str, object]]) -> list[dict[str, object]]:
    return [payload for event in events if isinstance((payload := event.get("payload")), dict)]


def _run_scenario(
    tmp_path: Path,
    profile: RuntimeProfile,
) -> tuple[list[dict[str, object]], KnowledgeStore, Path, str, str]:
    root = tmp_path / profile
    root.mkdir()
    workspace = root / "workspace"
    workspace.mkdir()
    store, vault, old_citation_id, current_citation_id = _prepare_store(root)
    source_roots = {
        "notes": KnowledgeSourceRoot(
            root_id="notes",
            kind="obsidian",
            label="Notes",
            path=vault,
        )
    }

    def model_factory():  # type: ignore[no-untyped-def]
        if profile == "legacy":
            return ScriptedApiClient(
                [
                    (
                        '<tool>{"name":"knowledge_search","args":'
                        f'{{"query":"{_QUERY}","top_k":8,'
                        f'"token_budget":{_TOKEN_BUDGET}}}}}</tool>'
                    ),
                    f"<final>当前证据可追溯到 [{current_citation_id}]。</final>",
                ]
            )
        return DeerflowKnowledgeModel(current_citation_id)

    app = create_app(
        coding_model_factory=model_factory,
        coding_workspace_root=workspace,
        coding_storage_root=root / ".coding",
        coding_deerflow_v2_enabled=True,
        knowledge_workspace_root=root / "knowledge",
        knowledge_database_path=root / "knowledge.sqlite3",
        knowledge_source_roots=source_roots,
        knowledge_jobs_enabled=False,
    )
    with TestClient(app) as client:
        session_id = client.post(
            "/api/v1/coding/session",
            json={"runtime_profile": profile},
        ).json()["session_id"]
        with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
            websocket.send_json({"content": "检索 Phoenix checkpoint 的当前修订证据"})
            events: list[dict[str, object]] = []
            while True:
                event = websocket.receive_json()
                events.append(event)
                if event["kind"] == "terminal":
                    return events, store, vault, old_citation_id, current_citation_id


def _retrieval_payload(events: list[dict[str, object]]) -> tuple[str, dict[str, object]]:
    tool_result = next(
        payload
        for payload in _payloads(events)
        if payload.get("type") == "tool_result" and payload.get("tool") == "knowledge_search"
    )
    content = str(tool_result["content"])
    decoded = json.loads(content)
    assert isinstance(decoded, dict)
    return content, decoded


def _final_contains(events: list[dict[str, object]], expected: str) -> bool:
    return any(
        payload.get("type") == "final" and expected in str(payload.get("content", ""))
        for payload in _payloads(events)
    )


def _assert_current_retrieval(
    *,
    content: str,
    retrieval: Mapping[str, object],
    store: KnowledgeStore,
    vault: Path,
    old_citation_id: str,
    current_citation_id: str,
) -> None:
    assert retrieval["status"] == "evidence_found"
    used_tokens = retrieval["used_tokens"]
    token_budget = retrieval["token_budget"]
    assert isinstance(used_tokens, int)
    assert isinstance(token_budget, int)
    assert used_tokens <= token_budget
    assert token_budget == _TOKEN_BUDGET
    citations = retrieval["citations"]
    assert isinstance(citations, list) and citations
    citation_ids = [str(item["citation_id"]) for item in citations]
    assert current_citation_id in citation_ids
    assert old_citation_id not in citation_ids
    assert str(vault) not in content
    for item in citations:
        assert isinstance(item, dict)
        chunk = store.citation(str(item["citation_id"]))
        assert item["page_revision"] == chunk.page_revision
        assert item["source_revision"] == chunk.source_revision
        assert item["source_relative_path"] == chunk.source_relative_path


def test_legacy_and_v2_return_current_bounded_knowledge_citations(
    tmp_path: Path,
) -> None:
    results = []
    for profile in ("legacy", "deerflow_v2"):
        events, store, vault, old_citation_id, current_citation_id = _run_scenario(
            tmp_path,
            profile,
        )
        content, retrieval = _retrieval_payload(events)
        _assert_current_retrieval(
            content=content,
            retrieval=retrieval,
            store=store,
            vault=vault,
            old_citation_id=old_citation_id,
            current_citation_id=current_citation_id,
        )
        if profile == "deerflow_v2":
            assert len(content) <= 4_000
        results.append(
            project_profile_timeline(
                "revision-bound-knowledge",
                profile,
                events,
                assertions_passed=_final_contains(events, current_citation_id),
            )
        )

    report = ProfileParityReport(results=results)

    assert report.paired_scenarios() == ("revision-bound-knowledge",)
    assert report.regressions() == ()
    assert all(result.passed for result in report.results), report.to_dict()
    assert all(result.tool_calls == 1 for result in report.results)
    assert all(result.tool_errors == 0 for result in report.results)
    assert report.metrics("legacy").tool_call_success_rate == 1.0
    assert report.metrics("deerflow_v2").tool_call_success_rate == 1.0
