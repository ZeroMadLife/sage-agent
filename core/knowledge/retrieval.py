"""Revision-aware chunking and deterministic hybrid-retrieval primitives."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from itertools import pairwise
from typing import Literal, Protocol

from core.knowledge.parsing import BlockKind, ParsedDocument
from core.knowledge.recovery import (
    KnowledgeNoEvidenceReason,
    KnowledgeRecoveryAttempt,
    KnowledgeRecoveryStatus,
)

_LATIN_TOKEN = re.compile(r"[a-z0-9_]+", re.IGNORECASE)
_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_MAX_CHUNK_CHARS = 4_000
_CHUNK_OVERLAP_CHARS = 160
_MAX_CHUNKS_PER_REVISION = 2_000
_MAX_QUERY_TERMS = 64

KnowledgeRetrievalMode = Literal["sparse", "dense", "hybrid"]
KnowledgeAblationStrategy = Literal[
    "baseline",
    "contextual_chunk",
    "parent_child",
    "semantic_boundary",
    "cross_encoder",
]


@dataclass(frozen=True, slots=True)
class KnowledgeAblationPolicy:
    """One mutually exclusive PR-6 retrieval experiment."""

    strategy: KnowledgeAblationStrategy = "baseline"
    parent_child_max_chars: int = 180
    parent_child_overlap_chars: int = 24
    semantic_min_chars: int = _MAX_CHUNK_CHARS
    semantic_min_chunk_chars: int = 800
    semantic_breakpoint_percentile: float = 95.0
    rerank_top_n: int = 20

    def __post_init__(self) -> None:
        if self.strategy not in {
            "baseline",
            "contextual_chunk",
            "parent_child",
            "semantic_boundary",
            "cross_encoder",
        }:
            raise ValueError("unsupported Knowledge ablation strategy")
        if not 64 <= self.parent_child_max_chars <= 2_000:
            raise ValueError("parent-child max chars must be between 64 and 2000")
        if not 0 <= self.parent_child_overlap_chars < self.parent_child_max_chars // 2:
            raise ValueError("parent-child overlap must be less than half the child size")
        if not 40 <= self.semantic_min_chars <= 20_000:
            raise ValueError("semantic minimum chars must be between 40 and 20000")
        if not 20 <= self.semantic_min_chunk_chars <= self.semantic_min_chars:
            raise ValueError("semantic minimum chunk chars are invalid")
        if not 0.0 <= self.semantic_breakpoint_percentile <= 100.0:
            raise ValueError("semantic breakpoint percentile must be between zero and 100")
        if not 2 <= self.rerank_top_n <= 50:
            raise ValueError("cross-encoder rerank top-n must be between 2 and 50")


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
    block_kind: BlockKind = "paragraph"
    bbox: tuple[float, float, float, float] | None = None
    media_ref: str | None = None
    confidence: float = 1.0
    parser_id: str = ""
    parser_version: str = ""
    retrieval_text: str | None = None


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
    retrieval_route: Literal["sparse", "dense", "hybrid", "graph"] = "hybrid"
    graph_edge_id: str | None = None
    graph_evidence_citation_id: str | None = None
    graph_seed_page_id: str | None = None
    graph_direction: Literal["outbound", "inbound"] | None = None
    graph_score: float | None = None
    rerank_score: float | None = None


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
    recovery_status: KnowledgeRecoveryStatus = "disabled"
    recovery_attempts: tuple[KnowledgeRecoveryAttempt, ...] = ()
    no_evidence_reason: KnowledgeNoEvidenceReason | None = None


@dataclass(frozen=True, slots=True)
class KnowledgeIndexSummary:
    backend: str
    embedding_model: str
    embedding_revision: str
    corpus_revision: str
    relevance_policy_id: str | None
    abstention_enabled: bool
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
        """Compatibility entrypoint for symmetric or legacy providers."""


def embed_document_text(
    provider: DenseEmbeddingProvider,
    text: str,
) -> tuple[float, ...]:
    """Embed index content with a role-aware provider when available."""

    embed_document = getattr(provider, "embed_document", None)
    if callable(embed_document):
        return tuple(float(value) for value in embed_document(text))
    return tuple(float(value) for value in provider.embed(text))


def embed_query_text(
    provider: DenseEmbeddingProvider,
    text: str,
) -> tuple[float, ...]:
    """Embed a retrieval query while preserving legacy provider compatibility."""

    embed_query = getattr(provider, "embed_query", None)
    if callable(embed_query):
        return tuple(float(value) for value in embed_query(text))
    return tuple(float(value) for value in provider.embed(text))


def prepare_document_embeddings(
    provider: DenseEmbeddingProvider,
    texts: tuple[str, ...],
) -> None:
    """Batch document embeddings without conflating them with query vectors."""

    prepare_documents = getattr(provider, "prepare_documents", None)
    if callable(prepare_documents):
        prepare_documents(texts)
        return
    prepare = getattr(provider, "prepare", None)
    if callable(prepare):
        prepare(texts)


def prepare_query_embeddings(
    provider: DenseEmbeddingProvider,
    texts: tuple[str, ...],
) -> None:
    """Batch query embeddings for benchmarks that explicitly opt into precaching."""

    prepare_queries = getattr(provider, "prepare_queries", None)
    if callable(prepare_queries):
        prepare_queries(texts)
        return
    prepare = getattr(provider, "prepare", None)
    if callable(prepare):
        prepare(texts)


class KnowledgeReranker(Protocol):
    model_id: str
    model_revision: str
    estimated_cost_usd: float | None

    def rerank(self, query: str, documents: tuple[str, ...]) -> tuple[float, ...]:
        """Score a bounded candidate list in its original order."""


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

    def embed_document(self, text: str) -> tuple[float, ...]:
        return self.embed(text)

    def embed_query(self, text: str) -> tuple[float, ...]:
        return self.embed(text)


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
    ablation_policy: KnowledgeAblationPolicy | None = None,
    semantic_provider: DenseEmbeddingProvider | None = None,
) -> tuple[KnowledgeChunk, ...]:
    """Preserve parser blocks first and split only oversized semantic blocks."""

    policy = ablation_policy or KnowledgeAblationPolicy()
    chunks: list[KnowledgeChunk] = []
    for block in document.blocks:
        if block.kind in {"frontmatter", "heading"} or not block.text.strip():
            continue
        parent_text = block.text.strip()
        parts = _ablation_parts(
            parent_text,
            policy=policy,
            semantic_provider=semantic_provider,
        )
        for part_index, (text, retrieval_value) in enumerate(parts):
            content_hash = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
            retrieval_hash = hashlib.sha256((retrieval_value or text).encode("utf-8")).hexdigest()
            ordinal = len(chunks)
            identity_parts = [
                "kchunk",
                page_revision,
                block.block_id,
                str(part_index),
                content_hash,
            ]
            if retrieval_value is not None:
                identity_parts.append(retrieval_hash)
            if block.bbox is not None or block.media_ref is not None:
                identity_parts.extend(
                    [
                        block.kind,
                        str(block.page or ""),
                        json.dumps(block.bbox, separators=(",", ":")),
                        block.media_ref or "",
                        format(block.confidence, ".12g"),
                        document.provenance.parser_id,
                        document.provenance.parser_version,
                    ]
                )
            chunk_id = _stable_id(*identity_parts)
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
                    block_kind=block.kind,
                    bbox=block.bbox,
                    media_ref=block.media_ref,
                    confidence=block.confidence,
                    parser_id=document.provenance.parser_id,
                    parser_version=document.provenance.parser_version,
                    retrieval_text=retrieval_value,
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
            parser_id=document.provenance.parser_id,
            parser_version=document.provenance.parser_version,
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


def contextual_retrieval_text(chunk: KnowledgeChunk) -> str:
    section = " / ".join(chunk.heading_path) or chunk.title
    return "\n".join(
        (
            f"Source: {chunk.source_relative_path}",
            f"Title: {chunk.title}",
            f"Section: {section}",
            "Content:",
            chunk.retrieval_text or chunk.text,
        )
    ).strip()


def index_text(
    chunk: KnowledgeChunk,
    *,
    ablation_policy: KnowledgeAblationPolicy | None = None,
) -> str:
    policy = ablation_policy or KnowledgeAblationPolicy()
    if policy.strategy == "contextual_chunk":
        return " ".join(lexical_terms(contextual_retrieval_text(chunk)))
    values = (chunk.title, *chunk.heading_path, chunk.retrieval_text or chunk.text)
    return " ".join(lexical_terms("\n".join(values)))


def embedding_text(
    chunk: KnowledgeChunk,
    *,
    ablation_policy: KnowledgeAblationPolicy | None = None,
) -> str:
    policy = ablation_policy or KnowledgeAblationPolicy()
    if policy.strategy == "contextual_chunk":
        return contextual_retrieval_text(chunk)
    return "\n".join(
        (chunk.title, " / ".join(chunk.heading_path), chunk.retrieval_text or chunk.text)
    ).strip()


def postprocess_search_hits(
    query: str,
    hits: tuple[KnowledgeSearchHit, ...],
    *,
    policy: KnowledgeAblationPolicy,
    reranker: KnowledgeReranker | None = None,
) -> tuple[KnowledgeSearchHit, ...]:
    """Apply exactly one bounded experiment after base retrieval."""

    if policy.strategy == "parent_child":
        selected: list[KnowledgeSearchHit] = []
        seen_parents: set[tuple[str, str]] = set()
        for hit in hits:
            parent = (hit.chunk.page_revision, hit.chunk.block_id)
            if parent in seen_parents:
                continue
            seen_parents.add(parent)
            selected.append(replace(hit, rank=len(selected) + 1))
        return tuple(selected)
    if policy.strategy != "cross_encoder":
        if reranker is not None:
            raise ValueError("reranker is only valid for the cross_encoder strategy")
        return hits
    if reranker is None:
        raise ValueError("cross-encoder reranker is required")
    bounded = hits[: policy.rerank_top_n]
    if not bounded:
        return hits
    scores = tuple(
        float(value)
        for value in reranker.rerank(
            query,
            tuple(hit.chunk.text for hit in bounded),
        )
    )
    if len(scores) != len(bounded) or any(not math.isfinite(value) for value in scores):
        raise ValueError("cross-encoder returned invalid scores")
    rescored = sorted(
        zip(bounded, scores, strict=True),
        key=lambda item: (-item[1], item[0].rank, item[0].chunk.chunk_id),
    )
    ordered = [
        replace(hit, rank=index, rerank_score=score)
        for index, (hit, score) in enumerate(rescored, start=1)
    ]
    ordered.extend(
        replace(hit, rank=index)
        for index, hit in enumerate(hits[policy.rerank_top_n :], start=len(ordered) + 1)
    )
    return tuple(ordered)


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
    tie_breakers: Mapping[str, str] | None = None,
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
    stable = tie_breakers or {}
    ordered = sorted(
        combined.items(),
        key=lambda item: (-item[1].score, stable.get(item[0], item[0]), item[0]),
    )
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
    recovery_status: KnowledgeRecoveryStatus = "disabled",
    recovery_attempts: tuple[KnowledgeRecoveryAttempt, ...] = (),
    no_evidence_reason: KnowledgeNoEvidenceReason | None = None,
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
        recovery_status=recovery_status,
        recovery_attempts=recovery_attempts,
        no_evidence_reason=no_evidence_reason,
    )


def _ablation_parts(
    text: str,
    *,
    policy: KnowledgeAblationPolicy,
    semantic_provider: DenseEmbeddingProvider | None,
) -> tuple[tuple[str, str | None], ...]:
    if policy.strategy == "parent_child":
        return tuple(
            (text, child)
            for child in _split_bounded_block(
                text,
                max_chars=policy.parent_child_max_chars,
                overlap_chars=policy.parent_child_overlap_chars,
            )
        )
    if policy.strategy == "semantic_boundary" and len(text) > policy.semantic_min_chars:
        if semantic_provider is None or not semantic_provider.supports_semantic_recall:
            raise ValueError("semantic boundary chunking requires a semantic embedding provider")
        return tuple(
            (part, None)
            for part in _split_semantic_block(
                text,
                provider=semantic_provider,
                minimum_chunk_chars=policy.semantic_min_chunk_chars,
                breakpoint_percentile=policy.semantic_breakpoint_percentile,
            )
        )
    return tuple((part, None) for part in _split_oversized_block(text))


def _split_oversized_block(text: str) -> tuple[str, ...]:
    return _split_bounded_block(
        text,
        max_chars=_MAX_CHUNK_CHARS,
        overlap_chars=_CHUNK_OVERLAP_CHARS,
    )


def _split_bounded_block(
    text: str,
    *,
    max_chars: int,
    overlap_chars: int,
) -> tuple[str, ...]:
    if len(text) <= max_chars:
        return (text,)
    pieces: list[str] = []
    cursor = 0
    while cursor < len(text):
        end = min(len(text), cursor + max_chars)
        if end < len(text):
            boundary = max(
                text.rfind("\n", cursor, end),
                text.rfind("。", cursor, end),
                text.rfind("！", cursor, end),
                text.rfind("？", cursor, end),
                text.rfind(". ", cursor, end),
            )
            if boundary > cursor + max_chars // 2:
                end = boundary + 1
        part = text[cursor:end].strip()
        if part:
            pieces.append(part)
        if end >= len(text):
            break
        cursor = max(cursor + 1, end - overlap_chars)
    return tuple(pieces)


def _split_semantic_block(
    text: str,
    *,
    provider: DenseEmbeddingProvider,
    minimum_chunk_chars: int,
    breakpoint_percentile: float,
) -> tuple[str, ...]:
    sentences = _sentence_units(text)
    if len(sentences) < 3:
        return _split_oversized_block(text)
    prepare = getattr(provider, "prepare", None)
    if callable(prepare):
        prepare(sentences)
    vectors = tuple(embed_document_text(provider, sentence) for sentence in sentences)
    if any(len(vector) != provider.dimensions for vector in vectors):
        raise ValueError("semantic boundary embedding dimensions changed")
    distances = tuple(1.0 - cosine_similarity(left, right) for left, right in pairwise(vectors))
    threshold = _percentile(distances, breakpoint_percentile)
    boundaries = {index + 1 for index, distance in enumerate(distances) if distance > threshold}
    pieces: list[str] = []
    current: list[str] = []
    for index, sentence in enumerate(sentences, start=1):
        current.append(sentence)
        candidate = " ".join(current).strip()
        if index in boundaries and len(candidate) >= minimum_chunk_chars:
            pieces.append(candidate)
            current = []
    if current:
        tail = " ".join(current).strip()
        if pieces and len(tail) < minimum_chunk_chars:
            pieces[-1] = f"{pieces[-1]} {tail}".strip()
        elif tail:
            pieces.append(tail)
    bounded = tuple(
        child
        for piece in pieces
        for child in _split_bounded_block(
            piece,
            max_chars=_MAX_CHUNK_CHARS,
            overlap_chars=_CHUNK_OVERLAP_CHARS,
        )
    )
    return bounded or _split_oversized_block(text)


def _sentence_units(text: str) -> tuple[str, ...]:
    units: list[str] = []
    start = 0
    for index, character in enumerate(text):
        if character not in {"。", "！", "？", "!", "?", "\n"} and not (
            character == "." and (index + 1 == len(text) or text[index + 1].isspace())
        ):
            continue
        value = text[start : index + 1].strip()
        if value:
            units.append(value)
        start = index + 1
    tail = text[start:].strip()
    if tail:
        units.append(tail)
    return tuple(units)


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * percentile / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


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
