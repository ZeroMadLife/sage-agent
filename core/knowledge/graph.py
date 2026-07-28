"""Versioned local graph projection derived from canonical Knowledge revisions."""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import unquote, urlsplit

_PROJECTOR_ID = "sage.local-knowledge-graph"
_PROJECTOR_VERSION = "1.2.0"
_GRAPH_CONFIG = {
    "edge_kinds": ["EVIDENCED_BY", "SHARES_SOURCE", "WIKILINK"],
    "link_syntax": ["wikilink", "markdown_internal"],
    "unresolved_wikilinks": "concept",
}
_GRAPH_SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_graph_snapshots (
    graph_revision TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    wiki_watermark TEXT NOT NULL,
    projector_id TEXT NOT NULL,
    projector_version TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    node_count INTEGER NOT NULL,
    edge_count INTEGER NOT NULL,
    warning_count INTEGER NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS knowledge_graph_snapshots_current_idx
    ON knowledge_graph_snapshots(workspace_id, wiki_watermark, status, completed_at);
CREATE TABLE IF NOT EXISTS knowledge_graph_nodes (
    graph_revision TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    page_id TEXT,
    page_revision TEXT,
    source_id TEXT,
    source_revision TEXT,
    properties_json TEXT NOT NULL,
    PRIMARY KEY(graph_revision, node_id)
);
CREATE INDEX IF NOT EXISTS knowledge_graph_nodes_filter_idx
    ON knowledge_graph_nodes(graph_revision, kind, label, node_id);
CREATE TABLE IF NOT EXISTS knowledge_graph_edges (
    graph_revision TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    edge_id TEXT NOT NULL,
    source_node_id TEXT NOT NULL,
    target_node_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    directed INTEGER NOT NULL,
    weight REAL NOT NULL,
    confidence REAL NOT NULL,
    extractor_id TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    properties_json TEXT NOT NULL,
    PRIMARY KEY(graph_revision, edge_id)
);
CREATE INDEX IF NOT EXISTS knowledge_graph_edges_source_idx
    ON knowledge_graph_edges(graph_revision, source_node_id, kind);
CREATE INDEX IF NOT EXISTS knowledge_graph_edges_target_idx
    ON knowledge_graph_edges(graph_revision, target_node_id, kind);
CREATE TABLE IF NOT EXISTS knowledge_graph_edge_evidence (
    graph_revision TEXT NOT NULL,
    edge_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    citation_id TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    page_id TEXT NOT NULL,
    page_revision TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_revision TEXT NOT NULL,
    PRIMARY KEY(graph_revision, edge_id, ordinal)
);
CREATE INDEX IF NOT EXISTS knowledge_graph_evidence_citation_idx
    ON knowledge_graph_edge_evidence(graph_revision, citation_id);
"""
_WIKILINK = re.compile(r"(?<!!)\[\[([^\]\n]{1,512})\]\]")
_MARKDOWN_LINK = re.compile(r"(?<!!)\[([^\]\n]{1,512})\]\(([^)\n]{1,1024})\)")
_MARKDOWN_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$")


class KnowledgeGraphError(RuntimeError):
    """A graph snapshot could not be built or read safely."""


@dataclass(frozen=True, slots=True)
class KnowledgeGraphSnapshot:
    graph_revision: str
    workspace_id: str
    wiki_watermark: str
    projector_id: str
    projector_version: str
    config_hash: str
    status: str
    node_count: int
    edge_count: int
    warning_count: int
    error: str | None
    created_at: str
    completed_at: str | None
    stale: bool = False


@dataclass(frozen=True, slots=True)
class KnowledgeGraphNode:
    node_id: str
    kind: str
    label: str
    page_id: str | None
    page_revision: str | None
    source_id: str | None
    source_revision: str | None
    properties: dict[str, Any]


@dataclass(frozen=True, slots=True)
class KnowledgeGraphEvidence:
    citation_id: str
    chunk_id: str
    page_id: str
    page_revision: str
    source_id: str
    source_revision: str


@dataclass(frozen=True, slots=True)
class KnowledgeGraphEdge:
    edge_id: str
    source_node_id: str
    target_node_id: str
    kind: str
    directed: bool
    weight: float
    confidence: float
    extractor_id: str
    extractor_version: str
    properties: dict[str, Any]
    evidence: tuple[KnowledgeGraphEvidence, ...]


@dataclass(frozen=True, slots=True)
class KnowledgeGraphOverview:
    snapshot: KnowledgeGraphSnapshot
    nodes: tuple[KnowledgeGraphNode, ...]
    edges: tuple[KnowledgeGraphEdge, ...]
    offset: int
    next_offset: int | None
    has_more: bool


@dataclass(frozen=True, slots=True)
class KnowledgeGraphNeighborhood:
    snapshot: KnowledgeGraphSnapshot
    center: KnowledgeGraphNode
    nodes: tuple[KnowledgeGraphNode, ...]
    edges: tuple[KnowledgeGraphEdge, ...]


@dataclass(frozen=True, slots=True)
class KnowledgeGraphRelationPath:
    """One evidence-bound, one-hop path between current Knowledge pages."""

    graph_revision: str
    seed_page_id: str
    seed_source_relative_path: str
    target_page_id: str
    target_page_revision: str
    target_source_relative_path: str
    direction: Literal["outbound", "inbound"]
    score: float
    edge: KnowledgeGraphEdge


@dataclass(frozen=True, slots=True)
class _PageInput:
    page_id: str
    path: str
    title: str
    page_revision: str
    content: str
    source_id: str
    source_revision: str
    source_kind: str
    source_relative_path: str
    chunks: tuple[_PageChunkInput, ...]


@dataclass(frozen=True, slots=True)
class _PageChunkInput:
    text: str
    evidence: KnowledgeGraphEvidence


@dataclass(frozen=True, slots=True)
class _ProjectedEdge:
    edge: KnowledgeGraphEdge


@dataclass(frozen=True, slots=True)
class _PageLink:
    target: str
    count: int
    anchors: tuple[str, ...]
    contexts: tuple[str, ...]
    lines: tuple[str, ...]
    syntaxes: tuple[str, ...]


class LocalKnowledgeGraph:
    """Build immutable graph snapshots from current SQLite-backed Git Wiki revisions."""

    def __init__(self, *, workspace_id: str = "knowledge-local") -> None:
        self.workspace_id = workspace_id
        self.projector_id = _PROJECTOR_ID
        self.projector_version = _PROJECTOR_VERSION
        self.config_hash = _stable_hash(_canonical_json(_GRAPH_CONFIG))

    def ensure_schema(self, connection: sqlite3.Connection) -> None:
        connection.executescript(_GRAPH_SCHEMA)

    def wiki_watermark(self, connection: sqlite3.Connection) -> str:
        rows = connection.execute(
            "SELECT page_id, current_revision FROM knowledge_pages ORDER BY page_id"
        ).fetchall()
        payload = "\n".join(f"{row['page_id']}\0{row['current_revision']}" for row in rows)
        return "kwm_" + _stable_hash(payload)[:32]

    def status(self, connection: sqlite3.Connection) -> KnowledgeGraphSnapshot | None:
        watermark = self.wiki_watermark(connection)
        current = connection.execute(
            """
            SELECT * FROM knowledge_graph_snapshots
            WHERE workspace_id=? AND wiki_watermark=?
            ORDER BY CASE status WHEN 'ready' THEN 0 WHEN 'building' THEN 1 ELSE 2 END,
                     completed_at DESC, created_at DESC
            LIMIT 1
            """,
            (self.workspace_id, watermark),
        ).fetchone()
        if current is not None:
            return _snapshot(current, stale=False)
        latest = connection.execute(
            """
            SELECT * FROM knowledge_graph_snapshots
            WHERE workspace_id=?
            ORDER BY completed_at DESC, created_at DESC LIMIT 1
            """,
            (self.workspace_id,),
        ).fetchone()
        return _snapshot(latest, stale=True) if latest is not None else None

    def ensure_current(self, connection: sqlite3.Connection) -> KnowledgeGraphSnapshot:
        status = self.status(connection)
        if status is not None and status.status == "ready" and not status.stale:
            return status
        return self.rebuild(connection)

    def rebuild(
        self, connection: sqlite3.Connection, *, force: bool = False
    ) -> KnowledgeGraphSnapshot:
        watermark = self.wiki_watermark(connection)
        graph_revision = self._graph_revision(watermark)
        existing = self._snapshot_by_revision(connection, graph_revision)
        if existing is not None and existing.status == "ready" and not force:
            return existing

        created_at = _now()
        connection.execute(
            """
            INSERT INTO knowledge_graph_snapshots (
                graph_revision, workspace_id, wiki_watermark, projector_id,
                projector_version, config_hash, status, node_count, edge_count,
                warning_count, error, created_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'building', 0, 0, 0, NULL, ?, NULL)
            ON CONFLICT(graph_revision) DO UPDATE SET
                status='building', node_count=0, edge_count=0, warning_count=0,
                error=NULL, created_at=excluded.created_at, completed_at=NULL
            """,
            (
                graph_revision,
                self.workspace_id,
                watermark,
                self.projector_id,
                self.projector_version,
                self.config_hash,
                created_at,
            ),
        )
        connection.execute("SAVEPOINT knowledge_graph_rebuild")
        try:
            connection.execute(
                "DELETE FROM knowledge_graph_edge_evidence WHERE graph_revision=?",
                (graph_revision,),
            )
            connection.execute(
                "DELETE FROM knowledge_graph_edges WHERE graph_revision=?", (graph_revision,)
            )
            connection.execute(
                "DELETE FROM knowledge_graph_nodes WHERE graph_revision=?", (graph_revision,)
            )
            nodes, edges, warning_count = self._project(connection)
            self._insert_nodes(connection, graph_revision, nodes)
            self._insert_edges(connection, graph_revision, edges)
            completed_at = _now()
            connection.execute(
                """
                UPDATE knowledge_graph_snapshots
                SET status='ready', node_count=?, edge_count=?, warning_count=?,
                    error=NULL, completed_at=?
                WHERE graph_revision=?
                """,
                (len(nodes), len(edges), warning_count, completed_at, graph_revision),
            )
            connection.execute("RELEASE SAVEPOINT knowledge_graph_rebuild")
        except Exception as exc:
            connection.execute("ROLLBACK TO SAVEPOINT knowledge_graph_rebuild")
            connection.execute("RELEASE SAVEPOINT knowledge_graph_rebuild")
            connection.execute(
                """
                UPDATE knowledge_graph_snapshots
                SET status='error', error='knowledge graph rebuild failed', completed_at=?
                WHERE graph_revision=?
                """,
                (_now(), graph_revision),
            )
            raise KnowledgeGraphError("knowledge graph rebuild failed") from exc
        snapshot = self._snapshot_by_revision(connection, graph_revision)
        if snapshot is None:
            raise KnowledgeGraphError("knowledge graph snapshot disappeared")
        return snapshot

    def overview(
        self,
        connection: sqlite3.Connection,
        *,
        graph_revision: str | None = None,
        query: str = "",
        kinds: tuple[str, ...] = (),
        offset: int = 0,
        limit: int = 500,
        edge_limit: int = 1_000,
    ) -> KnowledgeGraphOverview:
        snapshot = self._ready_snapshot(connection, graph_revision)
        normalized_query = query.strip().casefold()
        where = ["graph_revision=?"]
        params: list[object] = [snapshot.graph_revision]
        if kinds:
            where.append("kind IN (" + ",".join("?" for _ in kinds) + ")")
            params.extend(kinds)
        if normalized_query:
            where.append("(LOWER(label) LIKE ? OR LOWER(node_id) LIKE ?)")
            term = f"%{normalized_query}%"
            params.extend((term, term))
        clause = " AND ".join(where)
        rows = connection.execute(
            f"SELECT * FROM knowledge_graph_nodes WHERE {clause} "
            "ORDER BY kind, label, node_id LIMIT ? OFFSET ?",
            (*params, limit + 1, offset),
        ).fetchall()
        has_more = len(rows) > limit
        selected = rows[:limit]
        nodes = tuple(_node(row) for row in selected)
        node_ids = tuple(node.node_id for node in nodes)
        edges = self._edges_for_nodes(
            connection, snapshot.graph_revision, node_ids, limit=edge_limit
        )
        next_offset = offset + limit if has_more else None
        return KnowledgeGraphOverview(
            snapshot=snapshot,
            nodes=nodes,
            edges=edges,
            offset=offset,
            next_offset=next_offset,
            has_more=has_more,
        )

    def snapshot(
        self, connection: sqlite3.Connection, graph_revision: str | None = None
    ) -> KnowledgeGraphSnapshot:
        return self._ready_snapshot(connection, graph_revision)

    def node(
        self,
        connection: sqlite3.Connection,
        node_id: str,
        *,
        graph_revision: str | None = None,
    ) -> tuple[KnowledgeGraphSnapshot, KnowledgeGraphNode]:
        snapshot = self._ready_snapshot(connection, graph_revision)
        row = connection.execute(
            "SELECT * FROM knowledge_graph_nodes WHERE graph_revision=? AND node_id=?",
            (snapshot.graph_revision, node_id),
        ).fetchone()
        if row is None:
            raise KeyError(node_id)
        return snapshot, _node(row)

    def neighborhood(
        self,
        connection: sqlite3.Connection,
        node_id: str,
        *,
        graph_revision: str | None = None,
        limit: int = 100,
    ) -> KnowledgeGraphNeighborhood:
        snapshot, center = self.node(connection, node_id, graph_revision=graph_revision)
        edge_rows = connection.execute(
            """
            SELECT * FROM knowledge_graph_edges
            WHERE graph_revision=? AND (source_node_id=? OR target_node_id=?)
            ORDER BY kind, edge_id LIMIT ?
            """,
            (snapshot.graph_revision, node_id, node_id, limit),
        ).fetchall()
        edges = tuple(self._edge(connection, row) for row in edge_rows)
        node_ids = {node_id}
        for edge in edges:
            node_ids.add(edge.source_node_id)
            node_ids.add(edge.target_node_id)
        placeholders = ",".join("?" for _ in node_ids)
        node_rows = connection.execute(
            f"SELECT * FROM knowledge_graph_nodes WHERE graph_revision=? "
            f"AND node_id IN ({placeholders}) ORDER BY kind, label, node_id",
            (snapshot.graph_revision, *sorted(node_ids)),
        ).fetchall()
        return KnowledgeGraphNeighborhood(
            snapshot=snapshot,
            center=center,
            nodes=tuple(_node(row) for row in node_rows),
            edges=edges,
        )

    def expand_page_relations(
        self,
        connection: sqlite3.Connection,
        seed_page_ids: tuple[str, ...],
        *,
        query: str,
        limit: int = 20,
    ) -> tuple[KnowledgeGraphRelationPath, ...]:
        """Expand explicit page links only; weak source-coincidence edges stay excluded."""

        if not seed_page_ids:
            return ()
        normalized_query = " ".join(query.split())
        if not normalized_query or len(normalized_query) > 2_000:
            raise ValueError("knowledge relation query must be between 1 and 2000 characters")
        if len(seed_page_ids) > 20 or len(set(seed_page_ids)) != len(seed_page_ids):
            raise ValueError("knowledge relation seeds must contain 1 to 20 unique pages")
        if limit < 1 or limit > 50:
            raise ValueError("knowledge relation expansion limit must be between 1 and 50")
        snapshot = self.ensure_current(connection)
        placeholders = ",".join("?" for _ in seed_page_ids)
        rows = connection.execute(
            f"""
            SELECT edge.*, source.page_id AS source_page_id,
                   source.page_revision AS source_page_revision,
                   source.label AS source_label,
                   source.properties_json AS source_properties_json,
                   target.page_id AS target_page_id,
                   target.page_revision AS target_page_revision,
                   target.label AS target_label,
                   target.properties_json AS target_properties_json
            FROM knowledge_graph_edges AS edge
            JOIN knowledge_graph_nodes AS source
              ON source.graph_revision=edge.graph_revision
             AND source.node_id=edge.source_node_id
            JOIN knowledge_graph_nodes AS target
              ON target.graph_revision=edge.graph_revision
             AND target.node_id=edge.target_node_id
            WHERE edge.graph_revision=? AND edge.kind='WIKILINK'
              AND (source.page_id IN ({placeholders}) OR target.page_id IN ({placeholders}))
              AND source.page_id IS NOT NULL AND target.page_id IS NOT NULL
            ORDER BY edge.weight DESC, edge.edge_id
            LIMIT ?
            """,
            (
                snapshot.graph_revision,
                *seed_page_ids,
                *seed_page_ids,
                min(200, max(20, limit * 10)),
            ),
        ).fetchall()
        seed_order = {page_id: rank for rank, page_id in enumerate(seed_page_ids)}

        def path_from_row(row: sqlite3.Row) -> KnowledgeGraphRelationPath:
            outbound = str(row["source_page_id"]) in seed_order
            source_properties = json.loads(str(row["source_properties_json"]))
            target_properties = json.loads(str(row["target_properties_json"]))
            edge = self._edge(connection, row)
            return KnowledgeGraphRelationPath(
                graph_revision=snapshot.graph_revision,
                seed_page_id=str(row["source_page_id"] if outbound else row["target_page_id"]),
                seed_source_relative_path=str(
                    source_properties["source_relative_path"]
                    if outbound
                    else target_properties["source_relative_path"]
                ),
                target_page_id=str(row["target_page_id"] if outbound else row["source_page_id"]),
                target_page_revision=str(
                    row["target_page_revision"] if outbound else row["source_page_revision"]
                ),
                target_source_relative_path=str(
                    target_properties["source_relative_path"]
                    if outbound
                    else source_properties["source_relative_path"]
                ),
                direction="outbound" if outbound else "inbound",
                score=_relation_score(
                    normalized_query,
                    edge,
                    str(row["target_label"] if outbound else row["source_label"]),
                ),
                edge=edge,
            )

        paths = sorted(
            (path_from_row(row) for row in rows),
            key=lambda item: (
                -item.score,
                seed_order[item.seed_page_id],
                item.direction != "outbound",
                -item.edge.weight,
                item.target_page_id,
            ),
        )
        selected: list[KnowledgeGraphRelationPath] = []
        seen_targets: set[str] = set()
        for path in paths:
            if path.target_page_id in seed_order or path.target_page_id in seen_targets:
                continue
            selected.append(path)
            seen_targets.add(path.target_page_id)
            if len(selected) >= limit:
                break
        return tuple(selected)

    def _ready_snapshot(
        self, connection: sqlite3.Connection, graph_revision: str | None
    ) -> KnowledgeGraphSnapshot:
        if graph_revision is None:
            return self.ensure_current(connection)
        snapshot = self._snapshot_by_revision(connection, graph_revision)
        if snapshot is None or snapshot.workspace_id != self.workspace_id:
            raise KeyError(graph_revision)
        if snapshot.status != "ready":
            raise KnowledgeGraphError("knowledge graph snapshot is not ready")
        return replace(
            snapshot,
            stale=snapshot.wiki_watermark != self.wiki_watermark(connection),
        )

    def _snapshot_by_revision(
        self, connection: sqlite3.Connection, graph_revision: str
    ) -> KnowledgeGraphSnapshot | None:
        row = connection.execute(
            "SELECT * FROM knowledge_graph_snapshots WHERE graph_revision=?",
            (graph_revision,),
        ).fetchone()
        return _snapshot(row, stale=False) if row is not None else None

    def _graph_revision(self, watermark: str) -> str:
        return (
            "kgraph_"
            + _stable_hash(
                "\0".join(
                    (
                        self.workspace_id,
                        watermark,
                        self.projector_id,
                        self.projector_version,
                        self.config_hash,
                    )
                )
            )[:32]
        )

    def _project(
        self, connection: sqlite3.Connection
    ) -> tuple[dict[str, KnowledgeGraphNode], dict[str, _ProjectedEdge], int]:
        pages = self._load_pages(connection)
        nodes: dict[str, KnowledgeGraphNode] = {}
        edges: dict[str, _ProjectedEdge] = {}
        aliases: dict[str, str] = {}
        warning_count = 0

        for page in pages:
            node_id = _page_node_id(page.page_id)
            kind = _page_kind(page.path)
            nodes[node_id] = KnowledgeGraphNode(
                node_id=node_id,
                kind=kind,
                label=page.title,
                page_id=page.page_id,
                page_revision=page.page_revision,
                source_id=page.source_id,
                source_revision=page.source_revision,
                properties={
                    "path": page.path,
                    "source_relative_path": page.source_relative_path,
                    "missing": False,
                },
            )
            for alias in _page_aliases(page):
                aliases.setdefault(alias, node_id)

            source_node_id = _source_node_id(page.source_id)
            nodes.setdefault(
                source_node_id,
                KnowledgeGraphNode(
                    node_id=source_node_id,
                    kind="source",
                    label=page.source_relative_path or page.title,
                    page_id=None,
                    page_revision=None,
                    source_id=page.source_id,
                    source_revision=page.source_revision,
                    properties={
                        "source_kind": page.source_kind,
                        "relative_path": page.source_relative_path,
                    },
                ),
            )
            evidence = _page_evidence(page)
            if evidence is None:
                warning_count += 1
            else:
                projected = _edge(
                    source_node_id=node_id,
                    target_node_id=source_node_id,
                    kind="EVIDENCED_BY",
                    directed=True,
                    weight=1.0,
                    confidence=1.0,
                    evidence=(evidence,),
                )
                edges[projected.edge.edge_id] = projected

        for page in pages:
            source_node_id = _page_node_id(page.page_id)
            for link in _page_links(page):
                target_node_id = aliases.get(_normalize_alias(link.target))
                if target_node_id is None:
                    target_node_id = _concept_node_id(link.target)
                    nodes.setdefault(
                        target_node_id,
                        KnowledgeGraphNode(
                            node_id=target_node_id,
                            kind="concept",
                            label=link.target,
                            page_id=None,
                            page_revision=None,
                            source_id=None,
                            source_revision=None,
                            properties={"missing": True, "wikilink": link.target},
                        ),
                    )
                evidence = _link_evidence(page, link)
                if evidence is None:
                    warning_count += 1
                    continue
                projected = _edge(
                    source_node_id=source_node_id,
                    target_node_id=target_node_id,
                    kind="WIKILINK",
                    directed=True,
                    weight=float(link.count),
                    confidence=1.0,
                    evidence=(evidence,),
                    properties={
                        "anchors": list(link.anchors),
                        "contexts": list(link.contexts),
                        "syntaxes": list(link.syntaxes),
                    },
                )
                edges[projected.edge.edge_id] = projected

        by_source: dict[str, list[_PageInput]] = {}
        for page in pages:
            by_source.setdefault(page.source_id, []).append(page)
        for source_pages in by_source.values():
            ordered = sorted(source_pages, key=lambda page: page.page_id)
            for index, left in enumerate(ordered):
                for right in ordered[index + 1 :]:
                    pair_evidence = tuple(
                        item
                        for item in (_page_evidence(left), _page_evidence(right))
                        if item is not None
                    )
                    if len(pair_evidence) != 2:
                        warning_count += 1
                        continue
                    projected = _edge(
                        source_node_id=_page_node_id(left.page_id),
                        target_node_id=_page_node_id(right.page_id),
                        kind="SHARES_SOURCE",
                        directed=False,
                        weight=1.0,
                        confidence=1.0,
                        evidence=pair_evidence,
                    )
                    edges[projected.edge.edge_id] = projected
        return nodes, edges, warning_count

    def _load_pages(self, connection: sqlite3.Connection) -> tuple[_PageInput, ...]:
        rows = connection.execute(
            """
            SELECT page.page_id, page.path, page.title,
                   revision.revision_id AS page_revision, revision.content,
                   revision.source_revision, proposal.source_id,
                   proposal.source_kind, proposal.source_relative_path
            FROM knowledge_pages AS page
            JOIN knowledge_page_revisions AS revision
              ON revision.revision_id=page.current_revision
            JOIN knowledge_proposals AS proposal
              ON proposal.proposal_id=revision.proposal_id
            ORDER BY page.page_id
            """,
        ).fetchall()
        chunk_rows = connection.execute(
            """
            SELECT chunk_id, page_id, page_revision, source_id, source_revision, text
            FROM knowledge_chunks
            WHERE workspace_id=? AND active=1
            ORDER BY page_id, ordinal, chunk_id
            """,
            (self.workspace_id,),
        ).fetchall()
        chunks_by_page: dict[str, list[_PageChunkInput]] = {}
        for row in chunk_rows:
            chunk_id = str(row["chunk_id"])
            chunks_by_page.setdefault(str(row["page_id"]), []).append(
                _PageChunkInput(
                    text=str(row["text"]),
                    evidence=KnowledgeGraphEvidence(
                        citation_id=_stable_id(
                            "kcite",
                            self.workspace_id,
                            str(row["page_id"]),
                            str(row["page_revision"]),
                            str(row["source_revision"]),
                            chunk_id,
                        ),
                        chunk_id=chunk_id,
                        page_id=str(row["page_id"]),
                        page_revision=str(row["page_revision"]),
                        source_id=str(row["source_id"]),
                        source_revision=str(row["source_revision"]),
                    ),
                )
            )
        pages: list[_PageInput] = []
        for row in rows:
            pages.append(
                _PageInput(
                    page_id=str(row["page_id"]),
                    path=str(row["path"]),
                    title=str(row["title"]),
                    page_revision=str(row["page_revision"]),
                    content=str(row["content"]),
                    source_id=str(row["source_id"]),
                    source_revision=str(row["source_revision"]),
                    source_kind=str(row["source_kind"]),
                    source_relative_path=str(row["source_relative_path"]),
                    chunks=tuple(chunks_by_page.get(str(row["page_id"]), ())),
                )
            )
        return tuple(pages)

    def _insert_nodes(
        self,
        connection: sqlite3.Connection,
        graph_revision: str,
        nodes: dict[str, KnowledgeGraphNode],
    ) -> None:
        connection.executemany(
            """
            INSERT INTO knowledge_graph_nodes (
                graph_revision, workspace_id, node_id, kind, label, page_id,
                page_revision, source_id, source_revision, properties_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    graph_revision,
                    self.workspace_id,
                    node.node_id,
                    node.kind,
                    node.label,
                    node.page_id,
                    node.page_revision,
                    node.source_id,
                    node.source_revision,
                    _canonical_json(node.properties),
                )
                for node in sorted(nodes.values(), key=lambda item: item.node_id)
            ),
        )

    def _insert_edges(
        self,
        connection: sqlite3.Connection,
        graph_revision: str,
        edges: dict[str, _ProjectedEdge],
    ) -> None:
        for projected in sorted(edges.values(), key=lambda item: item.edge.edge_id):
            edge = projected.edge
            connection.execute(
                """
                INSERT INTO knowledge_graph_edges (
                    graph_revision, workspace_id, edge_id, source_node_id,
                    target_node_id, kind, directed, weight, confidence,
                    extractor_id, extractor_version, properties_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    graph_revision,
                    self.workspace_id,
                    edge.edge_id,
                    edge.source_node_id,
                    edge.target_node_id,
                    edge.kind,
                    int(edge.directed),
                    edge.weight,
                    edge.confidence,
                    edge.extractor_id,
                    edge.extractor_version,
                    _canonical_json(edge.properties),
                ),
            )
            connection.executemany(
                """
                INSERT INTO knowledge_graph_edge_evidence (
                    graph_revision, edge_id, ordinal, citation_id, chunk_id,
                    page_id, page_revision, source_id, source_revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        graph_revision,
                        edge.edge_id,
                        ordinal,
                        evidence.citation_id,
                        evidence.chunk_id,
                        evidence.page_id,
                        evidence.page_revision,
                        evidence.source_id,
                        evidence.source_revision,
                    )
                    for ordinal, evidence in enumerate(edge.evidence)
                ),
            )

    def _edges_for_nodes(
        self,
        connection: sqlite3.Connection,
        graph_revision: str,
        node_ids: tuple[str, ...],
        *,
        limit: int,
    ) -> tuple[KnowledgeGraphEdge, ...]:
        if not node_ids:
            return ()
        placeholders = ",".join("?" for _ in node_ids)
        rows = connection.execute(
            f"SELECT * FROM knowledge_graph_edges WHERE graph_revision=? "
            f"AND source_node_id IN ({placeholders}) "
            f"AND target_node_id IN ({placeholders}) "
            "ORDER BY kind, edge_id LIMIT ?",
            (graph_revision, *node_ids, *node_ids, limit),
        ).fetchall()
        return tuple(self._edge(connection, row) for row in rows)

    def _edge(self, connection: sqlite3.Connection, row: sqlite3.Row) -> KnowledgeGraphEdge:
        evidence_rows = connection.execute(
            """
            SELECT * FROM knowledge_graph_edge_evidence
            WHERE graph_revision=? AND edge_id=? ORDER BY ordinal
            """,
            (row["graph_revision"], row["edge_id"]),
        ).fetchall()
        return _edge_from_row(row, evidence_rows)


