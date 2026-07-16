"""Revision-bound Knowledge retrieval through the reusable Harness port."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from core.coding.runtime import CodingRuntime
from core.harness.knowledge_adapter import CodingKnowledgePort
from core.harness.tools_adapter import build_deerflow_coding_tools
from core.knowledge import KnowledgeSourceRoot, KnowledgeStore


def _knowledge_store(tmp_path: Path) -> tuple[KnowledgeStore, Path]:
    vault = tmp_path / "vault"
    vault.mkdir()
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=knowledge,
        check=True,
        capture_output=True,
        text=True,
    )
    store = KnowledgeStore(
        knowledge,
        tmp_path / "knowledge.sqlite3",
        {
            "notes": KnowledgeSourceRoot(
                root_id="notes",
                kind="obsidian",
                label="Notes",
                path=vault,
            )
        },
    )
    return store, vault


def _runtime(tmp_path: Path, store: KnowledgeStore | None) -> CodingRuntime:
    return CodingRuntime(
        session_id="session-knowledge",
        workspace_root=tmp_path / "workspace",
        model=object(),
        storage_root=tmp_path / ".coding",
        knowledge_store=store,
        runtime_profile="deerflow_v2",
    )


def _ingest(store: KnowledgeStore, vault: Path, content: str) -> None:
    (vault / "harness.md").write_text(content, encoding="utf-8")
    proposal = store.ingest("notes", "harness.md")
    store.evaluate_and_apply_policy(proposal.proposal_id)


def test_knowledge_port_returns_revision_bound_evidence_and_rejects_other_workspace(
    tmp_path: Path,
) -> None:
    store, vault = _knowledge_store(tmp_path)
    _ingest(store, vault, "# Harness\n\nCheckpoint resume uses revision-bound citations.\n")
    port = CodingKnowledgePort(_runtime(tmp_path, store))

    result = asyncio.run(
        port.search(
            "checkpoint revision citation",
            workspace_id=port.workspace_id,
            token_budget=512,
            top_k=4,
        )
    )

    assert result.status == "evidence_found"
    assert result.used_tokens <= 512
    assert result.evidence[0].citation_id.startswith("kcite_")
    assert result.evidence[0].page_revision.startswith("krev_")
    assert result.evidence[0].metadata["source_relative_path"] == "harness.md"
    assert str(vault) not in repr(result)
    with pytest.raises(PermissionError, match="workspace"):
        asyncio.run(
            port.search(
                "checkpoint",
                workspace_id="other-workspace",
                token_budget=512,
            )
        )


def test_knowledge_port_replaces_stale_revision_citation(tmp_path: Path) -> None:
    store, vault = _knowledge_store(tmp_path)
    _ingest(store, vault, "# Harness\n\nThe first checkpoint policy.\n")
    port = CodingKnowledgePort(_runtime(tmp_path, store))
    first = asyncio.run(
        port.search("checkpoint policy", workspace_id=port.workspace_id, token_budget=512)
    )
    first_citation = first.evidence[0].citation_id

    _ingest(store, vault, "# Harness\n\nThe revised checkpoint policy uses durable replay.\n")
    second = asyncio.run(
        port.search("checkpoint policy", workspace_id=port.workspace_id, token_budget=512)
    )

    assert second.evidence[0].citation_id != first_citation
    assert second.evidence[0].page_revision != first.evidence[0].page_revision
    with pytest.raises((KeyError, ValueError), match="stale|unknown"):
        store.citation(first_citation)


def test_harness_tools_serialize_search_and_gate_learning_on_availability(
    tmp_path: Path,
) -> None:
    store, vault = _knowledge_store(tmp_path)
    _ingest(store, vault, "# Harness\n\nEvidence is bounded by a token budget.\n")
    runtime = _runtime(tmp_path, store)
    port = CodingKnowledgePort(runtime)
    tools = build_deerflow_coding_tools(runtime, run_id="run-1", knowledge_port=port)
    by_name = {tool.name: tool for tool in tools}

    content = asyncio.run(
        by_name["knowledge_search"].ainvoke(
            {"query": "evidence token budget", "top_k": 4, "token_budget": 512}
        )
    )
    payload = json.loads(content)

    assert "knowledge_learn" in by_name
    assert payload["status"] == "evidence_found"
    assert payload["citations"][0]["citation_id"].startswith("kcite_")
    assert payload["citations"][0]["page_revision"].startswith("krev_")
    assert str(vault) not in content

    unavailable_runtime = _runtime(tmp_path / "unavailable", None)
    unavailable_port = CodingKnowledgePort(unavailable_runtime)
    unavailable_tools = build_deerflow_coding_tools(
        unavailable_runtime,
        run_id="run-2",
        knowledge_port=unavailable_port,
    )
    unavailable_by_name = {tool.name: tool for tool in unavailable_tools}
    unavailable_content = asyncio.run(
        unavailable_by_name["knowledge_search"].ainvoke(
            {"query": "evidence", "top_k": 4, "token_budget": 512}
        )
    )

    assert "knowledge_learn" not in unavailable_by_name
    assert json.loads(unavailable_content)["status"] == "unavailable"


def test_harness_knowledge_learning_reuses_sage_approval_boundary(tmp_path: Path) -> None:
    store, vault = _knowledge_store(tmp_path)
    _ingest(store, vault, "# Harness\n\nOnly cited evidence may be learned.\n")
    runtime = _runtime(tmp_path, store)
    port = CodingKnowledgePort(runtime)
    tools = {
        tool.name: tool
        for tool in build_deerflow_coding_tools(
            runtime,
            run_id="run-learn",
            knowledge_port=port,
        )
    }
    search_content = asyncio.run(
        tools["knowledge_search"].ainvoke(
            {"query": "cited evidence", "top_k": 4, "token_budget": 512}
        )
    )
    citation_id = str(json.loads(search_content)["citations"][0]["citation_id"])
    proposal_count = len(store.list_proposals())

    async def reject_learning() -> str:
        async with runtime.harness_turn("run-learn"):
            task = asyncio.create_task(
                tools["knowledge_learn"].ainvoke(
                    {"topic": "Harness evidence", "citation_ids": [citation_id]}
                )
            )
            pending = None
            for _ in range(100):
                pending = runtime.approval_manager.pending(runtime.session_id)
                if pending is not None:
                    break
                await asyncio.sleep(0.01)
            assert pending is not None
            assert pending["tool"] == "knowledge_learn"
            assert runtime.approval_manager.resolve(
                runtime.session_id,
                str(pending["approval_id"]),
                "deny",
            )
            return str(await task)

    result = asyncio.run(reject_learning())

    assert result == "approval denied"
    assert len(store.list_proposals()) == proposal_count
