"""V7.2 auditable Knowledge Workspace API coverage."""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from api.main import create_app
from core.knowledge import KnowledgeSourceRoot


def _app(tmp_path: Path):
    coding_workspace = tmp_path / "coding"
    coding_workspace.mkdir()
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
    app = create_app(
        coding_workspace_root=coding_workspace,
        coding_storage_root=tmp_path / ".coding",
        cloud_app_env="development",
        knowledge_workspace_root=knowledge,
        knowledge_database_path=knowledge / ".sage" / "knowledge.sqlite3",
        knowledge_source_roots={
            "sage-learning": KnowledgeSourceRoot(
                root_id="sage-learning",
                kind="obsidian",
                label="Sage Learning",
                path=vault,
            )
        },
        knowledge_jobs_enabled=False,
    )
    return app, vault, knowledge


def test_unconfigured_knowledge_is_explicitly_unavailable(tmp_path: Path) -> None:
    workspace = tmp_path / "coding"
    workspace.mkdir()
    app = create_app(
        coding_workspace_root=workspace,
        coding_storage_root=tmp_path / ".coding",
        cloud_app_env="development",
    )

    response = TestClient(app).get("/api/v1/knowledge")

    assert response.status_code == 503
    assert response.json() == {"detail": "knowledge workspace is not configured"}


def test_versioned_knowledge_graph_api_etag_and_neighborhood(tmp_path: Path) -> None:
    app, vault, _knowledge = _app(tmp_path)
    (vault / "retrieval.md").write_text(
        "# Hybrid Retrieval\n\nRRF combines retrieval results.\n", encoding="utf-8"
    )
    (vault / "harness.md").write_text(
        "# Agent Harness\n\nSee [[Hybrid Retrieval]] and [[Recovery]].\n",
        encoding="utf-8",
    )
    client = TestClient(app)

    status_response = client.get("/api/v1/knowledge/graph/status")
    assert status_response.status_code == 200
    assert status_response.json() == {"status": "unbuilt", "snapshot": None}
    for relative_path in ("retrieval.md", "harness.md"):
        ingested = client.post(
            "/api/v1/knowledge/ingest",
            json={"source_root_id": "sage-learning", "relative_path": relative_path},
        )
        assert ingested.status_code == 201

    graph = client.get("/api/v1/knowledge/graph")
    assert graph.status_code == 200
    assert graph.headers["cache-control"] == "private, no-cache"
    assert graph.headers["etag"].startswith('"')
    payload = graph.json()
    assert payload["snapshot"]["status"] == "ready"
    assert payload["snapshot"]["wiki_watermark"].startswith("kwm_")
    assert payload["snapshot"]["graph_revision"].startswith("kgraph_")
    assert payload["snapshot"]["warning_count"] == 0
    assert any(edge["kind"] == "WIKILINK" for edge in payload["edges"])
    assert all(edge["evidence"] for edge in payload["edges"])

    unchanged = client.get(
        "/api/v1/knowledge/graph", headers={"If-None-Match": graph.headers["etag"]}
    )
    assert unchanged.status_code == 304
    assert unchanged.content == b""

    concepts = client.get("/api/v1/knowledge/graph", params={"kind": "concept"})
    assert concepts.status_code == 200
    assert [node["label"] for node in concepts.json()["nodes"]] == ["Recovery"]
    assert concepts.json()["edges"] == []

    center = next(node for node in payload["nodes"] if node["label"] == "Agent Harness")
    detail = client.get(f"/api/v1/knowledge/graph/nodes/{center['node_id']}")
    assert detail.status_code == 200
    assert detail.json()["node"] == center
    neighbors = client.get(
        f"/api/v1/knowledge/graph/nodes/{center['node_id']}/neighbors",
        params={"limit": 2},
    )
    assert neighbors.status_code == 200
    assert neighbors.json()["center"]["node_id"] == center["node_id"]
    assert len(neighbors.json()["edges"]) == 2

    missing = client.get(
        "/api/v1/knowledge/graph", params={"graph_revision": "kgraph_missing"}
    )
    assert missing.status_code == 404

    goal = client.get("/api/v1/knowledge/goal")
    assert goal.status_code == 200
    assert goal.json()["structured"] is True
    goal_revision = goal.json()["goal_revision"]
    updated_goal = client.put(
        "/api/v1/knowledge/goal",
        json={
            "expected_goal_revision": goal_revision,
            "goal_id": "full-stack-ai-engineer",
            "title": "成为全栈 AI 应用工程师",
            "description": "完成 AI 产品从开发到部署的闭环。",
            "capabilities": [
                {
                    "capability_id": "agent-harness",
                    "label": "Agent Harness",
                    "description": "有状态、可恢复的运行时。",
                    "keywords": ["Agent Harness", "Recovery"],
                    "weight": 1.5,
                    "required": True,
                },
                {
                    "capability_id": "cloud-delivery",
                    "label": "云端交付",
                    "description": "容器化和自动部署。",
                    "keywords": ["Docker", "Kubernetes", "GitHub Actions"],
                    "weight": 1.0,
                    "required": True,
                },
            ],
        },
    )
    assert updated_goal.status_code == 200
    assert updated_goal.json()["goal_revision"] != goal_revision
    conflict = client.put(
        "/api/v1/knowledge/goal",
        json={
            **updated_goal.json(),
            "expected_goal_revision": goal_revision,
        },
    )
    assert conflict.status_code == 409

    communities = client.get("/api/v1/knowledge/graph/communities")
    assert communities.status_code == 200
    community_payload = communities.json()
    assert community_payload["analysis"]["algorithm_id"] == "networkx.louvain"
    assert community_payload["analysis"]["seed"] == 42
    assert community_payload["communities"]
    assert all(
        metric["community_id"] for metric in community_payload["node_metrics"]
    )

    insights = client.get("/api/v1/knowledge/graph/insights")
    assert insights.status_code == 200
    insight_payload = insights.json()
    assert insight_payload["goal"]["goal_id"] == "full-stack-ai-engineer"
    alignments = {item["capability_id"]: item for item in insight_payload["alignments"]}
    assert alignments["agent-harness"]["status"] == "covered"
    assert alignments["cloud-delivery"]["status"] == "gap"
    gaps = client.get(
        "/api/v1/knowledge/graph/insights",
        params={"kind": "capability_gap", "limit": 1},
    )
    assert gaps.status_code == 200
    assert len(gaps.json()["insights"]) == 1
    assert gaps.json()["insights"][0]["kind"] == "capability_gap"