def _page_kind(path: str) -> str:
    if path.startswith("wiki/projects/"):
        return "project"
    if path.startswith("wiki/concepts/"):
        return "concept"
    if path.startswith("wiki/decisions/"):
        return "decision"
    if path.startswith("wiki/tools/"):
        return "tool"
    return "page"


def _page_aliases(page: _PageInput) -> set[str]:
    path = page.path.removesuffix(".md")
    stem = path.rsplit("/", 1)[-1]
    source_path = page.source_relative_path.replace("\\", "/")
    source_without_extension = source_path.rsplit(".", 1)[0] if "." in source_path else source_path
    source_stem = source_path.rsplit("/", 1)[-1]
    if "." in source_stem:
        source_stem = source_stem.rsplit(".", 1)[0]
    return {
        _normalize_alias(page.title),
        _normalize_alias(path),
        _normalize_alias(stem),
        _normalize_alias(source_path),
        _normalize_alias(source_without_extension),
        _normalize_alias(source_stem),
    }


def _page_links(page: _PageInput) -> tuple[_PageLink, ...]:
    links: dict[str, dict[str, Any]] = {}
    in_fence = False
    fence_marker = ""
    heading = ""
    for line in page.content.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = ""
            continue
        if in_fence:
            continue
        visible = _remove_inline_code(line)
        heading_match = _MARKDOWN_HEADING.match(visible.strip())
        if heading_match:
            heading = heading_match.group(1).strip()
        for match in _WIKILINK.finditer(visible):
            raw_parts = match.group(1).split("|", 1)
            target = raw_parts[0].split("#", 1)[0].strip()
            if not target:
                continue
            anchor = raw_parts[1].strip() if len(raw_parts) == 2 else target
            _record_link(links, target, anchor, heading, visible, "wikilink")
        for match in _MARKDOWN_LINK.finditer(visible):
            target = _markdown_target(page.source_relative_path, match.group(2))
            if target is None:
                continue
            _record_link(
                links,
                target,
                match.group(1).strip(),
                heading,
                visible,
                "markdown_internal",
            )
    return tuple(
        _PageLink(
            target=target,
            count=int(value["count"]),
            anchors=tuple(sorted(value["anchors"])),
            contexts=tuple(sorted(value["contexts"])),
            lines=tuple(sorted(value["lines"])),
            syntaxes=tuple(sorted(value["syntaxes"])),
        )
        for target, value in sorted(links.items())
    )


