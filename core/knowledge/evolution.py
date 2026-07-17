"""Deterministic evidence deposits for the autonomous knowledge-learning loop."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from core.knowledge.retrieval import KnowledgeChunk

_FORMAT_VERSION = 1
GENERATOR_ID = "sage.evidence-learning"
GENERATOR_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class EvidenceLearningCitation:
    citation_id: str
    chunk_id: str
    page_id: str
    page_revision: str
    source_id: str
    source_revision: str
    source_kind: str
    source_relative_path: str
    block_id: str
    title: str
    heading_path: tuple[str, ...]
    excerpt: str


@dataclass(frozen=True, slots=True)
class EvidenceLearning:
    learning_id: str
    input_hash: str
    topic: str
    page_id: str
    target_path: str
    session_id: str
    run_id: str
    event_id: str
    generator_id: str
    generator_version: str
    citations: tuple[EvidenceLearningCitation, ...]
    rendered_markdown: str


def build_evidence_learning(
    *,
    topic: str,
    page_id: str,
    target_path: str,
    resolved_citations: tuple[tuple[str, KnowledgeChunk], ...],
    base_content: str = "",
    session_id: str = "",
    run_id: str = "",
    event_id: str = "",
) -> EvidenceLearning:
    """Build an extractive note; callers cannot inject uncited factual prose."""

    normalized_topic = " ".join(topic.split())
    if not normalized_topic or len(normalized_topic) > 160:
        raise ValueError("knowledge learning topic must be between 1 and 160 characters")
    if not resolved_citations or len(resolved_citations) > 8:
        raise ValueError("knowledge learning requires between 1 and 8 citations")
    ordered = tuple(
        sorted(
            resolved_citations,
            key=lambda item: (
                item[1].source_relative_path,
                item[1].ordinal,
                item[0],
            ),
        )
    )
    input_payload = {
        "topic": normalized_topic,
        "citations": [citation_id for citation_id, _chunk in ordered],
    }
    input_hash = "sha256:" + hashlib.sha256(
        json.dumps(input_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    learning_id = "klearn_" + hashlib.sha256(
        f"{GENERATOR_ID}\0{GENERATOR_VERSION}\0{input_hash}".encode()
    ).hexdigest()[:32]
    citations = tuple(
        EvidenceLearningCitation(
            citation_id=citation_id,
            chunk_id=chunk.chunk_id,
            page_id=chunk.page_id,
            page_revision=chunk.page_revision,
            source_id=chunk.source_id,
            source_revision=chunk.source_revision,
            source_kind=chunk.source_kind,
            source_relative_path=chunk.source_relative_path,
            block_id=chunk.block_id,
            title=chunk.title,
            heading_path=chunk.heading_path,
            excerpt=chunk.text[:1_200].rstrip(),
        )
        for citation_id, chunk in ordered
    )
    rendered = _render_markdown(
        learning_id=learning_id,
        input_hash=input_hash,
        topic=normalized_topic,
        page_id=page_id,
        citations=citations,
        base_content=base_content,
        session_id=session_id,
        run_id=run_id,
        event_id=event_id,
    )
    return EvidenceLearning(
        learning_id=learning_id,
        input_hash=input_hash,
        topic=normalized_topic,
        page_id=page_id,
        target_path=target_path,
        session_id=session_id,
        run_id=run_id,
        event_id=event_id,
        generator_id=GENERATOR_ID,
        generator_version=GENERATOR_VERSION,
        citations=citations,
        rendered_markdown=rendered,
    )


def serialize_evidence_learning(value: EvidenceLearning) -> str:
    payload = {
        "format_version": _FORMAT_VERSION,
        "learning_id": value.learning_id,
        "input_hash": value.input_hash,
        "topic": value.topic,
        "page_id": value.page_id,
        "target_path": value.target_path,
        "session_id": value.session_id,
        "run_id": value.run_id,
        "event_id": value.event_id,
        "generator_id": value.generator_id,
        "generator_version": value.generator_version,
        "rendered_markdown": value.rendered_markdown,
        "citations": [
            {
                "citation_id": item.citation_id,
                "chunk_id": item.chunk_id,
                "page_id": item.page_id,
                "page_revision": item.page_revision,
                "source_id": item.source_id,
                "source_revision": item.source_revision,
                "source_kind": item.source_kind,
                "source_relative_path": item.source_relative_path,
                "block_id": item.block_id,
                "title": item.title,
                "heading_path": list(item.heading_path),
                "excerpt": item.excerpt,
            }
            for item in value.citations
        ],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def deserialize_evidence_learning(value: str) -> EvidenceLearning:
    payload: Any = json.loads(value)
    if not isinstance(payload, dict) or payload.get("format_version") != _FORMAT_VERSION:
        raise ValueError("unsupported evidence learning format")
    raw_citations = payload.get("citations")
    if not isinstance(raw_citations, list):
        raise ValueError("invalid evidence learning citations")
    citations = tuple(
        EvidenceLearningCitation(
            citation_id=str(item["citation_id"]),
            chunk_id=str(item["chunk_id"]),
            page_id=str(item["page_id"]),
            page_revision=str(item["page_revision"]),
            source_id=str(item["source_id"]),
            source_revision=str(item["source_revision"]),
            source_kind=str(item["source_kind"]),
            source_relative_path=str(item["source_relative_path"]),
            block_id=str(item["block_id"]),
            title=str(item["title"]),
            heading_path=tuple(str(value) for value in item["heading_path"]),
            excerpt=str(item["excerpt"]),
        )
        for item in raw_citations
        if isinstance(item, dict) and isinstance(item.get("heading_path"), list)
    )
    if len(citations) != len(raw_citations):
        raise ValueError("invalid evidence learning citations")
    return EvidenceLearning(
        learning_id=str(payload["learning_id"]),
        input_hash=str(payload["input_hash"]),
        topic=str(payload["topic"]),
        page_id=str(payload["page_id"]),
        target_path=str(payload["target_path"]),
        session_id=str(payload["session_id"]),
        run_id=str(payload["run_id"]),
        event_id=str(payload["event_id"]),
        generator_id=str(payload["generator_id"]),
        generator_version=str(payload["generator_version"]),
        citations=citations,
        rendered_markdown=str(payload["rendered_markdown"]),
    )


def _render_markdown(
    *,
    learning_id: str,
    input_hash: str,
    topic: str,
    page_id: str,
    citations: tuple[EvidenceLearningCitation, ...],
    base_content: str,
    session_id: str,
    run_id: str,
    event_id: str,
) -> str:
    if base_content.strip():
        lines = [base_content.rstrip(), ""]
    else:
        lines = [
            "---",
            f"id: {page_id}",
            "type: evidence-learning",
            f"title: {topic}",
            "status: active",
            "visibility: private",
            f"generator_id: {GENERATOR_ID}",
            f"generator_version: {GENERATOR_VERSION}",
            "---",
            "",
            f"# {topic}",
            "",
            "> 本页只机械整理已批准知识的引用片段，不包含模型自由生成的新事实。",
            "",
        ]
    lines.extend(
        [
            f"## Evidence snapshot `{learning_id}`",
            "",
            f"- input hash: `{input_hash}`",
            f"- session: `{session_id or 'browser'}`",
            f"- run: `{run_id or 'manual'}`",
            f"- event: `{event_id or 'manual'}`",
            "",
        ]
    )
    for item in citations:
        heading = " / ".join(item.heading_path) or "正文"
        lines.extend(
            [
                f"### {item.title} / {heading}",
                "",
                item.excerpt,
                "",
                f"- citation: `{item.citation_id}`",
                f"- source: `{item.source_relative_path}`",
                f"- page revision: `{item.page_revision}`",
                f"- source revision: `{item.source_revision}`",
                f"- block: `{item.block_id}`",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"
