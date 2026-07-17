"""Revision-aware chunking and deterministic hybrid-retrieval primitives."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Literal, Protocol

from core.knowledge.parsing import ParsedDocument

_LATIN_TOKEN = re.compile(r"[a-z0-9_]+", re.IGNORECASE)
_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_MAX_CHUNK_CHARS = 4_000
_CHUNK_OVERLAP_CHARS = 160
_MAX_CHUNKS_PER_REVISION = 2_000
_MAX_QUERY_TERMS = 64


@dataclass(frozen=True, slots=True)
class KnowledgeChunk:
    chunk_id: str
    workspace_id: str
    page_id: str
    page_revision: str
    page_path: str
    source_id: str
    source_revision: str
    source_kind: str
    source_relative_path: str
    proposal_id: str
    artifact_id: str | None
    block_id: str
    ordinal: int
    title: str
    heading_path: tuple[str, ...]
    page_number: int | None
    text: str
    token_count: int
    content_hash: str
    visibility: str
    language: str
    active: bool


@dataclass(frozen=True, slots=True)
class KnowledgeSearchHit:
    chunk: KnowledgeChunk
    citation_id: str
    rank: int
    rrf_score: float
    sparse_rank: int | None
    sparse_score: float | None
    dense_rank: int | None
    dense_score: float | None


@dataclass(frozen=True, slots=True)
class KnowledgeEvidence:
    """One bounded, citation-stable excerpt selected for an Agent context."""

    hit: KnowledgeSearchHit
    excerpt: str
    token_count: int
    truncated: bool


@dataclass(frozen=True, slots=True)
class KnowledgeRetrievalBundle:
    """A token-bounded evidence bundle shared by HTTP and coding tools."""

    query: str
    status: Literal["evidence_found", "no_evidence"]
    evidence: tuple[KnowledgeEvidence, ...]
    token_budget: int
    used_tokens: int
    omitted_count: int


@dataclass(frozen=True, slots=True)
class KnowledgeIndexSummary:
    backend: str
    embedding_model: str
    embedding_revision: str
    revision_count: int
    indexed_revision_count: int
    active_chunk_count: int
    total_chunk_count: int
    error_count: int


@dataclass(slots=True)
class _RrfState:
    score: float = 0.0
    sparse_rank: int | None = None
    sparse_score: float | None = None
    dense_rank: int | None = None
    dense_score: float | None = None


class DenseEmbeddingProvider(Protocol):
    model_id: str
    model_revision: str
    dimensions: int
    supports_semantic_recall: bool

    def embed(self, text: str) -> tuple[float, ...]:
        """Return one normalized vector for deterministic persistence and scoring."""


class HashingEmbeddingProvider:
    """Dependency-free local baseline; production can replace this with pgvector embeddings."""

    model_id = "sage.hashing"
    model_revision = "1.0.0"
    supports_semantic_recall = False

    def __init__(self, dimensions: int = 256) -> None:
        if dimensions < 32 or dimensions > 4_096:
            raise ValueError("embedding dimensions must be between 32 and 4096")
        self.dimensions = dimensions

    def embed(self, text: str) -> tuple[float, ...]:
        counts = Counter(lexical_terms(text))
        vector = [0.0] * self.dimensions
        for term, count in counts.items():
            digest = hashlib.sha256(term.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign * (1.0 + math.log(float(count)))
        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            vector = [value / norm for value in vector]
        return tuple(vector)


def chunk_document(
    document: ParsedDocument,
    *,
    workspace_id: str,
    page_id: str,
    page_revision: str,
    page_path: str,
    source_id: str,
    source_revision: str,
    source_kind: str,
    source_relative_path: str,
    proposal_id: str,
    artifact_id: str | None,
    title: str,
    visibility: str,
    active: bool,
) -> tuple[KnowledgeChunk, ...]:
    """Preserve parser blocks first and split only oversized semantic blocks."""

    chunks: list[KnowledgeChunk] = []
    for block in document.blocks:
        if block.kind in {"frontmatter", "heading"} or not block.text.strip():
            continue
        for part_index, text in enumerate(_split_oversized_block(block.text.strip())):
            content_hash = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
            ordinal = len(chunks)
            chunk_id = _stable_id(
                "kchunk",
                page_revision,
                block.block_id,
                str(part_index),
                content_hash,
            )
            chunks.append(
                KnowledgeChunk(
                    chunk_id=chunk_id,
                    workspace_id=workspace_id,
                    page_id=page_id,
                    page_revision=page_revision,
                    page_path=page_path,
                    source_id=source_id,
                    source_revision=source_revision,
                    source_kind=source_kind,
                    source_relative_path=source_relative_path,
                    proposal_id=proposal_id,
                    artifact_id=artifact_id,
                    block_id=block.block_id,
                    ordinal=ordinal,
                    title=title,
                    heading_path=block.heading_path,
                    page_number=block.page,
                    text=text,
                    token_count=max(1, len(lexical_terms(text))),
                    content_hash=content_hash,
                    visibility=visibility,
                    language=document.language,
                    active=active,
                )
            )
            if len(chunks) >= _MAX_CHUNKS_PER_REVISION:
                return tuple(chunks)
    if chunks:
        return tuple(chunks)
    fallback = title.strip() or "Untitled"
    content_hash = "sha256:" + hashlib.sha256(fallback.encode("utf-8")).hexdigest()
    return (
        KnowledgeChunk(
            chunk_id=_stable_id("kchunk", page_revision, document.document_id, content_hash),
            workspace_id=workspace_id,
            page_id=page_id,
            page_revision=page_revision,
            page_path=page_path,
            source_id=source_id,
            source_revision=source_revision,
            source_kind=source_kind,
            source_relative_path=source_relative_path,
            proposal_id=proposal_id,
            artifact_id=artifact_id,
            block_id=document.document_id,
            ordinal=0,
            title=title,
            heading_path=(),
            page_number=None,
            text=fallback,
            token_count=max(1, len(lexical_terms(fallback))),
            content_hash=content_hash,
            visibility=visibility,
            language=document.language,
            active=active,
        ),
    )


def lexical_terms(text: str, *, limit: int | None = None) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", text).lower()
    terms: list[str] = [match.group(0) for match in _LATIN_TOKEN.finditer(normalized)]
    for match in _CJK_RUN.finditer(normalized):
        value = match.group(0)
        terms.extend(value)
        terms.extend(value[index : index + 2] for index in range(max(0, len(value) - 1)))
    if limit is not None:
        return tuple(terms[:limit])
    return tuple(terms)


def fts_query(text: str) -> str:
    unique = tuple(dict.fromkeys(_query_terms(text)[:_MAX_QUERY_TERMS]))
    if not unique:
        raise ValueError("knowledge query has no searchable terms")
    return " OR ".join(f'"{term}"' for term in unique)


def _query_terms(text: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", text).lower()
    terms: list[str] = [match.group(0) for match in _LATIN_TOKEN.finditer(normalized)]
    for match in _CJK_RUN.finditer(normalized):
        value = match.group(0)
        if len(value) == 1:
            terms.append(value)
        else:
            terms.extend(value[index : index + 2] for index in range(len(value) - 1))
    return tuple(terms)


def index_text(chunk: KnowledgeChunk) -> str:
    values = (chunk.title, *chunk.heading_path, chunk.text)
    return " ".join(lexical_terms("\n".join(values)))


def embedding_text(chunk: KnowledgeChunk) -> str:
    return "\n".join((chunk.title, " / ".join(chunk.heading_path), chunk.text)).strip()


def serialize_vector(vector: tuple[float, ...]) -> str:
    return json.dumps(vector, ensure_ascii=True, separators=(",", ":"))


def deserialize_vector(value: str, *, dimensions: int) -> tuple[float, ...]:
    raw = json.loads(value)
    if not isinstance(raw, list) or len(raw) != dimensions:
        raise ValueError("invalid knowledge embedding vector")
    vector = tuple(float(item) for item in raw)
    if any(not math.isfinite(item) for item in vector):
        raise ValueError("invalid knowledge embedding vector")
    return vector


def cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right):
        raise ValueError("knowledge embedding dimensions do not match")
    return sum(a * b for a, b in zip(left, right, strict=True))


def reciprocal_rank_fusion(
    sparse: list[tuple[str, float]],
    dense: list[tuple[str, float]],
    *,
    rank_constant: int = 60,
) -> list[tuple[str, float, int | None, float | None, int | None, float | None]]:
    if rank_constant < 1:
        raise ValueError("RRF rank constant must be positive")
    combined: dict[str, _RrfState] = {}
    for rank, (chunk_id, score) in enumerate(sparse, start=1):
        combined[chunk_id] = _RrfState(
            score=1.0 / (rank_constant + rank),
            sparse_rank=rank,
            sparse_score=score,
        )
    for rank, (chunk_id, score) in enumerate(dense, start=1):
        state = combined.setdefault(chunk_id, _RrfState())
        state.score += 1.0 / (rank_constant + rank)
        state.dense_rank = rank
        state.dense_score = score
    ordered = sorted(combined.items(), key=lambda item: (-item[1].score, item[0]))
    return [
        (
            chunk_id,
            state.score,
            state.sparse_rank,
            state.sparse_score,
            state.dense_rank,
            state.dense_score,
        )
        for chunk_id, state in ordered
    ]


def citation_id(chunk: KnowledgeChunk) -> str:
    return _stable_id(
        "kcite",
        chunk.workspace_id,
        chunk.page_id,
        chunk.page_revision,
        chunk.source_revision,
        chunk.chunk_id,
    )


def assemble_retrieval_bundle(
    query: str,
    hits: tuple[KnowledgeSearchHit, ...],
    *,
    token_budget: int = 3_000,
) -> KnowledgeRetrievalBundle:
    """Select ranked evidence without allowing retrieval to overrun model context."""

    if token_budget < 256 or token_budget > 20_000:
        raise ValueError("knowledge token_budget must be between 256 and 20000")
    selected: list[KnowledgeEvidence] = []
    used_tokens = 0
    for hit in hits:
        remaining = token_budget - used_tokens
        if remaining <= 0:
            break
        chunk_tokens = max(1, hit.chunk.token_count)
        if chunk_tokens <= remaining:
            selected.append(
                KnowledgeEvidence(
                    hit=hit,
                    excerpt=hit.chunk.text,
                    token_count=chunk_tokens,
                    truncated=False,
                )
            )
            used_tokens += chunk_tokens
            continue
        if selected:
            break
        excerpt = _truncate_excerpt(hit.chunk.text, chunk_tokens, remaining)
        selected.append(
            KnowledgeEvidence(
                hit=hit,
                excerpt=excerpt,
                token_count=remaining,
                truncated=True,
            )
        )
        used_tokens += remaining
        break
    return KnowledgeRetrievalBundle(
        query=query.strip(),
        status="evidence_found" if selected else "no_evidence",
        evidence=tuple(selected),
        token_budget=token_budget,
        used_tokens=used_tokens,
        omitted_count=max(0, len(hits) - len(selected)),
    )


def _split_oversized_block(text: str) -> tuple[str, ...]:
    if len(text) <= _MAX_CHUNK_CHARS:
        return (text,)
    pieces: list[str] = []
    cursor = 0
    while cursor < len(text):
        end = min(len(text), cursor + _MAX_CHUNK_CHARS)
        if end < len(text):
            boundary = max(text.rfind("\n", cursor, end), text.rfind("。", cursor, end))
            if boundary > cursor + _MAX_CHUNK_CHARS // 2:
                end = boundary + 1
        part = text[cursor:end].strip()
        if part:
            pieces.append(part)
        if end >= len(text):
            break
        cursor = max(cursor + 1, end - _CHUNK_OVERLAP_CHARS)
    return tuple(pieces)


def _truncate_excerpt(text: str, source_tokens: int, token_budget: int) -> str:
    if not text or token_budget <= 0:
        return ""
    ratio = min(1.0, token_budget / max(1, source_tokens))
    limit = min(len(text), max(80, int(len(text) * ratio)))
    excerpt = text[:limit].rstrip()
    if limit < len(text):
        excerpt += "..."
    return excerpt


def _stable_id(prefix: str, *parts: str) -> str:
    payload = "\0".join(parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(payload).hexdigest()[:32]}"