def _record_link(
    links: dict[str, dict[str, Any]],
    target: str,
    anchor: str,
    heading: str,
    line: str,
    syntax: str,
) -> None:
    value = links.setdefault(
        target,
        {
            "count": 0,
            "anchors": set(),
            "contexts": set(),
            "lines": set(),
            "syntaxes": set(),
        },
    )
    value["count"] += 1
    value["anchors"].add(anchor[:512])
    value["contexts"].add("\n".join(item for item in (heading, line.strip()) if item)[:2_000])
    value["lines"].add(line.strip()[:2_000])
    value["syntaxes"].add(syntax)


def _markdown_target(source_relative_path: str, raw_destination: str) -> str | None:
    destination = raw_destination.strip()
    if destination.startswith("<") and ">" in destination:
        destination = destination[1 : destination.index(">")]
    else:
        destination = destination.split(maxsplit=1)[0]
    parsed = urlsplit(destination)
    if parsed.scheme or parsed.netloc or not parsed.path:
        return None
    decoded = unquote(parsed.path).replace("\\", "/")
    if decoded.startswith("/"):
        normalized = posixpath.normpath(decoded.lstrip("/"))
    else:
        parent = posixpath.dirname(source_relative_path.replace("\\", "/"))
        normalized = posixpath.normpath(posixpath.join(parent, decoded))
    if normalized.startswith("../") or normalized in {"", ".", ".."}:
        return None
    return normalized.removesuffix(".md").removesuffix(".markdown")


