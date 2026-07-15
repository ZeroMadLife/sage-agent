"""Deterministic, bounded projection for the Sage personal assistant home."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import quote

from core.coding.memory.durable import workspace_id_from_path
from core.coding.persistence import MemoryStore, MemoryStoreError

HomeSectionStatus = Literal["ready", "empty", "not_configured", "unavailable", "error"]
HomeIdentityMode = Literal["local", "cloud"]
HomeActionKind = Literal["chat", "knowledge", "review", "project"]

_SESSION_SCAN_LIMIT = 500
_RECENT_SESSION_LIMIT = 6
_RECENT_PROJECT_LIMIT = 6
_MEMORY_DATABASE_NAME = "memory.sqlite3"


class SessionStoreReader(Protocol):
    """Minimal persisted-session surface required by the home projection."""

    def list_sessions(
        self, limit: int = 30, *, include_archived: bool = False
    ) -> list[dict[str, Any]]: ...

    def load(self, session_id: str) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class HomeIdentity:
    mode: HomeIdentityMode
    user_id: str | None
    display_name: str

    @classmethod
    def local(cls) -> HomeIdentity:
        return cls(mode="local", user_id=None, display_name="本地工作区")

    @classmethod
    def cloud(cls, user_id: str, display_name: str) -> HomeIdentity:
        normalized_name = display_name.strip() or "Sage 用户"
        return cls(mode="cloud", user_id=user_id, display_name=normalized_name)


@dataclass(frozen=True, slots=True)
class HomeKnowledge:
    status: HomeSectionStatus = "not_configured"
    source_count: int = 0
    wiki_page_count: int = 0
    last_synced_at: str | None = None


@dataclass(frozen=True, slots=True)
class HomeRecentSession:
    session_id: str
    title: str
    workspace_name: str
    updated_at: str
    message_count: int
    target: str


@dataclass(frozen=True, slots=True)
class HomeSessions:
    status: HomeSectionStatus
    items: tuple[HomeRecentSession, ...]
    total: int
    error: str | None = None


@dataclass(frozen=True, slots=True)
class HomeProject:
    project_id: str
    name: str


@dataclass(frozen=True, slots=True)
class HomeProjects:
    status: HomeSectionStatus
    items: tuple[HomeProject, ...]
    total: int
    error: str | None = None


@dataclass(frozen=True, slots=True)
class HomeProposalSummary:
    status: HomeSectionStatus
    memory_pending: int
    wiki_pending: int = 0
    note_pending: int = 0
    error: str | None = None


@dataclass(frozen=True, slots=True)
class HomeAction:
    id: str
    kind: HomeActionKind
    label: str
    description: str
    target: str


@dataclass(frozen=True, slots=True)
class AssistantHomeSummary:
    identity: HomeIdentity
    knowledge: HomeKnowledge
    sessions: HomeSessions
    projects: HomeProjects
    proposals: HomeProposalSummary
    suggested_actions: tuple[HomeAction, ...]


@dataclass(frozen=True, slots=True)
class _VisibleSession:
    summary: HomeRecentSession
    workspace_root: Path


class AssistantHomeSummaryService:
    """Project existing canonical stores without creating new product state."""

    def __init__(self, session_store: SessionStoreReader, storage_root: Path) -> None:
        self._session_store = session_store
        self._storage_root = storage_root.resolve()

    def build(
        self,
        identity: HomeIdentity,
        *,
        projects: Sequence[HomeProject] | None = None,
        project_error: bool = False,
    ) -> AssistantHomeSummary:
        sessions, visible = self._sessions(identity)
        projected_projects = self._projects(projects, project_error=project_error)
        proposals = self._proposals(visible, sessions)
        knowledge = HomeKnowledge()
        return AssistantHomeSummary(
            identity=identity,
            knowledge=knowledge,
            sessions=sessions,
            projects=projected_projects,
            proposals=proposals,
            suggested_actions=self._actions(knowledge, sessions, proposals),
        )

    def _sessions(
        self, identity: HomeIdentity
    ) -> tuple[HomeSessions, tuple[_VisibleSession, ...]]:
        try:
            candidates = self._session_store.list_sessions(
                limit=_SESSION_SCAN_LIMIT, include_archived=False
            )
        except (OSError, ValueError):
            return (
                HomeSessions(
                    status="error",
                    items=(),
                    total=0,
                    error="近期对话暂不可用",
                ),
                (),
            )

        visible: list[_VisibleSession] = []
        for candidate in candidates:
            session_id = str(candidate.get("session_id", "")).strip()
            if not session_id:
                continue
            try:
                state = self._session_store.load(session_id)
            except (OSError, ValueError):
                continue
            if not self._is_visible_owner(state, identity):
                continue
            workspace_root = Path(str(state.get("workspace_root", ""))).resolve()
            visible.append(
                _VisibleSession(
                    summary=HomeRecentSession(
                        session_id=session_id,
                        title=str(candidate.get("title", "新会话"))[:120],
                        workspace_name=workspace_root.name or "工作区",
                        updated_at=str(candidate.get("updated_at", "")),
                        message_count=max(0, int(candidate.get("message_count", 0))),
                        target=f"/coding/session/{quote(session_id, safe='')}",
                    ),
                    workspace_root=workspace_root,
                )
            )
        visible.sort(key=lambda item: item.summary.updated_at, reverse=True)
        recent = tuple(item.summary for item in visible[:_RECENT_SESSION_LIMIT])
        return (
            HomeSessions(
                status="ready" if recent else "empty",
                items=recent,
                total=len(visible),
            ),
            tuple(visible),
        )

    @staticmethod
    def _is_visible_owner(state: Mapping[str, Any], identity: HomeIdentity) -> bool:
        owner = str(state.get("owner_user_id") or "").strip() or None
        if identity.mode == "cloud":
            return owner is not None and owner == identity.user_id
        return owner is None

    @staticmethod
    def _projects(
        projects: Sequence[HomeProject] | None, *, project_error: bool
    ) -> HomeProjects:
        if project_error:
            return HomeProjects(
                status="error", items=(), total=0, error="项目摘要暂不可用"
            )
        if projects is None:
            return HomeProjects(status="unavailable", items=(), total=0)
        ordered = tuple(projects[:_RECENT_PROJECT_LIMIT])
        return HomeProjects(
            status="ready" if ordered else "empty",
            items=ordered,
            total=len(projects),
        )

    def _proposals(
        self,
        visible: tuple[_VisibleSession, ...],
        sessions: HomeSessions,
    ) -> HomeProposalSummary:
        if sessions.status == "error":
            return HomeProposalSummary(
                status="unavailable",
                memory_pending=0,
                error="待确认沉淀暂不可用",
            )
        sessions_by_workspace: dict[Path, set[str]] = {}
        for item in visible:
            sessions_by_workspace.setdefault(item.workspace_root, set()).add(
                item.summary.session_id
            )
        proposal_ids: set[str] = set()
        try:
            for workspace_root, session_ids in sessions_by_workspace.items():
                workspace_id = workspace_id_from_path(workspace_root)
                database = (
                    self._storage_root
                    / "memory"
                    / workspace_id
                    / _MEMORY_DATABASE_NAME
                )
                if not database.is_file():
                    continue
                store = MemoryStore(self._storage_root, workspace_id)
                proposal_ids.update(
                    proposal.proposal_id
                    for proposal in store.list_proposals("pending")
                    if proposal.session_id in session_ids
                )
        except (MemoryStoreError, OSError, ValueError):
            return HomeProposalSummary(
                status="error",
                memory_pending=0,
                error="待确认沉淀暂不可用",
            )
        return HomeProposalSummary(
            status="ready" if proposal_ids else "empty",
            memory_pending=len(proposal_ids),
        )

    @staticmethod
    def _actions(
        knowledge: HomeKnowledge,
        sessions: HomeSessions,
        proposals: HomeProposalSummary,
    ) -> tuple[HomeAction, ...]:
        actions: list[HomeAction] = []
        if proposals.memory_pending:
            actions.append(
                HomeAction(
                    id="review-memory",
                    kind="review",
                    label="查看待确认沉淀",
                    description=f"有 {proposals.memory_pending} 条记忆提案等待处理。",
                    target="/evolution",
                )
            )
        if knowledge.status == "not_configured":
            actions.append(
                HomeAction(
                    id="connect-knowledge",
                    kind="knowledge",
                    label="规划第一个知识空间",
                    description="了解如何从 Markdown、Obsidian 和 GitHub 构建长期 Wiki。",
                    target="/knowledge",
                )
            )
        if sessions.items:
            latest = sessions.items[0]
            actions.append(
                HomeAction(
                    id="continue-conversation",
                    kind="chat",
                    label="继续最近对话",
                    description=latest.title,
                    target=latest.target,
                )
            )
        else:
            actions.append(
                HomeAction(
                    id="start-conversation",
                    kind="chat",
                    label="开始一次学习对话",
                    description="让 Sage 帮你理解项目、整理资料或制定练习。",
                    target="/assistant?action=compose",
                )
            )
        return tuple(actions[:4])