def test_ingest_review_approve_and_rollback_api_contract(tmp_path: Path) -> None:
    app, vault, knowledge = _app(tmp_path)
    note = vault / "harness.md"
    note.write_text("# Agent Harness\n\n可恢复、可审核。\n", encoding="utf-8")
    client = TestClient(app)

    initial = client.get("/api/v1/knowledge")
    assert initial.status_code == 200
    assert initial.headers["cache-control"] == "no-store"
    assert initial.json() == {
        "status": "ready",
        "workspace_name": "knowledge",
        "source_count": 0,
        "wiki_page_count": 0,
        "pending_proposal_count": 0,
        "last_synced_at": None,
        "source_roots": [
            {
                "root_id": "sage-learning",
                "kind": "obsidian",
                "label": "Sage Learning",
            }
        ],
    }
    assert str(vault) not in initial.text
    assert app.state.knowledge_store.database_path == (knowledge / ".sage" / "knowledge.sqlite3")

    ingested = client.post(
        "/api/v1/knowledge/ingest",
        json={"source_root_id": "sage-learning", "relative_path": "harness.md"},
    )
    assert ingested.status_code == 201
    proposal = ingested.json()
    assert proposal["status"] == "approved"
    assert proposal["projection_status"] == "complete"
    assert proposal["policy_decision"]["risk_level"] == "low"
    assert proposal["policy_decision"]["action"] == "auto_apply"
    assert proposal["policy_decision"]["undo_available"] is True
    assert proposal["source_relative_path"] == "harness.md"
    assert proposal["source_revision"].startswith("sha256:")
    assert proposal["diff"] == ""
    assert proposal["diff_truncated"] is False
    assert str(vault) not in ingested.text
    assert (knowledge / proposal["target_path"]).exists()

    listed = client.get("/api/v1/knowledge/proposals", params={"status": "approved"})
    assert listed.status_code == 200
    assert [item["proposal_id"] for item in listed.json()["proposals"]] == [proposal["proposal_id"]]
    detail = client.get(f"/api/v1/knowledge/proposals/{proposal['proposal_id']}").json()
    assert detail["events"][0]["event_type"] == "proposal_created"
    assert detail["parse_artifact"]["artifact_id"] == proposal["parse_artifact_id"]
    assert detail["parse_artifact"]["parser_id"] == "sage.markdown"
    assert detail["parse_artifact"]["parser_version"] == "1.0.0"
    assert detail["parse_artifact"]["source_revision"] == proposal["source_revision"]
    assert detail["parse_artifact"]["block_count"] >= 2
    assert detail["parse_artifact"]["blocks"][0]["block_id"].startswith("pblk_")
    assert "text" not in detail["parse_artifact"]["blocks"][0]
    understanding = detail["source_understanding"]
    assert understanding["artifact_id"] == proposal["parse_artifact_id"]
    assert understanding["generator_id"] == "sage.extractive"
    assert "可恢复、可审核" in understanding["summary"]
    assert understanding["citations"][0]["block_id"].startswith("pblk_")
    assert "text" not in understanding["citations"][0]

    pages = client.get("/api/v1/knowledge/pages")
    assert pages.status_code == 200
    page = pages.json()["pages"][0]
    assert page["title"] == "Agent Harness"
    assert len(page["revisions"]) == 1
    assert (knowledge / page["path"]).is_file()

    rollback = client.post(
        f"/api/v1/knowledge/pages/{page['page_id']}/rollback",
        json={
            "target_revision_id": page["revisions"][0]["revision_id"],
            "expected_page_revision": page["current_revision"],
        },
    )
    assert rollback.status_code == 201
    assert rollback.json()["change_kind"] == "rollback"
    assert rollback.json()["status"] == "pending"
    assert rollback.json()["policy_decision"]["risk_level"] == "high"
    assert rollback.json()["policy_decision"]["action"] == "require_review"


