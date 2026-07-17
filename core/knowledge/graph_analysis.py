"""Deterministic local community and learning-goal analysis for graph snapshots."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import networkx as nx  # type: ignore[import-untyped]

from core.knowledge.goals import LearningGoal
from core.knowledge.graph import KnowledgeGraphSnapshot

_ALGORITHM_ID = "networkx.louvain"
_ALGORITHM_VERSION = "3.5"
_DEFAULT_SEED = 42
_DEFAULT_RESOLUTION = 1.0
_DEFAULT_THRESHOLD = 0.0000001
_ANALYSIS_SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_graph_analyses (
    analysis_revision TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    graph_revision TEXT NOT NULL,
    goal_revision TEXT NOT NULL,
    algorithm_id TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    seed INTEGER NOT NULL,
    resolution REAL NOT NULL,
    threshold REAL NOT NULL,
    status TEXT NOT NULL,
    community_count INTEGER NOT NULL,
    insight_count INTEGER NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS knowledge_graph_analyses_lookup_idx
    ON knowledge_graph_analyses(graph_revision, goal_revision, status, completed_at);
CREATE TABLE IF NOT EXISTS knowledge_graph_communities (
    analysis_revision TEXT NOT NULL,
    community_id TEXT NOT NULL,
    label TEXT NOT NULL,
    node_count INTEGER NOT NULL,
    edge_count INTEGER NOT NULL,
    cohesion REAL NOT NULL,
    properties_json TEXT NOT NULL,
    PRIMARY KEY(analysis_revision, community_id)
);
CREATE TABLE IF NOT EXISTS knowledge_graph_node_metrics (
    analysis_revision TEXT NOT NULL,
    node_id TEXT NOT NULL,
    community_id TEXT NOT NULL,
    degree INTEGER NOT NULL,
    weighted_degree REAL NOT NULL,
    bridge_score REAL NOT NULL,
    PRIMARY KEY(analysis_revision, node_id)
);
CREATE INDEX IF NOT EXISTS knowledge_graph_node_metrics_community_idx
    ON knowledge_graph_node_metrics(analysis_revision, community_id, degree);
CREATE TABLE IF NOT EXISTS knowledge_graph_goal_alignments (
    analysis_revision TEXT NOT NULL,
    capability_id TEXT NOT NULL,
    label TEXT NOT NULL,
    coverage REAL NOT NULL,
    status TEXT NOT NULL,
    matched_keywords_json TEXT NOT NULL,
    missing_keywords_json TEXT NOT NULL,
    matched_node_ids_json TEXT NOT NULL,
    PRIMARY KEY(analysis_revision, capability_id)
);
CREATE TABLE IF NOT EXISTS knowledge_graph_insights (
    analysis_revision TEXT NOT NULL,
    insight_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    node_id TEXT,
    community_id TEXT,
    capability_id TEXT,
    properties_json TEXT NOT NULL,
    PRIMARY KEY(analysis_revision, insight_id)
);
CREATE INDEX IF NOT EXISTS knowledge_graph_insights_filter_idx
    ON knowledge_graph_insights(analysis_revision, kind, severity, insight_id);
"""


class KnowledgeGraphAnalysisError(RuntimeError):
    """Graph analysis failed without changing canonical knowledge."""


@dataclass(frozen=True, slots=True)
class KnowledgeGraphAnalysisSnapshot:
    analysis_revision: str
    workspace_id: str
    graph_revision: str
    goal_revision: str
    algorithm_id: str
    algorithm_version: str
    seed: int
    resolution: float
    threshold: float
    status: str
    community_count: int
    insight_count: int
    error: str | None
    created_at: str
    completed_at: str | None


@dataclass(frozen=True, slots=True)
class KnowledgeGraphCommunity:
    community_id: str
    label: str
    node_count: int
    edge_count: int
    cohesion: float
    properties: dict[str, Any]


@dataclass(frozen=True, slots=True)
class KnowledgeGraphNodeMetric:
    node_id: str
    community_id: str
    degree: int
    weighted_degree: float
    bridge_score: float


