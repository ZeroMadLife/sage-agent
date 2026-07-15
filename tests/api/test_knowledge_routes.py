"""V7.2 auditable Knowledge Workspace API coverage."""

from __future__ import annotations

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
    assert proposal["status"] == "pending"
    assert proposal["source_relative_path"] == "harness.md"
    assert proposal["source_revision"].startswith("sha256:")
    assert "可恢复、可审核" in proposal["diff"]
    assert proposal["diff_truncated"] is False
    assert str(vault) not in ingested.text
    assert not (knowledge / proposal["target_path"]).exists()

    listed = client.get("/api/v1/knowledge/proposals", params={"status": "pending"})
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

    approved = client.post(
        f"/api/v1/knowledge/proposals/{proposal['proposal_id']}/approve",
        json={"expected_revision": 0},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["projection_status"] == "complete"

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
        f"/api/v1/knowledge/proposals/{proposal['proposal_id']}/approve",
        json={"expected_revision": 7},
    )
    assert stale.status_code == 409
    assert stale.json() == {"detail": "knowledge revision conflict"}


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
