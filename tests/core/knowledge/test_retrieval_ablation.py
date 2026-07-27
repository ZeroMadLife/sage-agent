from __future__ import annotations

from dataclasses import replace

import pytest

from core.knowledge.parsing import MarkdownParser, ParseRequest
from core.knowledge.retrieval import (
    KnowledgeAblationPolicy,
    KnowledgeSearchHit,
    chunk_document,
    embedding_text,
    index_text,
    postprocess_search_hits,
)


def _document(markdown: str):
    return MarkdownParser().parse(
        ParseRequest(
            source_id="src_test",
            relative_path="guide.md",
            source_revision="sha256:test",
            media_type="text/markdown",
            payload=markdown.encode("utf-8"),
        )
    )


def _chunks(markdown: str, policy: KnowledgeAblationPolicy):
    return chunk_document(
        _document(markdown),
        workspace_id="knowledge-local",
        page_id="page_test",
        page_revision="krev_test",
        page_path="wiki/sources/guide.md",
        source_id="src_test",
        source_revision="sha256:test",
        source_kind="official",
        source_relative_path="langgraph/guide.md",
        proposal_id="kprop_test",
        artifact_id="part_test",
        title="Sage Guide",
        visibility="private",
        active=True,
        ablation_policy=policy,
    )


def _hit(chunk, *, rank: int, score: float = 0.03) -> KnowledgeSearchHit:
    return KnowledgeSearchHit(
        chunk=chunk,
        citation_id=f"cite-{rank}",
        rank=rank,
        rrf_score=score,
        sparse_rank=rank,
        sparse_score=4.0 - rank,
        dense_rank=rank,
        dense_score=0.9 - rank / 10,
    )


def test_ablation_policy_allows_exactly_one_named_strategy() -> None:
    assert KnowledgeAblationPolicy().strategy == "baseline"
    assert KnowledgeAblationPolicy(strategy="cross_encoder").rerank_top_n == 20

    with pytest.raises(ValueError, match="unsupported Knowledge ablation strategy"):
        KnowledgeAblationPolicy(strategy="contextual_chunk+cross_encoder")  # type: ignore[arg-type]


def test_contextual_chunk_adds_source_context_only_to_index_inputs() -> None:
    baseline = _chunks(
        "# Sage\n\n## Recovery\n\nGate remains fixed during bounded recovery.\n",
        KnowledgeAblationPolicy(),
    )[0]
    policy = KnowledgeAblationPolicy(strategy="contextual_chunk")

    contextual_sparse = index_text(baseline, ablation_policy=policy)
    contextual_dense = embedding_text(baseline, ablation_policy=policy)

    assert contextual_sparse != index_text(baseline)
    assert "langgraph" in contextual_sparse
    assert "Source: langgraph/guide.md" in contextual_dense
    assert "Title: Sage Guide" in contextual_dense
    assert "Section: Sage / Recovery" in contextual_dense
    assert baseline.text == "Gate remains fixed during bounded recovery."


def test_parent_child_retrieves_children_but_returns_one_citable_parent() -> None:
    parent = (
        "第一段说明小块用于精确检索。第二段说明父块保留完整回答上下文。"
        "第三段说明多个子块不能重复占用 Top-K。第四段说明 citation 必须解析到父块正文。"
    )
    policy = KnowledgeAblationPolicy(
        strategy="parent_child",
        parent_child_max_chars=64,
        parent_child_overlap_chars=8,
    )

    children = _chunks(f"# Sage\n\n## Parent Child\n\n{parent}\n", policy)

    assert len(children) > 1
    assert {chunk.text for chunk in children} == {parent}
    assert all(chunk.retrieval_text for chunk in children)
    assert all(len(chunk.retrieval_text or "") <= 64 for chunk in children)
    assert len({chunk.chunk_id for chunk in children}) == len(children)
    assert len({chunk.block_id for chunk in children}) == 1

    unrelated = replace(children[0], block_id="other", chunk_id="other")
    processed = postprocess_search_hits(
        "父块上下文",
        (_hit(children[0], rank=1), _hit(children[1], rank=2), _hit(unrelated, rank=3)),
        policy=policy,
    )

    assert [hit.chunk.chunk_id for hit in processed] == [children[0].chunk_id, "other"]
    assert [hit.rank for hit in processed] == [1, 2]
    assert all(hit.chunk.text == parent for hit in processed)