@dataclass(frozen=True, slots=True)
class KnowledgeGoalAlignment:
    capability_id: str
    label: str
    coverage: float
    status: str
    matched_keywords: tuple[str, ...]
    missing_keywords: tuple[str, ...]
    matched_node_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class KnowledgeGraphInsight:
    insight_id: str
    kind: str
    severity: str
    title: str
    description: str
    node_id: str | None
    community_id: str | None
    capability_id: str | None
    properties: dict[str, Any]


@dataclass(frozen=True, slots=True)
class KnowledgeGraphAnalysis:
    snapshot: KnowledgeGraphAnalysisSnapshot
    communities: tuple[KnowledgeGraphCommunity, ...]
    node_metrics: tuple[KnowledgeGraphNodeMetric, ...]
    alignments: tuple[KnowledgeGoalAlignment, ...]
    insights: tuple[KnowledgeGraphInsight, ...]


@dataclass(frozen=True, slots=True)
class _AnalysisInputNode:
    node_id: str
    kind: str
    label: str
    text: str
    missing: bool


class LocalKnowledgeGraphAnalyzer:
    """Persist reproducible Louvain communities and evidence-gap observations."""

    def __init__(
        self,
        *,
        workspace_id: str = "knowledge-local",
        seed: int = _DEFAULT_SEED,
        resolution: float = _DEFAULT_RESOLUTION,
        threshold: float = _DEFAULT_THRESHOLD,
    ) -> None:
        self.workspace_id = workspace_id
        self.seed = seed
        self.resolution = resolution
        self.threshold = threshold

    def ensure_schema(self, connection: sqlite3.Connection) -> None:
        connection.executescript(_ANALYSIS_SCHEMA)

    def analyze(
        self,
        connection: sqlite3.Connection,
        graph_snapshot: KnowledgeGraphSnapshot,
        goal: LearningGoal,
        *,
        force: bool = False,
    ) -> KnowledgeGraphAnalysis:
        if graph_snapshot.status != "ready":
            raise KnowledgeGraphAnalysisError("knowledge graph snapshot is not ready")
        revision = self._analysis_revision(graph_snapshot.graph_revision, goal.goal_revision)
        existing = self._snapshot(connection, revision)
        if existing is not None and existing.status == "ready" and not force:
            return self.get(connection, revision)
        created_at = _now()
        connection.execute(
            """
            INSERT INTO knowledge_graph_analyses (
                analysis_revision, workspace_id, graph_revision, goal_revision,
                algorithm_id, algorithm_version, seed, resolution, threshold,
                status, community_count, insight_count, error, created_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'building', 0, 0, NULL, ?, NULL)
            ON CONFLICT(analysis_revision) DO UPDATE SET
                status='building', community_count=0, insight_count=0,
                error=NULL, created_at=excluded.created_at, completed_at=NULL
            """,
            (
                revision,
                self.workspace_id,
                graph_snapshot.graph_revision,
                goal.goal_revision,
                _ALGORITHM_ID,
                _ALGORITHM_VERSION,
                self.seed,
                self.resolution,
                self.threshold,
                created_at,
            ),
        )
        connection.execute("SAVEPOINT knowledge_graph_analysis")
        try:
            self._clear(connection, revision)
            nodes, graph = self._load_graph(connection, graph_snapshot.graph_revision)
            communities, metrics = self._communities(graph, nodes, revision)
            alignments = self._align_goal(goal, nodes)
            insights = self._insights(nodes, graph, communities, metrics, alignments)
            self._insert(connection, revision, communities, metrics, alignments, insights)
            completed_at = _now()
            connection.execute(
                """
                UPDATE knowledge_graph_analyses
                SET status='ready', community_count=?, insight_count=?,
                    error=NULL, completed_at=?
                WHERE analysis_revision=?
                """,
                (len(communities), len(insights), completed_at, revision),
            )
            connection.execute("RELEASE SAVEPOINT knowledge_graph_analysis")
        except Exception as exc:
            connection.execute("ROLLBACK TO SAVEPOINT knowledge_graph_analysis")
            connection.execute("RELEASE SAVEPOINT knowledge_graph_analysis")
            connection.execute(
                """
                UPDATE knowledge_graph_analyses
                SET status='error', error='knowledge graph analysis failed', completed_at=?
                WHERE analysis_revision=?
                """,
                (_now(), revision),
            )
            raise KnowledgeGraphAnalysisError("knowledge graph analysis failed") from exc
        return self.get(connection, revision)

    def get(self, connection: sqlite3.Connection, analysis_revision: str) -> KnowledgeGraphAnalysis:
        snapshot = self._snapshot(connection, analysis_revision)
        if snapshot is None:
            raise KeyError(analysis_revision)
        if snapshot.status != "ready":
            raise KnowledgeGraphAnalysisError("knowledge graph analysis is not ready")
        community_rows = connection.execute(
            """
            SELECT * FROM knowledge_graph_communities
            WHERE analysis_revision=? ORDER BY label, community_id
            """,
            (analysis_revision,),
        ).fetchall()
        metric_rows = connection.execute(
            """
            SELECT * FROM knowledge_graph_node_metrics
            WHERE analysis_revision=? ORDER BY community_id, degree DESC, node_id
            """,
            (analysis_revision,),
        ).fetchall()
        alignment_rows = connection.execute(
            """
            SELECT * FROM knowledge_graph_goal_alignments
            WHERE analysis_revision=? ORDER BY capability_id
            """,
            (analysis_revision,),
        ).fetchall()
        insight_rows = connection.execute(
            """
            SELECT * FROM knowledge_graph_insights
            WHERE analysis_revision=?
            ORDER BY CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                     kind, insight_id
            """,
            (analysis_revision,),
        ).fetchall()
        return KnowledgeGraphAnalysis(
            snapshot=snapshot,
            communities=tuple(_community(row) for row in community_rows),
            node_metrics=tuple(_metric(row) for row in metric_rows),
            alignments=tuple(_alignment(row) for row in alignment_rows),
            insights=tuple(_insight(row) for row in insight_rows),
        )

    def _analysis_revision(self, graph_revision: str, goal_revision: str) -> str:
        payload = "\0".join(
            (
                self.workspace_id,
                graph_revision,
                goal_revision,
                _ALGORITHM_ID,
                _ALGORITHM_VERSION,
                str(self.seed),
                f"{self.resolution:.8f}",
                f"{self.threshold:.12f}",
            )
        )
        return "kanalysis_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def _snapshot(
        self, connection: sqlite3.Connection, revision: str
    ) -> KnowledgeGraphAnalysisSnapshot | None:
        row = connection.execute(
            "SELECT * FROM knowledge_graph_analyses WHERE analysis_revision=?",
            (revision,),
        ).fetchone()
        return _analysis_snapshot(row) if row is not None else None

    def _clear(self, connection: sqlite3.Connection, revision: str) -> None:
        for table in (
            "knowledge_graph_communities",
            "knowledge_graph_node_metrics",
            "knowledge_graph_goal_alignments",
            "knowledge_graph_insights",
        ):
            connection.execute(f"DELETE FROM {table} WHERE analysis_revision=?", (revision,))

    def _load_graph(
        self, connection: sqlite3.Connection, graph_revision: str
    ) -> tuple[dict[str, _AnalysisInputNode], nx.Graph]:
        node_rows = connection.execute(
            """
            SELECT node.*, revision.content
            FROM knowledge_graph_nodes AS node
            LEFT JOIN knowledge_page_revisions AS revision
              ON revision.revision_id=node.page_revision
            WHERE node.graph_revision=? AND node.kind!='source'
            ORDER BY node.node_id
            """,
            (graph_revision,),
        ).fetchall()
        nodes: dict[str, _AnalysisInputNode] = {}
        graph = nx.Graph()
        for row in node_rows:
            properties = json.loads(str(row["properties_json"]))
            node = _AnalysisInputNode(
                node_id=str(row["node_id"]),
                kind=str(row["kind"]),
                label=str(row["label"]),
                text="\n".join(
                    (
                        str(row["label"]),
                        str(row["content"]) if row["content"] else "",
                        json.dumps(properties, ensure_ascii=False, sort_keys=True),
                    )
                ),
                missing=bool(properties.get("missing", False)),
            )
            nodes[node.node_id] = node
            graph.add_node(node.node_id)
        edge_rows = connection.execute(
            """
            SELECT source_node_id, target_node_id, weight
            FROM knowledge_graph_edges
            WHERE graph_revision=? AND kind IN ('WIKILINK', 'SHARES_SOURCE')
            ORDER BY edge_id
            """,
            (graph_revision,),
        ).fetchall()
        for row in edge_rows:
            source = str(row["source_node_id"])
            target = str(row["target_node_id"])
            if source not in nodes or target not in nodes or source == target:
                continue
            weight = float(row["weight"])
            if graph.has_edge(source, target):
                graph[source][target]["weight"] += weight
            else:
                graph.add_edge(source, target, weight=weight)
        return nodes, graph

    def _communities(
        self,
        graph: nx.Graph,
        nodes: dict[str, _AnalysisInputNode],
        analysis_revision: str,
    ) -> tuple[tuple[KnowledgeGraphCommunity, ...], tuple[KnowledgeGraphNodeMetric, ...]]:
        raw_communities: list[set[str]] = []
        for component_nodes in sorted(
            nx.connected_components(graph), key=lambda item: min(item) if item else ""
        ):
            component = graph.subgraph(component_nodes).copy()
            if component.number_of_nodes() == 1 or component.number_of_edges() == 0:
                raw_communities.extend({node_id} for node_id in sorted(component_nodes))
                continue
            detected = nx.community.louvain_communities(
                component,
                weight="weight",
                resolution=self.resolution,
                threshold=self.threshold,
                seed=self.seed,
            )
            raw_communities.extend(set(item) for item in detected)
        raw_communities.sort(key=lambda item: min(item) if item else "")
        assignments: dict[str, str] = {}
        communities: list[KnowledgeGraphCommunity] = []
        for members in raw_communities:
            ordered = tuple(sorted(members))
            community_id = (
                "kcommunity_"
                + hashlib.sha256(
                    (analysis_revision + "\0" + "\0".join(ordered)).encode("utf-8")
                ).hexdigest()[:24]
            )
            for node_id in ordered:
                assignments[node_id] = community_id
            subgraph = graph.subgraph(ordered)
            edge_count = subgraph.number_of_edges()
            possible = len(ordered) * (len(ordered) - 1) / 2
            cohesion = edge_count / possible if possible else 0.0
            ranked = sorted(
                ordered,
                key=lambda node_id: (-int(graph.degree(node_id)), nodes[node_id].label, node_id),
            )
            labels = [nodes[node_id].label for node_id in ranked[:3]]
            communities.append(
                KnowledgeGraphCommunity(
                    community_id=community_id,
                    label=" / ".join(labels),
                    node_count=len(ordered),
                    edge_count=edge_count,
                    cohesion=round(cohesion, 6),
                    properties={"representative_node_ids": list(ranked[:3])},
                )
            )
        metrics: list[KnowledgeGraphNodeMetric] = []
        for node_id in sorted(nodes):
            neighbors = tuple(graph.neighbors(node_id))
            external = sum(
                1 for neighbor in neighbors if assignments.get(neighbor) != assignments.get(node_id)
            )
            bridge_score = external / len(neighbors) if neighbors else 0.0
            metrics.append(
                KnowledgeGraphNodeMetric(
                    node_id=node_id,
                    community_id=assignments[node_id],
                    degree=int(graph.degree(node_id)),
                    weighted_degree=float(graph.degree(node_id, weight="weight")),
                    bridge_score=round(bridge_score, 6),
                )
            )
        return tuple(communities), tuple(metrics)

    def _align_goal(
        self, goal: LearningGoal, nodes: dict[str, _AnalysisInputNode]
    ) -> tuple[KnowledgeGoalAlignment, ...]:
        alignments: list[KnowledgeGoalAlignment] = []
        for capability in goal.capabilities:
            matched_keywords: list[str] = []
            matched_nodes: set[str] = set()
            for keyword in capability.keywords:
                keyword_matched = False
                for node in nodes.values():
                    if _keyword_present(keyword, node.text):
                        matched_nodes.add(node.node_id)
                        keyword_matched = True
                if keyword_matched:
                    matched_keywords.append(keyword)
            missing = tuple(
                keyword for keyword in capability.keywords if keyword not in matched_keywords
            )
            coverage = len(matched_keywords) / len(capability.keywords)
            if coverage >= 0.75:
                status = "covered"
            elif coverage >= 0.25:
                status = "learning"
            else:
                status = "gap"
            alignments.append(
                KnowledgeGoalAlignment(
                    capability_id=capability.capability_id,
                    label=capability.label,
                    coverage=round(coverage, 6),
                    status=status,
                    matched_keywords=tuple(matched_keywords),
                    missing_keywords=missing,
                    matched_node_ids=tuple(sorted(matched_nodes)),
                )
            )
        return tuple(alignments)

    def _insights(
        self,
        nodes: dict[str, _AnalysisInputNode],
        graph: nx.Graph,
        communities: tuple[KnowledgeGraphCommunity, ...],
        metrics: tuple[KnowledgeGraphNodeMetric, ...],
        alignments: tuple[KnowledgeGoalAlignment, ...],
    ) -> tuple[KnowledgeGraphInsight, ...]:
        insights: list[KnowledgeGraphInsight] = []
        for node in nodes.values():
            if node.missing:
                insights.append(
                    _insight_item(
                        kind="missing_concept",
                        severity="high",
                        title=f"缺少概念：{node.label}",
                        description="已有 Wiki 链接指向该概念，但尚无已批准页面。",
                        node_id=node.node_id,
                        properties={"research_query": node.label},
                    )
                )
            if graph.degree(node.node_id) == 0:
                insights.append(
                    _insight_item(
                        kind="isolated_node",
                        severity="low",
                        title=f"孤立知识：{node.label}",
                        description="该节点当前没有本地确定性关系。",
                        node_id=node.node_id,
                    )
                )
        for metric in metrics:
            if metric.bridge_score <= 0:
                continue
            node = nodes[metric.node_id]
            insights.append(
                _insight_item(
                    kind="bridge_node",
                    severity="low",
                    title=f"跨社区桥接：{node.label}",
                    description="该节点连接了不同知识社区。",
                    node_id=node.node_id,
                    community_id=metric.community_id,
                    properties={"bridge_score": metric.bridge_score},
                )
            )
        for community in communities:
            if community.node_count < 3 or community.cohesion >= 0.15:
                continue
            insights.append(
                _insight_item(
                    kind="sparse_community",
                    severity="medium",
                    title=f"社区连接稀疏：{community.label}",
                    description="该社区包含多个节点，但内部确定性关系较少。",
                    community_id=community.community_id,
                    properties={"cohesion": community.cohesion},
                )
            )
        for alignment in alignments:
            if alignment.status == "covered":
                continue
            severity = "high" if alignment.status == "gap" else "medium"
            insights.append(
                _insight_item(
                    kind="capability_gap",
                    severity=severity,
                    title=f"目标能力缺口：{alignment.label}",
                    description="本地知识尚未覆盖全部目标关键词。",
                    capability_id=alignment.capability_id,
                    properties={
                        "coverage": alignment.coverage,
                        "missing_keywords": list(alignment.missing_keywords),
                        "research_query": " ".join(alignment.missing_keywords[:6]),
                    },
                )
            )
        return tuple(sorted(insights, key=lambda item: (item.kind, item.insight_id)))

    def _insert(
        self,
        connection: sqlite3.Connection,
        revision: str,
        communities: tuple[KnowledgeGraphCommunity, ...],
        metrics: tuple[KnowledgeGraphNodeMetric, ...],
        alignments: tuple[KnowledgeGoalAlignment, ...],
        insights: tuple[KnowledgeGraphInsight, ...],
    ) -> None:
        connection.executemany(
            """
            INSERT INTO knowledge_graph_communities (
                analysis_revision, community_id, label, node_count,
                edge_count, cohesion, properties_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    revision,
                    item.community_id,
                    item.label,
                    item.node_count,
                    item.edge_count,
                    item.cohesion,
                    _json(item.properties),
                )
                for item in communities
            ),
        )
        connection.executemany(
            """
            INSERT INTO knowledge_graph_node_metrics (
                analysis_revision, node_id, community_id, degree,
                weighted_degree, bridge_score
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    revision,
                    item.node_id,
                    item.community_id,
                    item.degree,
                    item.weighted_degree,
                    item.bridge_score,
                )
                for item in metrics
            ),
        )
        connection.executemany(
            """
            INSERT INTO knowledge_graph_goal_alignments (
                analysis_revision, capability_id, label, coverage, status,
                matched_keywords_json, missing_keywords_json, matched_node_ids_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    revision,
                    item.capability_id,
                    item.label,
                    item.coverage,
                    item.status,
                    _json(item.matched_keywords),
                    _json(item.missing_keywords),
                    _json(item.matched_node_ids),
                )
                for item in alignments
            ),
        )
        connection.executemany(
            """
            INSERT INTO knowledge_graph_insights (
                analysis_revision, insight_id, kind, severity, title,
                description, node_id, community_id, capability_id, properties_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    revision,
                    item.insight_id,
                    item.kind,
                    item.severity,
                    item.title,
                    item.description,
                    item.node_id,
                    item.community_id,
                    item.capability_id,
                    _json(item.properties),
                )
                for item in insights
            ),
        )


