from __future__ import annotations

from core.knowledge.benchmark import KnowledgeBenchmarkQueryV2, KnowledgeRelevanceJudgment
from evals.book_learning_claims import (
    ClaimEvidenceGoldCase,
    ClaimEvidenceRequirement,
)
from evals.book_learning_query_rewrite import (
    QueryRewriteObservation,
    evaluate_query_rewrite_variants,
)


def test_query_rewrite_variants_measure_original_rewrite_and_rrf_union() -> None:
    query = KnowledgeBenchmarkQueryV2(
        query_id="q1",
        query="why",
        category="paraphrase",
        split="test",
        answerable=True,
        relevant=(
            KnowledgeRelevanceJudgment("book#A", 3),
            KnowledgeRelevanceJudgment("book#B", 2),
        ),
    )
    claim_gold = ClaimEvidenceGoldCase(
        query_id="q1",
        answerable=True,
        expected_decision="answer",
        claims=(
            ClaimEvidenceRequirement("c1", "first fact", ("book#A",), "any"),
            ClaimEvidenceRequirement("c2", "second fact", ("book#B",), "any"),
        ),
    )
    report = evaluate_query_rewrite_variants(
        (query,),
        (claim_gold,),
        (
            QueryRewriteObservation(
                query_id="q1",
                original_passage_ids=("book#A", "book#noise"),
                rewrite_passage_ids=("book#B", "book#other"),
            ),
        ),
        top_k=2,
    )

    assert report["variants"]["original_only"]["retrieval"]["recall_at_k"] == 0.5
    assert report["variants"]["rewrite_only"]["retrieval"]["recall_at_k"] == 0.5
    assert report["variants"]["original_plus_rewrite"]["retrieval"]["recall_at_k"] == 1.0
    assert (
        report["variants"]["original_plus_rewrite"]["claim_evidence"]["metrics"]
        ["first_pass_claim_evidence_coverage"]
        == 1.0
    )


def test_query_rewrite_rejects_duplicate_observation_ids() -> None:
    query = KnowledgeBenchmarkQueryV2(
        query_id="q1",
        query="why",
        category="paraphrase",
        split="test",
        answerable=True,
        relevant=(KnowledgeRelevanceJudgment("book#A", 3),),
    )
    gold = ClaimEvidenceGoldCase(
        query_id="q1",
        answerable=True,
        expected_decision="answer",
        claims=(ClaimEvidenceRequirement("c1", "fact", ("book#A",), "any"),),
    )

    try:
        evaluate_query_rewrite_variants(
            (query,),
            (gold,),
            (
                QueryRewriteObservation("q1", ("book#A",), ("book#A",)),
                QueryRewriteObservation("q1", ("book#A",), ("book#A",)),
            ),
            top_k=1,
        )
    except ValueError as exc:
        assert "unique" in str(exc)
    else:
        raise AssertionError("duplicate observations must be rejected")