def test_pending_migration_preview_apply_and_conflict_contract(tmp_path: Path) -> None:
    app, vault, knowledge = _app(tmp_path)
    source = vault / "legacy.md"
    source.write_text("# Legacy\n\n自动整理历史知识。\n", encoding="utf-8")
    store = app.state.knowledge_store
    proposal = store.ingest("sage-learning", "legacy.md")
    legacy_id = "legacy_" + proposal.proposal_id.removeprefix("kprop_")
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "DELETE FROM knowledge_source_understandings WHERE artifact_id=?",
            (proposal.parse_artifact_id,),
        )
        connection.execute(
            "DELETE FROM knowledge_parse_artifacts WHERE artifact_id=?",
            (proposal.parse_artifact_id,),
        )
        connection.execute(
            "UPDATE knowledge_proposals SET proposal_id=?, parse_artifact_id=NULL "
            "WHERE proposal_id=?",
            (legacy_id, proposal.proposal_id),
        )
        connection.commit()
    client = TestClient(app)

    preview = client.get("/api/v1/knowledge/migrations/pending")

    assert preview.status_code == 200
    plan = preview.json()
    assert plan["total"] == 1
    assert plan["auto_apply_count"] == 1
    assert plan["review_count"] == 0
    assert plan["items"][0]["proposal_id"] == legacy_id
    assert plan["items"][0]["disposition"] == "auto_apply"
    assert str(vault) not in preview.text

    source.write_text("# Legacy\n\n计划后变化。\n", encoding="utf-8")
    stale = client.post(
        "/api/v1/knowledge/migrations/pending/apply",
        json={"expected_plan_id": plan["plan_id"]},
    )
    assert stale.status_code == 409
    assert stale.json() == {"detail": "knowledge migration plan changed"}

    current = client.get("/api/v1/knowledge/migrations/pending").json()
    applied = client.post(
        "/api/v1/knowledge/migrations/pending/apply",
        json={"expected_plan_id": current["plan_id"]},
    )
    assert applied.status_code == 200
    result = applied.json()
    assert result["status"] == "completed"
    assert result["retired_count"] == 1
    assert result["error_count"] == 0
    assert client.get("/api/v1/knowledge/migrations/pending").json()["total"] == 0
    assert not list((knowledge / "wiki" / "sources").glob("*.md"))