def _remove_inline_code(line: str) -> str:
    result: list[str] = []
    in_code = False
    index = 0
    while index < len(line):
        if line[index] == "`":
            run = 1
            while index + run < len(line) and line[index + run] == "`":
                run += 1
            in_code = not in_code
            result.extend(" " * run)
            index += run
            continue
        result.append(" " if in_code else line[index])
        index += 1
    return "".join(result)


def _page_evidence(page: _PageInput) -> KnowledgeGraphEvidence | None:
    return page.chunks[0].evidence if page.chunks else None


def _link_evidence(page: _PageInput, link: _PageLink) -> KnowledgeGraphEvidence | None:
    for chunk in page.chunks:
        if any(line and line in chunk.text for line in link.lines):
            return chunk.evidence
    return None


def _edge(
    *,
    source_node_id: str,
    target_node_id: str,
    kind: str,
    directed: bool,
    weight: float,
    confidence: float,
    evidence: tuple[KnowledgeGraphEvidence, ...],
    properties: dict[str, Any] | None = None,
) -> _ProjectedEdge:
    left, right = source_node_id, target_node_id
    if not directed and right < left:
        left, right = right, left
    edge_id = _stable_id("kedge", kind, left, right)
    return _ProjectedEdge(
        edge=KnowledgeGraphEdge(
            edge_id=edge_id,
            source_node_id=left,
            target_node_id=right,
            kind=kind,
            directed=directed,
            weight=weight,
            confidence=confidence,
            extractor_id=_PROJECTOR_ID,
            extractor_version=_PROJECTOR_VERSION,
            properties=properties or {},
            evidence=evidence,
        )
    )


