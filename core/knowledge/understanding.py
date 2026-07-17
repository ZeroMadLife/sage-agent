"""Deterministic source-understanding artifacts with block-level citations."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from core.knowledge.parsing import ParsedDocument

_UNDERSTANDING_VERSION = 1
_GENERATOR_ID = "sage.extractive"
_GENERATOR_VERSION = "1.0.0"
_SUMMARY_LIMIT = 800
_MAX_SECTIONS = 80
_MAX_TOPICS = 12


@dataclass(frozen=True, slots=True)
class UnderstandingCitation:
    block_id: str
    page: int | None
    heading_path: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SourceSection:
    title: str
    block_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SourceUnderstanding:
    understanding_id: str
    artifact_id: str
    source_id: str
    source_revision: str
    title: str
    summary: str
    sections: tuple[SourceSection, ...]
    topics: tuple[str, ...]
    block_kind_counts: tuple[tuple[str, int], ...]
    citations: tuple[UnderstandingCitation, ...]
    generator_id: str
    generator_version: str


def understand_source(artifact_id: str, document: ParsedDocument) -> SourceUnderstanding:
    """Build a bounded extractive view without inventing unsupported facts."""
    eligible = [
        block
        for block in document.blocks
        if block.kind in {"paragraph", "list", "quote", "table", "code"}
        and block.text.strip()
    ]
    summary_parts: list[str] = []
    summary_length = 0
    citations: list[UnderstandingCitation] = []
    for block in eligible:
        normalized = _normalize_text(block.text)
        if not normalized:
            continue
        remaining = _SUMMARY_LIMIT - summary_length
        if remaining <= 0:
            break
        excerpt = normalized[:remaining].rstrip()
        summary_parts.append(excerpt)
        summary_length += len(excerpt) + 1
        citations.append(
            UnderstandingCitation(
                block_id=block.block_id,
                page=block.page,
                heading_path=block.heading_path,
            )
        )
    summary = " ".join(summary_parts).strip()
    if not summary:
        summary = document.title
        if document.blocks:
            first = document.blocks[0]
            citations.append(
                UnderstandingCitation(first.block_id, first.page, first.heading_path)
            )

    section_blocks: dict[str, list[str]] = {}
    for block in document.blocks:
        title = block.heading_path[-1] if block.heading_path else document.title
        section_blocks.setdefault(title[:300], []).append(block.block_id)
        if len(section_blocks) >= _MAX_SECTIONS:
            break
    sections = tuple(
        SourceSection(title=title, block_ids=tuple(block_ids))
        for title, block_ids in section_blocks.items()
    )
    topics = _topics(document)
    kind_counts = tuple(sorted(Counter(block.kind for block in document.blocks).items()))
    content_hash = hashlib.sha256(
        serialize_understanding_payload(
            artifact_id=artifact_id,
            document=document,
            summary=summary,
            sections=sections,
            topics=topics,
            block_kind_counts=kind_counts,
            citations=tuple(citations),
        ).encode("utf-8")
    ).hexdigest()
    return SourceUnderstanding(
        understanding_id=f"kund_{content_hash[:32]}",
        artifact_id=artifact_id,
        source_id=document.source_id,
        source_revision=document.source_revision,
        title=document.title,
        summary=summary,
        sections=sections,
        topics=topics,
        block_kind_counts=kind_counts,
        citations=tuple(citations),
        generator_id=_GENERATOR_ID,
        generator_version=_GENERATOR_VERSION,
    )


def serialize_understanding(value: SourceUnderstanding) -> str:
    payload = _payload(
        artifact_id=value.artifact_id,
        source_id=value.source_id,
        source_revision=value.source_revision,
        title=value.title,
        summary=value.summary,
        sections=value.sections,
        topics=value.topics,
        block_kind_counts=value.block_kind_counts,
        citations=value.citations,
        generator_id=value.generator_id,
        generator_version=value.generator_version,
    )
    payload["understanding_id"] = value.understanding_id
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def deserialize_understanding(value: str) -> SourceUnderstanding:
    payload: Any = json.loads(value)
    if not isinstance(payload, dict) or payload.get("format_version") != _UNDERSTANDING_VERSION:
        raise ValueError("unsupported source understanding format")
    raw_sections = payload.get("sections")
    raw_citations = payload.get("citations")
    raw_counts = payload.get("block_kind_counts")
    raw_topics = payload.get("topics")
    if (
        not isinstance(raw_sections, list)
        or not isinstance(raw_citations, list)
        or not isinstance(raw_counts, list)
        or not isinstance(raw_topics, list)
    ):
        raise ValueError("invalid source understanding")
    return SourceUnderstanding(
        understanding_id=str(payload["understanding_id"]),
        artifact_id=str(payload["artifact_id"]),
        source_id=str(payload["source_id"]),
        source_revision=str(payload["source_revision"]),
        title=str(payload["title"]),
        summary=str(payload["summary"]),
        sections=tuple(
            SourceSection(
                title=str(section["title"]),
                block_ids=tuple(str(block_id) for block_id in section["block_ids"]),
            )
            for section in raw_sections
            if isinstance(section, dict) and isinstance(section.get("block_ids"), list)
        ),
        topics=tuple(str(topic) for topic in raw_topics),
        block_kind_counts=tuple(
            (str(item[0]), int(item[1]))
            for item in raw_counts
            if isinstance(item, list) and len(item) == 2
        ),
        citations=tuple(
            UnderstandingCitation(
                block_id=str(citation["block_id"]),
                page=int(citation["page"]) if citation.get("page") is not None else None,
                heading_path=tuple(str(part) for part in citation["heading_path"]),
            )
            for citation in raw_citations
            if isinstance(citation, dict) and isinstance(citation.get("heading_path"), list)
        ),
        generator_id=str(payload["generator_id"]),
        generator_version=str(payload["generator_version"]),
    )


def serialize_understanding_payload(
    *,
    artifact_id: str,
    document: ParsedDocument,
    summary: str,
    sections: tuple[SourceSection, ...],
    topics: tuple[str, ...],
    block_kind_counts: tuple[tuple[str, int], ...],
    citations: tuple[UnderstandingCitation, ...],
) -> str:
    return json.dumps(
        _payload(
            artifact_id=artifact_id,
            source_id=document.source_id,
            source_revision=document.source_revision,
            title=document.title,
            summary=summary,
            sections=sections,
            topics=topics,
            block_kind_counts=block_kind_counts,
            citations=citations,
            generator_id=_GENERATOR_ID,
            generator_version=_GENERATOR_VERSION,
        ),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _payload(
    *,
    artifact_id: str,
    source_id: str,
    source_revision: str,
    title: str,
    summary: str,
    sections: tuple[SourceSection, ...],
    topics: tuple[str, ...],
    block_kind_counts: tuple[tuple[str, int], ...],
    citations: tuple[UnderstandingCitation, ...],
    generator_id: str,
    generator_version: str,
) -> dict[str, object]:
    return {
        "format_version": _UNDERSTANDING_VERSION,
        "artifact_id": artifact_id,
        "source_id": source_id,
        "source_revision": source_revision,
        "title": title,
        "summary": summary,
        "sections": [
            {"title": section.title, "block_ids": list(section.block_ids)}
            for section in sections
        ],
        "topics": list(topics),
        "block_kind_counts": [list(item) for item in block_kind_counts],
        "citations": [
            {
                "block_id": citation.block_id,
                "page": citation.page,
                "heading_path": list(citation.heading_path),
            }
            for citation in citations
        ],
        "generator_id": generator_id,
        "generator_version": generator_version,
    }


_TOPIC_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_.+#/-]{2,48}|[\u4e00-\u9fff]{2,12}")
_STOP_TOPICS = {
    "and", "the", "with", "from", "this", "that", "into", "for", "page",
    "一个", "可以", "这个", "我们", "以及", "进行", "通过", "使用", "需要", "实现",
}


def _topics(document: ParsedDocument) -> tuple[str, ...]:
    candidates: list[str] = [document.title]
    candidates.extend(
        part
        for block in document.blocks
        for part in block.heading_path
    )
    candidates.extend(
        match.group(0)
        for block in document.blocks
        for match in _TOPIC_WORD.finditer(block.text[:4000])
    )
    counts = Counter(
        normalized
        for value in candidates
        if (normalized := value.strip("#`*_ .,:;!?，。；：！？").lower())
        and normalized not in _STOP_TOPICS
        and len(normalized) <= 80
    )
    return tuple(
        value for value, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[
            :_MAX_TOPICS
        ]
    )


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" #`*_\t\r\n")