class _SemanticProvider:
    model_id = "test.semantic"
    model_revision = "1"
    dimensions = 2
    supports_semantic_recall = True

    def prepare(self, _texts: tuple[str, ...]) -> None:
        return None

    def embed(self, text: str) -> tuple[float, ...]:
        return (1.0, 0.0) if "检索" in text else (0.0, 1.0)


def test_semantic_boundary_uses_embedding_breakpoint_only_for_oversized_blocks() -> None:
    text = (
        "检索阶段需要召回候选。检索阶段还要记录排序。"
        "部署阶段需要不可变镜像。部署阶段还要验证回滚。"
    )
    policy = KnowledgeAblationPolicy(
        strategy="semantic_boundary",
        semantic_min_chars=40,
        semantic_min_chunk_chars=20,
        semantic_breakpoint_percentile=50.0,
    )
    chunks = chunk_document(
        _document(f"# Sage\n\n## Boundary\n\n{text}\n"),
        workspace_id="knowledge-local",
        page_id="page_test",
        page_revision="krev_test",
        page_path="wiki/sources/guide.md",
        source_id="src_test",
        source_revision="sha256:test",
        source_kind="official",
        source_relative_path="guide.md",
        proposal_id="kprop_test",
        artifact_id="part_test",
        title="Sage Guide",
        visibility="private",
        active=True,
        ablation_policy=policy,
        semantic_provider=_SemanticProvider(),
    )

    assert len(chunks) == 2
    assert "检索阶段还要记录排序" in chunks[0].text
    assert chunks[1].text.startswith("部署阶段")

    baseline_sized = replace(policy, semantic_min_chars=len(text) + 1)
    assert len(
        chunk_document(
            _document(f"# Sage\n\n## Boundary\n\n{text}\n"),
            workspace_id="knowledge-local",
            page_id="page_test",
            page_revision="krev_test",
            page_path="wiki/sources/guide.md",
            source_id="src_test",
            source_revision="sha256:test",
            source_kind="official",
            source_relative_path="guide.md",
            proposal_id="kprop_test",
            artifact_id="part_test",
            title="Sage Guide",
            visibility="private",
            active=True,
            ablation_policy=baseline_sized,
            semantic_provider=_SemanticProvider(),
        )
    ) == 1


class _Reranker:
    model_id = "test.cross-encoder"
    model_revision = "1"
    estimated_cost_usd = 0.0

    def rerank(self, query: str, documents: tuple[str, ...]) -> tuple[float, ...]:
        assert query == "fixed gate"
        return tuple(1.0 if "best" in document else 0.1 for document in documents)


def test_cross_encoder_reranks_only_bounded_rrf_candidates_and_preserves_tail() -> None:
    chunks = list(
        _chunks(
            "# Sage\n\n## A\n\nfirst\n\n## B\n\nbest\n\n## C\n\ntail\n",
            KnowledgeAblationPolicy(),
        )
    )
    hits = tuple(_hit(chunk, rank=index + 1) for index, chunk in enumerate(chunks))
    policy = KnowledgeAblationPolicy(strategy="cross_encoder", rerank_top_n=2)

    processed = postprocess_search_hits(
        "fixed gate",
        hits,
        policy=policy,
        reranker=_Reranker(),
    )

    assert [hit.chunk.text for hit in processed] == ["best", "first", "tail"]
    assert [hit.rank for hit in processed] == [1, 2, 3]
    assert [hit.rerank_score for hit in processed] == [1.0, 0.1, None]


def test_cross_encoder_strategy_requires_a_reranker() -> None:
    chunk = _chunks("# Sage\n\n## A\n\ntext\n", KnowledgeAblationPolicy())[0]

    with pytest.raises(ValueError, match="cross-encoder reranker is required"):
        postprocess_search_hits(
            "query",
            (_hit(chunk, rank=1),),
            policy=KnowledgeAblationPolicy(strategy="cross_encoder"),
        )