def _node(row: sqlite3.Row) -> KnowledgeGraphNode:
    return KnowledgeGraphNode(
        node_id=str(row["node_id"]),
        kind=str(row["kind"]),
        label=str(row["label"]),
        page_id=str(row["page_id"]) if row["page_id"] else None,
        page_revision=str(row["page_revision"]) if row["page_revision"] else None,
        source_id=str(row["source_id"]) if row["source_id"] else None,
        source_revision=str(row["source_revision"]) if row["source_revision"] else None,
        properties=json.loads(str(row["properties_json"])),
    )


def _edge_from_row(row: sqlite3.Row, evidence_rows: list[sqlite3.Row]) -> KnowledgeGraphEdge:
    evidence = tuple(
        KnowledgeGraphEvidence(
            citation_id=str(item["citation_id"]),
            chunk_id=str(item["chunk_id"]),
            page_id=str(item["page_id"]),
            page_revision=str(item["page_revision"]),
            source_id=str(item["source_id"]),
            source_revision=str(item["source_revision"]),
        )
        for item in evidence_rows
    )
    return KnowledgeGraphEdge(
        edge_id=str(row["edge_id"]),
        source_node_id=str(row["source_node_id"]),
        target_node_id=str(row["target_node_id"]),
        kind=str(row["kind"]),
        directed=bool(row["directed"]),
        weight=float(row["weight"]),
        confidence=float(row["confidence"]),
        extractor_id=str(row["extractor_id"]),
        extractor_version=str(row["extractor_version"]),
        properties=json.loads(str(row["properties_json"])),
        evidence=evidence,
    )