def test_index_status_and_rebuild_api_contract(tmp_path: Path) -> None:
    app, vault, _ = _app(tmp_path)
    (vault / "memory.md").write_text(
        "# Memory\n\n长期记忆使用事实证据和动态 TTL。\n",
        encoding="utf-8",
    )
    client = TestClient(app)
    ingested = client.post(
        "/api/v1/knowledge/ingest",
        json={"source_root_id": "sage-learning", "relative_path": "memory.md"},
    )
    assert ingested.status_code == 201

    status_response = client.get("/api/v1/knowledge/index")

    assert status_response.status_code == 200
    assert status_response.headers["cache-control"] == "no-store"
    status_body = status_response.json()
    assert status_body["status"] == "ready"
    assert status_body["backend"] == "sqlite-fts5+hashing"
    assert status_body["revision_count"] == 1
    assert status_body["indexed_revision_count"] == 1
    assert status_body["active_chunk_count"] == 1
    assert status_body["error_count"] == 0

    rebuilt = client.post("/api/v1/knowledge/index/rebuild")
    assert rebuilt.status_code == 200
    assert rebuilt.json() == status_body


def test_search_api_returns_bounded_revision_citations_and_no_evidence(tmp_path: Path) -> None:
    app, vault, _ = _app(tmp_path)
    (vault / "memory.md").write_text(
        "# Memory\n\n长期记忆使用事实证据和动态 TTL。\n",
        encoding="utf-8",
    )
    client = TestClient(app)
    client.post(
        "/api/v1/knowledge/ingest",
        json={"source_root_id": "sage-learning", "relative_path": "memory.md"},
    )

    found = client.post(
        "/api/v1/knowledge/search",
        json={"query": "长期记忆 TTL", "top_k": 4, "token_budget": 512},
    )

    assert found.status_code == 200
    assert found.headers["cache-control"] == "no-store"
    body = found.json()
    assert body["status"] == "evidence_found"
    assert body["used_tokens"] <= 512
    assert body["citations"][0]["citation_id"].startswith("kcite_")
    assert body["citations"][0]["page_revision"].startswith("krev_")
    assert body["citations"][0]["source_relative_path"] == "memory.md"
    assert str(vault) not in found.text

    citation = client.get(
        f"/api/v1/knowledge/citations/{body['citations'][0]['citation_id']}"
    )
    assert citation.status_code == 200
    assert citation.headers["cache-control"] == "no-store"
    citation_body = citation.json()
    assert citation_body["chunk_id"] == body["citations"][0]["chunk_id"]
    assert citation_body["page_revision"] == body["citations"][0]["page_revision"]
    assert citation_body["source_revision"] == body["citations"][0]["source_revision"]
    assert citation_body["source_relative_path"] == "memory.md"
    assert "长期记忆使用事实证据" in citation_body["excerpt"]
    assert citation_body["truncated"] is False
    assert str(vault) not in citation.text

    stale_citation = client.get(
        "/api/v1/knowledge/citations/kcite_00000000000000000000000000000000"
    )
    assert stale_citation.status_code == 404
    malformed_citation = client.get("/api/v1/knowledge/citations/not-a-citation")
    assert malformed_citation.status_code == 422

    learned = client.post(
        "/api/v1/knowledge/learnings",
        json={
            "topic": "长期记忆策略",
            "citation_ids": [body["citations"][0]["citation_id"]],
            "session_id": "browser-test",
        },
    )
    assert learned.status_code == 201
    learning = learned.json()
    assert learning["status"] == "approved"
    assert learning["projection_status"] == "complete"
    assert learning["change_kind"] == "learning"
    assert learning["target_path"].startswith("wiki/learnings/")
    assert learning["policy_decision"]["action"] == "auto_apply"
    assert learning["policy_decision"]["undo_available"] is True
    assert str(vault) not in learned.text

    stale = client.post(
        "/api/v1/knowledge/learnings",
        json={"topic": "伪造学习", "citation_ids": ["kcite_missing"]},
    )
    assert stale.status_code == 409

    missing = client.post(
        "/api/v1/knowledge/search",
        json={"query": "zxqv_9f87ab completely_unrelated_needle"},
    )
    assert missing.status_code == 200
    assert missing.json()["status"] == "no_evidence"
    assert missing.json()["citations"] == []


