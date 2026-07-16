"""Versioned local graph projection derived from canonical Knowledge revisions."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

_PROJECTOR_ID = "sage.local-knowledge-graph"
_PROJECTOR_VERSION = "1.0.0"
_GRAPH_CONFIG = {
    "edge_kinds": ["EVIDENCED_BY", "SHARES_SOURCE", "WIKILINK"],
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
    chunk_id: str | None
    citation_id: str | None


@dataclass(frozen=True, slots=True)
class _ProjectedEdge:
    edge: KnowledgeGraphEdge


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
                properties={"path": page.path, "missing": False},
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
            evidence = _page_evidence(page)
            for target, count in _wikilinks(page.content).items():
                target_node_id = aliases.get(_normalize_alias(target))
                if target_node_id is None:
                    target_node_id = _concept_node_id(target)
                    nodes.setdefault(
                        target_node_id,
                        KnowledgeGraphNode(
                            node_id=target_node_id,
                            kind="concept",
                            label=target,
                            page_id=None,
                            page_revision=None,
                            source_id=None,
                            source_revision=None,
                            properties={"missing": True, "wikilink": target},
                        ),
                    )
                if evidence is None:
                    warning_count += 1
                    continue
                projected = _edge(
                    source_node_id=source_node_id,
                    target_node_id=target_node_id,
                    kind="WIKILINK",
                    directed=True,
                    weight=float(count),
                    confidence=1.0,
                    evidence=(evidence,),
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
                   proposal.source_kind, proposal.source_relative_path,
                   chunk.chunk_id
            FROM knowledge_pages AS page
            JOIN knowledge_page_revisions AS revision
              ON revision.revision_id=page.current_revision
            JOIN knowledge_proposals AS proposal
              ON proposal.proposal_id=revision.proposal_id
            LEFT JOIN knowledge_chunks AS chunk
              ON chunk.chunk_id=(
                  SELECT current_chunk.chunk_id FROM knowledge_chunks AS current_chunk
                  WHERE current_chunk.workspace_id=?
                    AND current_chunk.page_id=page.page_id
                    AND current_chunk.page_revision=page.current_revision
                    AND current_chunk.active=1
                  ORDER BY current_chunk.ordinal, current_chunk.chunk_id LIMIT 1
              )
            ORDER BY page.page_id
            """,
            (self.workspace_id,),
        ).fetchall()
        pages: list[_PageInput] = []
        for row in rows:
            chunk_id = str(row["chunk_id"]) if row["chunk_id"] else None
            citation = (
                _stable_id(
                    "kcite",
                    self.workspace_id,
                    str(row["page_id"]),
                    str(row["page_revision"]),
                    str(row["source_revision"]),
                    chunk_id,
                )
                if chunk_id
                else None
            )
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
                    chunk_id=chunk_id,
                    citation_id=citation,
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
    source_stem = page.source_relative_path.rsplit("/", 1)[-1]
    if "." in source_stem:
        source_stem = source_stem.rsplit(".", 1)[0]
    return {
        _normalize_alias(page.title),
        _normalize_alias(path),
        _normalize_alias(stem),
        _normalize_alias(source_stem),
    }


def _wikilinks(content: str) -> dict[str, int]:
    links: dict[str, int] = {}
    in_fence = False
    fence_marker = ""
    for line in content.splitlines():
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
        for match in _WIKILINK.finditer(visible):
            raw = match.group(1).split("|", 1)[0].split("#", 1)[0].strip()
            if not raw:
                continue
            links[raw] = links.get(raw, 0) + 1
    return links


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
    if page.chunk_id is None or page.citation_id is None:
        return None
    return KnowledgeGraphEvidence(
        citation_id=page.citation_id,
        chunk_id=page.chunk_id,
        page_id=page.page_id,
        page_revision=page.page_revision,
        source_id=page.source_id,
        source_revision=page.source_revision,
    )


def _edge(
    *,
    source_node_id: str,
    target_node_id: str,
    kind: str,
    directed: bool,
    weight: float,
    confidence: float,
    evidence: tuple[KnowledgeGraphEvidence, ...],
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
            properties={},
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