def _snapshot(row: sqlite3.Row, *, stale: bool) -> KnowledgeGraphSnapshot:
    return KnowledgeGraphSnapshot(
        graph_revision=str(row["graph_revision"]),
        workspace_id=str(row["workspace_id"]),
        wiki_watermark=str(row["wiki_watermark"]),
        projector_id=str(row["projector_id"]),
        projector_version=str(row["projector_version"]),
        config_hash=str(row["config_hash"]),
        status=str(row["status"]),
        node_count=int(row["node_count"]),
        edge_count=int(row["edge_count"]),
        warning_count=int(row["warning_count"]),
        error=str(row["error"]) if row["error"] else None,
        created_at=str(row["created_at"]),
        completed_at=str(row["completed_at"]) if row["completed_at"] else None,
        stale=stale,
    )


def _normalize_alias(value: str) -> str:
    return " ".join(value.strip().replace("\\", "/").casefold().split())


def _relation_score(query: str, edge: KnowledgeGraphEdge, target_label: str) -> float:
    from core.knowledge.retrieval import lexical_terms

    query_terms = set(lexical_terms(query))
    relation_text = "\n".join(
        (
            target_label,
            *(str(value) for value in edge.properties.get("anchors", ())),
            *(str(value) for value in edge.properties.get("contexts", ())),
        )
    )
    relation_terms = set(lexical_terms(relation_text))
    return len(query_terms.intersection(relation_terms)) / max(1, len(query_terms))


def _page_node_id(page_id: str) -> str:
    return _stable_id("knode", "page", page_id)


def _source_node_id(source_id: str) -> str:
    return _stable_id("knode", "source", source_id)


def _concept_node_id(label: str) -> str:
    return _stable_id("knode", "concept", _normalize_alias(label))


def _stable_id(prefix: str, *parts: str) -> str:
    return f"{prefix}_{_stable_hash(chr(0).join(parts))[:32]}"


def _stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _now() -> str:
    return datetime.now(UTC).isoformat()
