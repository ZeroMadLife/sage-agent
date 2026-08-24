"""Knowledge-first LearningPlan and citation-bound learning-map contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Literal

from sage_harness import (
    EvidenceBundleItem,
    KnowledgeEvidence,
    KnowledgePort,
    TaskDAGNode,
    TaskDAGPlan,
)

from core.learning.tasks import LearningSourcePolicy, LearningTask, source_policy_revision

LearningUnitStatus = Literal["grounded", "source_gap", "unverified"]
LearningArtifactStatus = Literal["ready", "source_gap", "unverified", "blocked"]


@dataclass(frozen=True, slots=True)
class LearningCitation:
    citation_id: str
    title: str
    content: str
    content_hash: str
    page_revision: str
    source_revision: str
    url: str = ""
    fetched_at: str = ""


@dataclass(frozen=True, slots=True)
class KnowledgeUnit:
    unit_id: str
    ordinal: int
    title: str
    objective: str
    prerequisite_unit_ids: tuple[str, ...]
    source_policy_revision: str
    risk_class: str
    status: LearningUnitStatus
    evidence_refs: tuple[str, ...] = ()
    source_revisions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LearningPlan:
    schema_version: int
    plan_id: str
    plan_hash: str
    plan_revision: int
    workspace_id: str
    task_id: str
    task_revision: int
    goal_id: str
    goal_revision: str
    source_policy: LearningSourcePolicy
    source_policy_revision: str
    capability_revision: str
    catalog_revision: str
    dag_id: str
    dag_hash: str
    units: tuple[KnowledgeUnit, ...]


@dataclass(frozen=True, slots=True)
class LearningMapArtifact:
    schema_version: int
    artifact_id: str
    kind: Literal["learning_map"]
    plan_id: str
    unit_ids: tuple[str, ...]
    status: LearningArtifactStatus
    media_type: Literal["text/markdown"]
    content: str
    content_hash: str
    evidence_refs: tuple[str, ...]
    source_revisions: tuple[str, ...]
    citation_count: int


@dataclass(frozen=True, slots=True)
class LearningMapOutcome:
    plan: LearningPlan
    artifact: LearningMapArtifact
    citations: tuple[LearningCitation, ...]
    gap_reason: str = ""


class LearningMapService:
    """Build one deterministic map without granting Research or persistence authority."""

    def __init__(
        self,
        *,
        knowledge_port: KnowledgePort | None,
        top_k: int = 8,
        token_budget: int = 3_000,
    ) -> None:
        if not 1 <= top_k <= 20:
            raise ValueError("top_k must be between 1 and 20")
        if not 256 <= token_budget <= 20_000:
            raise ValueError("token_budget must be between 256 and 20000")
        self.knowledge_port = knowledge_port
        self.top_k = top_k
        self.token_budget = token_budget

    async def build(
        self,
        *,
        task: LearningTask,
        parent_run_id: str,
        capability_revision: str,
        catalog_revision: str,
    ) -> LearningMapOutcome:
        _validate_inputs(task, parent_run_id, capability_revision, catalog_revision)
        unit = _unit_for_task(task)
        citations: tuple[LearningCitation, ...] = ()
        gap_reason = ""
        if task.source_policy.knowledge == "disabled":
            gap_reason = "knowledge_disabled"
        elif self.knowledge_port is None or not self.knowledge_port.available:
            gap_reason = "knowledge_unavailable"
        else:
            try:
                result = await self.knowledge_port.search(
                    _knowledge_query(task),
                    workspace_id=self.knowledge_port.workspace_id,
                    top_k=self.top_k,
                    token_budget=self.token_budget,
                )
            except Exception:
                gap_reason = "knowledge_failed"
            else:
                citations = _valid_citations(result.evidence)
                if not citations:
                    gap_reason = (
                        "knowledge_no_evidence"
                        if not result.evidence
                        else "knowledge_revision_missing"
                    )
        if citations:
            unit = replace(
                unit,
                status="grounded",
                evidence_refs=tuple(item.citation_id for item in citations),
                source_revisions=tuple(sorted({item.source_revision for item in citations})),
            )
        plan = _plan_for_task(
            task,
            unit,
            capability_revision=capability_revision,
            catalog_revision=catalog_revision,
        )
        artifact = _artifact_for_plan(task, plan, citations, gap_reason=gap_reason)
        return LearningMapOutcome(
            plan=plan,
            artifact=artifact,
            citations=citations,
            gap_reason=gap_reason,
        )


def _validate_inputs(
    task: LearningTask,
    parent_run_id: str,
    capability_revision: str,
    catalog_revision: str,
) -> None:
    if task.status != "active" or not task.learning_goal_ref:
        raise ValueError("learning map requires an active task with a Goal binding")
    for name, value in (
        ("parent_run_id", parent_run_id),
        ("capability_revision", capability_revision),
        ("catalog_revision", catalog_revision),
    ):
        if not value.strip() or len(value) > 256:
            raise ValueError(f"{name} must be non-empty and bounded")


def _unit_for_task(task: LearningTask) -> KnowledgeUnit:
    policy_revision = source_policy_revision(task.source_policy)
    title = task.topic
    objective = task.desired_outcome or f"建立对{task.topic}的可验证理解"
    identity = _canonical_hash(
        {
            "schema_version": 1,
            "ordinal": 1,
            "title": title,
            "objective": objective,
            "prerequisite_unit_ids": [],
            "source_policy_revision": policy_revision,
            "risk_class": task.risk_class,
        }
    )
    return KnowledgeUnit(
        unit_id=f"lunit_{identity[:24]}",
        ordinal=1,
        title=title,
        objective=objective,
        prerequisite_unit_ids=(),
        source_policy_revision=policy_revision,
        risk_class=task.risk_class,
        status="source_gap",
    )


def _plan_for_task(
    task: LearningTask,
    unit: KnowledgeUnit,
    *,
    capability_revision: str,
    catalog_revision: str,
) -> LearningPlan:
    dag = TaskDAGPlan.create(
        nodes=(
            TaskDAGNode(
                node_id="knowledge",
                description="读取 revision-bound Knowledge evidence",
                prompt="Read only the frozen Knowledge scope and return evidence receipts.",
                subagent_type="research",
            ),
            TaskDAGNode(
                node_id="research",
                description="按来源策略条件补足只读证据",
                prompt="Research only when policy, sufficiency, risk, and budget allow it.",
                subagent_type="research",
                depends_on=("knowledge",),
            ),
            TaskDAGNode(
                node_id="synthesize",
                description="生成 citation-bound Learning Artifact",
                prompt="Synthesize only from authorized evidence and preserve citation IDs.",
                subagent_type="synthesize",
                depends_on=("knowledge", "research"),
            ),
        ),
        max_concurrent=1,
        allowed_profiles=frozenset({"research", "synthesize"}),
    )
    goal = task.learning_goal_ref or {}
    policy_revision = source_policy_revision(task.source_policy)
    payload = {
        "schema_version": 1,
        "plan_revision": 1,
        "workspace_id": task.workspace_id,
        "task_id": task.task_id,
        "task_revision": task.task_revision,
        "goal_id": str(goal.get("goal_id", "")),
        "goal_revision": str(goal.get("goal_revision", "")),
        "source_policy": {
            "knowledge": task.source_policy.knowledge,
            "web": task.source_policy.web,
            "domains": list(task.source_policy.domains),
            "freshness": task.source_policy.freshness,
        },
        "source_policy_revision": policy_revision,
        "capability_revision": capability_revision,
        "catalog_revision": catalog_revision,
        "dag_hash": dag.dag_hash,
        "unit_ids": [unit.unit_id],
    }
    digest = _canonical_hash(payload)
    return LearningPlan(
        schema_version=1,
        plan_id=f"lplan_{digest[:24]}",
        plan_hash=f"sha256:{digest}",
        plan_revision=1,
        workspace_id=task.workspace_id,
        task_id=task.task_id,
        task_revision=task.task_revision,
        goal_id=str(goal["goal_id"]),
        goal_revision=str(goal["goal_revision"]),
        source_policy=task.source_policy,
        source_policy_revision=policy_revision,
        capability_revision=capability_revision,
        catalog_revision=catalog_revision,
        dag_id=dag.dag_id,
        dag_hash=dag.dag_hash,
        units=(unit,),
    )


def _valid_citations(evidence: tuple[KnowledgeEvidence, ...]) -> tuple[LearningCitation, ...]:
    selected: list[LearningCitation] = []
    seen: set[str] = set()
    for item in evidence:
        citation_id = item.citation_id.strip()
        content = item.content.strip()
        page_revision = item.page_revision.strip()
        source_revision = item.source_revision.strip()
        if (
            not citation_id
            or citation_id in seen
            or not content
            or not page_revision
            or not source_revision
        ):
            continue
        seen.add(citation_id)
        selected.append(
            LearningCitation(
                citation_id=citation_id,
                title=str(item.metadata.get("title", "来源"))[:300],
                content=content,
                content_hash=f"sha256:{hashlib.sha256(content.encode()).hexdigest()}",
                page_revision=page_revision,
                source_revision=source_revision,
            )
        )
    return tuple(selected)


def _artifact_for_plan(
    task: LearningTask,
    plan: LearningPlan,
    citations: tuple[LearningCitation, ...],
    *,
    gap_reason: str,
) -> LearningMapArtifact:
    unit = plan.units[0]
    lines = [
        f"# {task.topic}学习地图",
        "",
        "## 学习目标",
        unit.objective,
        "",
        "## 学习单元",
        f"### 1. {unit.title}",
    ]
    if citations:
        lines.extend(
            [
                "来源状态：已由当前 revision 的证据支持。",
                "",
                "## 来源",
                *(
                    f"- {item.title} [{item.citation_id}]" + (f"({item.url})" if item.url else "")
                    for item in citations
                ),
            ]
        )
    else:
        lines.extend(
            [
                "来源状态：source_gap / unverified。",
                f"缺口原因：{gap_reason or 'knowledge_no_evidence'}。",
            ]
        )
    if task.risk_notice:
        lines.extend(["", "## 风险边界", task.risk_notice])
    content = "\n".join(lines).strip() + "\n"
    content_hash = f"sha256:{hashlib.sha256(content.encode()).hexdigest()}"
    identity = _canonical_hash(
        {
            "schema_version": 1,
            "kind": "learning_map",
            "task_id": task.task_id,
            "task_revision": task.task_revision,
            "plan_id": plan.plan_id,
            "unit_ids": [unit.unit_id],
        }
    )
    return LearningMapArtifact(
        schema_version=1,
        artifact_id=f"lart_{identity[:24]}",
        kind="learning_map",
        plan_id=plan.plan_id,
        unit_ids=(unit.unit_id,),
        status="ready" if citations else "source_gap",
        media_type="text/markdown",
        content=content,
        content_hash=content_hash,
        evidence_refs=tuple(item.citation_id for item in citations),
        source_revisions=tuple(sorted({item.source_revision for item in citations})),
        citation_count=len(citations),
    )


def synthesize_research_map(
    task: LearningTask,
    plan: LearningPlan,
    evidence: tuple[EvidenceBundleItem, ...],
) -> tuple[LearningMapArtifact, tuple[LearningCitation, ...]]:
    """Build controlled Markdown only from the Research service's validated bundle."""
    citations = tuple(
        LearningCitation(
            citation_id=item.evidence_ref,
            title=item.title,
            content=item.content,
            content_hash=item.content_hash,
            page_revision=item.content_hash,
            source_revision=item.content_hash,
            url=item.canonical_url,
            fetched_at=str(item.metadata.get("fetched_at", "")),
        )
        for item in evidence
        if item.evidence_ref
        and item.title
        and item.content
        and item.content_hash
        and item.canonical_url
        and str(item.metadata.get("fetched_at", ""))
    )
    return _artifact_for_plan(
        task,
        plan,
        citations,
        gap_reason="" if citations else "learning_research_no_evidence",
    ), citations


def _knowledge_query(task: LearningTask) -> str:
    return " ".join(filter(None, (task.topic, task.desired_outcome or "")))


def _canonical_hash(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "KnowledgeUnit",
    "LearningArtifactStatus",
    "LearningCitation",
    "LearningMapArtifact",
    "LearningMapOutcome",
    "LearningMapService",
    "LearningPlan",
    "LearningUnitStatus",
    "synthesize_research_map",
]