def _insight_item(
    *,
    kind: str,
    severity: str,
    title: str,
    description: str,
    node_id: str | None = None,
    community_id: str | None = None,
    capability_id: str | None = None,
    properties: dict[str, Any] | None = None,
) -> KnowledgeGraphInsight:
    identity = "\0".join((kind, node_id or "", community_id or "", capability_id or "", title))
    insight_id = "kinsight_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return KnowledgeGraphInsight(
        insight_id=insight_id,
        kind=kind,
        severity=severity,
        title=title,
        description=description,
        node_id=node_id,
        community_id=community_id,
        capability_id=capability_id,
        properties=properties or {},
    )


def _keyword_present(keyword: str, text: str) -> bool:
    needle = " ".join(keyword.casefold().split())
    haystack = " ".join(text.casefold().split())
    if not needle:
        return False
    if all(
        character.isascii() and (character.isalnum() or character in "_- ") for character in needle
    ):
        return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack) is not None
    return needle in haystack


def _analysis_snapshot(row: sqlite3.Row) -> KnowledgeGraphAnalysisSnapshot:
    return KnowledgeGraphAnalysisSnapshot(
        analysis_revision=str(row["analysis_revision"]),
        workspace_id=str(row["workspace_id"]),
        graph_revision=str(row["graph_revision"]),
        goal_revision=str(row["goal_revision"]),
        algorithm_id=str(row["algorithm_id"]),
        algorithm_version=str(row["algorithm_version"]),
        seed=int(row["seed"]),
        resolution=float(row["resolution"]),
        threshold=float(row["threshold"]),
        status=str(row["status"]),
        community_count=int(row["community_count"]),
        insight_count=int(row["insight_count"]),
        error=str(row["error"]) if row["error"] else None,
        created_at=str(row["created_at"]),
        completed_at=str(row["completed_at"]) if row["completed_at"] else None,
    )


