"""Deterministic home summary coverage for the V7 personal assistant."""

from pathlib import Path

from core.assistant.home import AssistantHomeSummaryService, HomeIdentity, HomeProject
from core.coding.memory.durable import workspace_id_from_path
from core.coding.persistence import CodingSessionStore, MemoryCandidate, MemoryStore


def _save_session(
    store: CodingSessionStore,
    *,
    session_id: str,
    workspace_root: Path,
    updated_at: str,
    owner_user_id: str | None = None,
) -> None:
    store.save(
        {
            "id": session_id,
            "workspace_root": str(workspace_root),
            "owner_user_id": owner_user_id,
            "created_at": "2026-07-15T00:00:00+00:00",
            "updated_at": updated_at,
            "history": [
                {
                    "role": "user",
                    "content": f"学习 {session_id}",
                    "created_at": updated_at,
                }
            ],
            "runtime_mode": {"mode": "default"},
        }
    )


def test_local_summary_is_empty_without_creating_knowledge_state(tmp_path: Path) -> None:
    store = CodingSessionStore(tmp_path / "storage" / "sessions")
    service = AssistantHomeSummaryService(store, tmp_path / "storage")

    summary = service.build(HomeIdentity.local())

    assert summary.identity.mode == "local"
    assert summary.identity.display_name == "本地工作区"
    assert summary.knowledge.status == "not_configured"
    assert summary.knowledge.source_count == 0
    assert summary.sessions.status == "empty"
    assert summary.sessions.items == ()
    assert summary.projects.status == "unavailable"
    assert summary.proposals.memory_pending == 0
    assert [action.id for action in summary.suggested_actions] == [
        "connect-knowledge",
        "start-conversation",
    ]
    assert not (tmp_path / "storage" / "memory").exists()


def test_summary_filters_owner_sorts_recent_and_deduplicates_proposals(
    tmp_path: Path,
) -> None:
    storage_root = tmp_path / "storage"
    session_store = CodingSessionStore(storage_root / "sessions")
    first_workspace = tmp_path / "workspaces" / "first"
    second_workspace = tmp_path / "workspaces" / "second"
    first_workspace.mkdir(parents=True)
    second_workspace.mkdir(parents=True)
    _save_session(
        session_store,
        session_id="older",
        workspace_root=first_workspace,
        updated_at="2026-07-15T01:00:00+00:00",
        owner_user_id="user-a",
    )
    _save_session(
        session_store,
        session_id="newer",
        workspace_root=first_workspace,
        updated_at="2026-07-15T03:00:00+00:00",
        owner_user_id="user-a",
    )
    _save_session(
        session_store,
        session_id="other-user",
        workspace_root=second_workspace,
        updated_at="2026-07-15T04:00:00+00:00",
        owner_user_id="user-b",
    )
    first_memory = MemoryStore(storage_root, workspace_id_from_path(first_workspace))
    first_memory.create_proposal(
        [MemoryCandidate(content="保留可追溯引用", topic="decisions")],
        session_id="newer",
        proposal_id="visible-proposal",
    )
    second_memory = MemoryStore(storage_root, workspace_id_from_path(second_workspace))
    second_memory.create_proposal(
        [MemoryCandidate(content="另一个用户的记忆", topic="decisions")],
        session_id="other-user",
        proposal_id="hidden-proposal",
    )
    service = AssistantHomeSummaryService(session_store, storage_root)

    summary = service.build(
        HomeIdentity.cloud("user-a", "Sage Owner"),
        projects=(HomeProject(project_id="project-a", name="Sage"),),
    )

    assert [item.session_id for item in summary.sessions.items] == ["newer", "older"]
    assert summary.sessions.total == 2
    assert summary.sessions.items[0].workspace_name == "first"
    assert [project.project_id for project in summary.projects.items] == ["project-a"]
    assert summary.proposals.memory_pending == 1
    assert summary.proposals.status == "ready"
    assert "other-user" not in repr(summary)
    assert "另一个用户的记忆" not in repr(summary)


class _BrokenSessionStore:
    def list_sessions(self, limit: int, *, include_archived: bool = False):
        _ = limit, include_archived
        raise OSError("/private/server/path should not escape")


def test_session_failure_is_redacted_and_does_not_break_other_sections(
    tmp_path: Path,
) -> None:
    service = AssistantHomeSummaryService(_BrokenSessionStore(), tmp_path / "storage")

    summary = service.build(
        HomeIdentity.cloud("user-a", "Sage Owner"),
        projects=(HomeProject(project_id="project-a", name="Sage"),),
    )

    assert summary.sessions.status == "error"
    assert summary.sessions.error == "近期对话暂不可用"
    assert summary.projects.status == "ready"
    assert summary.projects.items[0].name == "Sage"
    assert summary.proposals.status == "unavailable"
    assert "/private/server/path" not in repr(summary)
