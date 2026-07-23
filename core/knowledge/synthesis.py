"""Deterministic workspace synthesis over approved source understandings."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from core.knowledge.understanding import SourceUnderstanding

_FORMAT_VERSION = 1
_GENERATOR_ID = "sage.workspace-index"
_GENERATOR_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class WorkspaceSourceEvidence:
    page_id: str
    page_revision: str
    proposal_id: str
    understanding_id: str
    source_id: str
    source_revision: str
    title: str
    path: str
    summary: str
    topics: tuple[str, ...]
    citation_block_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorkspaceSynthesis:
    synthesis_id: str
    input_hash: str
    generator_id: str
    generator_version: str
    sources: tuple[WorkspaceSourceEvidence, ...]
    rendered_markdown: str


def source_evidence(
    *,
    page_id: str,
    page_revision: str,
    proposal_id: str,
    path: str,
    understanding: SourceUnderstanding,
) -> WorkspaceSourceEvidence:
    return WorkspaceSourceEvidence(
        page_id=page_id,
        page_revision=page_revision,
        proposal_id=proposal_id,
        understanding_id=understanding.understanding_id,
        source_id=understanding.source_id,
        source_revision=understanding.source_revision,
        title=understanding.title,
        path=path,
        summary=understanding.summary[:800],
        topics=understanding.topics[:12],
        citation_block_ids=tuple(citation.block_id for citation in understanding.citations[:8]),
    )


def synthesize_workspace(
    evidence: tuple[WorkspaceSourceEvidence, ...],
) -> WorkspaceSynthesis:
    ordered = tuple(sorted(evidence, key=lambda item: (item.title.lower(), item.page_id)))
    if not ordered:
        raise ValueError("workspace synthesis requires at least one approved source")
    if len(ordered) > 500:
        raise ValueError("workspace synthesis exceeds 500 source limit")
    input_payload = [
        {
            "page_id": item.page_id,
            "page_revision": item.page_revision,
            "proposal_id": item.proposal_id,
            "understanding_id": item.understanding_id,
            "source_id": item.source_id,
            "source_revision": item.source_revision,
        }
        for item in ordered
    ]
    input_json = json.dumps(input_payload, sort_keys=True, separators=(",", ":"))
    input_hash = "sha256:" + hashlib.sha256(input_json.encode("utf-8")).hexdigest()
    synthesis_id = (
        "ksyn_"
        + hashlib.sha256(
            f"{input_hash}\0{_GENERATOR_ID}\0{_GENERATOR_VERSION}".encode()
        ).hexdigest()[:32]
    )
    rendered = _render_markdown(synthesis_id, input_hash, ordered)
    return WorkspaceSynthesis(
        synthesis_id=synthesis_id,
        input_hash=input_hash,
        generator_id=_GENERATOR_ID,
        generator_version=_GENERATOR_VERSION,
        sources=ordered,
        rendered_markdown=rendered,
    )


def serialize_synthesis(value: WorkspaceSynthesis) -> str:
    payload = {
        "format_version": _FORMAT_VERSION,
        "synthesis_id": value.synthesis_id,
        "input_hash": value.input_hash,
        "generator_id": value.generator_id,
        "generator_version": value.generator_version,
        "rendered_markdown": value.rendered_markdown,
        "sources": [
            {
                "page_id": item.page_id,
                "page_revision": item.page_revision,
                "proposal_id": item.proposal_id,
                "understanding_id": item.understanding_id,
                "source_id": item.source_id,
                "source_revision": item.source_revision,
                "title": item.title,
                "path": item.path,
                "summary": item.summary,
                "topics": list(item.topics),
                "citation_block_ids": list(item.citation_block_ids),
            }
            for item in value.sources
        ],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def deserialize_synthesis(value: str) -> WorkspaceSynthesis:
    payload: Any = json.loads(value)
    if not isinstance(payload, dict) or payload.get("format_version") != _FORMAT_VERSION:
        raise ValueError("unsupported workspace synthesis format")
    raw_sources = payload.get("sources")
    if not isinstance(raw_sources, list):
        raise ValueError("invalid workspace synthesis")
    sources: list[WorkspaceSourceEvidence] = []
    for raw in raw_sources:
        if (
            not isinstance(raw, dict)
            or not isinstance(raw.get("topics"), list)
            or not isinstance(raw.get("citation_block_ids"), list)
        ):
            raise ValueError("invalid workspace synthesis source")
        sources.append(
            WorkspaceSourceEvidence(
                page_id=str(raw["page_id"]),
                page_revision=str(raw["page_revision"]),
                proposal_id=str(raw["proposal_id"]),
                understanding_id=str(raw["understanding_id"]),
                source_id=str(raw["source_id"]),
                source_revision=str(raw["source_revision"]),
                title=str(raw["title"]),
                path=str(raw["path"]),
                summary=str(raw["summary"]),
                topics=tuple(str(topic) for topic in raw["topics"]),
                citation_block_ids=tuple(str(item) for item in raw["citation_block_ids"]),
            )
        )
    return WorkspaceSynthesis(
        synthesis_id=str(payload["synthesis_id"]),
        input_hash=str(payload["input_hash"]),
        generator_id=str(payload["generator_id"]),
        generator_version=str(payload["generator_version"]),
        sources=tuple(sources),
        rendered_markdown=str(payload["rendered_markdown"]),
    )


def _render_markdown(
    synthesis_id: str,
    input_hash: str,
    sources: tuple[WorkspaceSourceEvidence, ...],
) -> str:
    lines = [
        "---",
        "id: page_workspace_overview",
        "type: overview",
        "title: Knowledge Overview",
        "status: draft",
        "visibility: private",
        f"synthesis_id: {synthesis_id}",
        f"input_hash: {input_hash}",
        f"generator_id: {_GENERATOR_ID}",
        f"generator_version: {_GENERATOR_VERSION}",
        "---",
        "",
        "# Knowledge Overview",
        "",
        "> 本页只综合已经批准并写入 Git 的来源页面。每条摘要保留页面 revision 与 block citation。",
        "",
        f"## 已批准来源（{len(sources)}）",
        "",
    ]
    for item in sources:
        citations = (
            ", ".join(f"`{block_id}`" for block_id in item.citation_block_ids) or "无块级引用"
        )
        topics = "、".join(item.topics) or "未提取主题"
        lines.extend(
            [
                f"### [[{item.path}|{item.title}]]",
                "",
                item.summary,
                "",
                f"- topics: {topics}",
                f"- page revision: `{item.page_revision}`",
                f"- source revision: `{item.source_revision}`",
                f"- understanding: `{item.understanding_id}`",
                f"- citations: {citations}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"