def _community(row: sqlite3.Row) -> KnowledgeGraphCommunity:
    return KnowledgeGraphCommunity(
        community_id=str(row["community_id"]),
        label=str(row["label"]),
        node_count=int(row["node_count"]),
        edge_count=int(row["edge_count"]),
        cohesion=float(row["cohesion"]),
        properties=json.loads(str(row["properties_json"])),
    )


def _metric(row: sqlite3.Row) -> KnowledgeGraphNodeMetric:
    return KnowledgeGraphNodeMetric(
        node_id=str(row["node_id"]),
        community_id=str(row["community_id"]),
        degree=int(row["degree"]),
        weighted_degree=float(row["weighted_degree"]),
        bridge_score=float(row["bridge_score"]),
    )


def _alignment(row: sqlite3.Row) -> KnowledgeGoalAlignment:
    return KnowledgeGoalAlignment(
        capability_id=str(row["capability_id"]),
        label=str(row["label"]),
        coverage=float(row["coverage"]),
        status=str(row["status"]),
        matched_keywords=tuple(json.loads(str(row["matched_keywords_json"]))),
        missing_keywords=tuple(json.loads(str(row["missing_keywords_json"]))),
        matched_node_ids=tuple(json.loads(str(row["matched_node_ids_json"]))),
    )


def _insight(row: sqlite3.Row) -> KnowledgeGraphInsight:
    return KnowledgeGraphInsight(
        insight_id=str(row["insight_id"]),
        kind=str(row["kind"]),
        severity=str(row["severity"]),
        title=str(row["title"]),
        description=str(row["description"]),
        node_id=str(row["node_id"]) if row["node_id"] else None,
        community_id=str(row["community_id"]) if row["community_id"] else None,
        capability_id=str(row["capability_id"]) if row["capability_id"] else None,
        properties=json.loads(str(row["properties_json"])),
    )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _now() -> str:
    return datetime.now(UTC).isoformat()
