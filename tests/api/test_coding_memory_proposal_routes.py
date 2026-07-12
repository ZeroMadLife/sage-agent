"""REST coverage for persisted, session-scoped memory proposals."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from core.coding.persistence import MemoryCandidate


class FakeModel:
    async def complete(self, prompt: str) -> str:
        _ = prompt
        return "<final>ok</final>"


def _app(tmp_path: Path, *, workspace: Path | None = None):
    root = workspace or tmp_path / "workspace"
    root.mkdir(parents=True, exist_ok=True)
    return create_app(
        coding_model_factory=FakeModel,
        coding_workspace_root=root,
        coding_storage_root=tmp_path / ".coding",
    )


def _session(client: TestClient, *, workspace_root: Path | None = None) -> str:
    payload = {"workspace_root": str(workspace_root)} if workspace_root else {}
    response = client.post("/api/v1/coding/session", json=payload)
    assert response.status_code == 200
    return str(response.json()["session_id"])


def _proposal(app, session_id: str, proposal_id: str = "proposal-1"):
    runtime = app.state.coding_sessions[session_id]
    return runtime.memory_manager.create_proposal(
        [
            MemoryCandidate(
                content="Use SQLite as canonical memory evidence",
                topic="decisions",
                source="dream_proposal",
                source_ref="reflection-1",
                created_at="2026-07-12T00:00:00+00:00",
            )
        ],
        session_id=session_id,
        run_id="run-1",
        reflection_id="reflection-1",
        proposal_id=proposal_id,
    )


def test_list_detail_and_status_filter_use_wire_contract(tmp_path: Path) -> None:
    app = _app(tmp_path)
    client = TestClient(app)
    session_id = _session(client)
    proposal = _proposal(app, session_id)

    listed = client.get(
        f"/api/v1/coding/{session_id}/memory/proposals", params={"status": "pending"}
    )
    assert listed.status_code == 200
    assert listed.json() == {
        "proposals": [
            {
                "proposal_id": proposal.proposal_id,
                "workspace_id": proposal.workspace_id,
                "session_id": session_id,
                "run_id": "run-1",
                "reflection_id": "reflection-1",
                "status": "pending",
                "projection_status": "pending",
                "revision": 0,
                "base_revision": 0,
                "candidate_count": 1,
                "candidates": [
                    {
                        "content": "Use SQLite as canonical memory evidence",
                        "topic": "decisions",
                        "source": "dream_proposal",
                        "source_ref": "reflection-1",
                        "created_at": "2026-07-12T00:00:00+00:00",
                    }
                ],
                "created_at": proposal.created_at,
                "updated_at": proposal.updated_at,
            }
        ]
    }

    detail = client.get(
        f"/api/v1/coding/{session_id}/memory/proposals/{proposal.proposal_id}"
    )
    assert detail.status_code == 200
    body = detail.json()
    assert body["proposal"] == listed.json()["proposals"][0]
    assert [event["event_type"] for event in body["events"]] == ["proposal_created"]
    event = body["events"][0]
    assert event["proposal_id"] == proposal.proposal_id
    assert event["session_id"] == session_id
    assert event["run_id"] == "run-1"
    assert event["reflection_id"] == "reflection-1"
    assert event["candidate_count"] == 1
    assert event["base_revision"] == 0
    assert event["revision"] == 0

    assert client.get(
        f"/api/v1/coding/{session_id}/memory/proposals",
        params={"status": "approved"},
    ).json() == {"proposals": []}


@pytest.mark.parametrize("action,status", [("approve", "approved"), ("reject", "rejected")])
def test_transition_by_id_is_persisted_and_idempotent(
    tmp_path: Path, action: str, status: str
) -> None:
    app = _app(tmp_path)
    client = TestClient(app)
    session_id = _session(client)
    proposal = _proposal(app, session_id)
    url = f"/api/v1/coding/{session_id}/memory/proposals/{proposal.proposal_id}/{action}"

    transitioned = client.post(url, json={"expected_revision": 0})
    assert transitioned.status_code == 200
    assert transitioned.json()["status"] == status
    assert transitioned.json()["revision"] == 1

    replay = client.post(url, json={"expected_revision": 1})
    assert replay.status_code == 200
    assert replay.json() == transitioned.json()


def test_restart_can_list_and_approve_without_in_memory_pending_state(tmp_path: Path) -> None:
    app = _app(tmp_path)
    client = TestClient(app)
    session_id = _session(client)
    proposal = _proposal(app, session_id)
    app.state.coding_sessions[session_id]._save_session()

    restarted = _app(tmp_path)
    restarted_client = TestClient(restarted)
    resumed = restarted_client.post(f"/api/v1/coding/session/{session_id}/resume")
    assert resumed.status_code == 200
    runtime = restarted.state.coding_sessions[session_id]
    runtime.memory_manager._pending_proposal = None
    runtime.memory_manager._proposal_id = ""

    assert restarted_client.get(
        f"/api/v1/coding/{session_id}/memory/proposals"
    ).json()["proposals"][0]["proposal_id"] == proposal.proposal_id
    approved = restarted_client.post(
        f"/api/v1/coding/{session_id}/memory/proposals/{proposal.proposal_id}/approve",
        json={"expected_revision": 0},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"


def test_revision_and_status_conflicts_are_409_and_errors_are_redacted(tmp_path: Path) -> None:
    app = _app(tmp_path)
    client = TestClient(app)
    session_id = _session(client)
    proposal = _proposal(app, session_id)
    url = f"/api/v1/coding/{session_id}/memory/proposals/{proposal.proposal_id}"

    stale = client.post(f"{url}/approve", json={"expected_revision": 9})
    assert stale.status_code == 409
    assert stale.json() == {"detail": "memory proposal conflict"}

    assert client.post(f"{url}/approve", json={"expected_revision": 0}).status_code == 200
    wrong_terminal = client.post(f"{url}/reject", json={"expected_revision": 1})
    assert wrong_terminal.status_code == 409
    assert wrong_terminal.json() == {"detail": "memory proposal conflict"}


def test_unknown_cross_workspace_and_cross_session_proposals_are_404(tmp_path: Path) -> None:
    root = tmp_path / "workspaces"
    first_workspace = root / "first"
    second_workspace = root / "second"
    app = _app(tmp_path, workspace=root)
    first_workspace.mkdir(parents=True)
    second_workspace.mkdir(parents=True)
    client = TestClient(app)
    first = _session(client, workspace_root=first_workspace)
    same_workspace_other_session = _session(client, workspace_root=first_workspace)
    second = _session(client, workspace_root=second_workspace)
    proposal = _proposal(app, first)

    for scoped_session in (same_workspace_other_session, second):
        path = (
            f"/api/v1/coding/{scoped_session}/memory/proposals/{proposal.proposal_id}"
        )
        assert client.get(path).status_code == 404
        assert client.post(
            f"{path}/approve", json={"expected_revision": 0}
        ).status_code == 404

    unknown = f"/api/v1/coding/{first}/memory/proposals/not-found"
    assert client.get(unknown).status_code == 404
    assert client.post(
        f"{unknown}/reject", json={"expected_revision": 0}
    ).status_code == 404


def test_legacy_routes_require_explicit_id_and_revision_and_use_cas(tmp_path: Path) -> None:
    app = _app(tmp_path)
    client = TestClient(app)
    session_id = _session(client)
    proposal = _proposal(app, session_id)
    url = f"/api/v1/coding/{session_id}/memory/proposal/approve"

    missing = client.post(url, json={})
    assert missing.status_code == 422
    approved = client.post(
        url,
        json={"proposal_id": proposal.proposal_id, "expected_revision": 0},
    )
    assert approved.status_code == 200
    assert approved.headers["deprecation"] == "true"
    assert approved.json()["status"] == "approved"