def test_knowledge_api_rejects_unsafe_paths_and_stale_revisions(tmp_path: Path) -> None:
    app, vault, _ = _app(tmp_path)
    (vault / "note.md").write_text("# Note\n", encoding="utf-8")
    client = TestClient(app)

    traversal = client.post(
        "/api/v1/knowledge/ingest",
        json={"source_root_id": "sage-learning", "relative_path": "../note.md"},
    )
    assert traversal.status_code == 400
    assert traversal.json() == {"detail": "invalid relative source path"}

    proposal = client.post(
        "/api/v1/knowledge/ingest",
        json={"source_root_id": "sage-learning", "relative_path": "note.md"},
    ).json()
    stale = client.post(
        f"/api/v1/knowledge/proposals/{proposal['proposal_id']}/undo-auto-apply",
        json={"expected_page_revision": "krev_stale"},
    )
    assert stale.status_code == 409
    assert stale.json() == {"detail": "knowledge revision conflict"}


def test_auto_applied_ingest_can_be_undone_once_through_api(tmp_path: Path) -> None:
    app, vault, knowledge = _app(tmp_path)
    (vault / "note.md").write_text("# Note\n\n可撤销。\n", encoding="utf-8")
    client = TestClient(app)
    proposal = client.post(
        "/api/v1/knowledge/ingest",
        json={"source_root_id": "sage-learning", "relative_path": "note.md"},
    ).json()

    undone = client.post(
        f"/api/v1/knowledge/proposals/{proposal['proposal_id']}/undo-auto-apply",
        json={
            "expected_page_revision": proposal["policy_decision"]["applied_page_revision"]
        },
    )

    assert undone.status_code == 200
    assert undone.json()["change_kind"] == "retraction"
    detail = client.get(
        f"/api/v1/knowledge/proposals/{proposal['proposal_id']}"
    ).json()
    assert detail["proposal"]["policy_decision"]["undo_available"] is False
    assert detail["proposal"]["policy_decision"]["undo_proposal_id"] == undone.json()[
        "proposal_id"
    ]
    page = client.get("/api/v1/knowledge/pages").json()["pages"][0]
    assert page["revisions"][-1]["change_kind"] == "retraction"
    assert "此自动沉淀已由用户撤销" in (knowledge / page["path"]).read_text(
        encoding="utf-8"
    )


def test_workspace_synthesis_api_requires_approved_sources_and_returns_evidence(
    tmp_path: Path,
) -> None:
    app, vault, _ = _app(tmp_path)
    client = TestClient(app)

    empty = client.post("/api/v1/knowledge/synthesis")
    assert empty.status_code == 409

    (vault / "source.md").write_text("# Source\n\nApproved evidence.\n", encoding="utf-8")
    ingested = client.post(
        "/api/v1/knowledge/ingest",
        json={"source_root_id": "sage-learning", "relative_path": "source.md"},
    ).json()
    client.post(
        f"/api/v1/knowledge/proposals/{ingested['proposal_id']}/approve",
        json={"expected_revision": 0},
    )

    created = client.post("/api/v1/knowledge/synthesis")
    assert created.status_code == 201
    assert created.json()["change_kind"] == "synthesis"
    assert created.json()["policy_decision"]["risk_level"] == "medium"
    assert created.json()["policy_decision"]["action"] == "draft"
    detail = client.get(
        f"/api/v1/knowledge/proposals/{created.json()['proposal_id']}"
    ).json()
    assert detail["parse_artifact"] is None
    assert detail["source_understanding"] is None
    synthesis = detail["workspace_synthesis"]
    assert synthesis["generator_id"] == "sage.workspace-index"
    assert len(synthesis["sources"]) == 1
    assert synthesis["sources"][0]["proposal_id"] == ingested["proposal_id"]
    assert synthesis["sources"][0]["citation_block_ids"]
