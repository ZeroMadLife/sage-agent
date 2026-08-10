"""Bounded retrieval sufficiency state transitions."""

from core.harness.retrieval_sufficiency import evaluate_retrieval_sufficiency


def _kwargs(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "query_fingerprint": "q-fp-1",
        "round_index": 1,
        "required_aspects": ("definition",),
        "covered_aspects": ("definition",),
        "citation_refs": ("cite-1",),
        "source_refs": ("book-1",),
    }
    values.update(overrides)
    return values


def test_complete_authorized_evidence_can_answer() -> None:
    assessment = evaluate_retrieval_sufficiency(**_kwargs(model_confidence=0.2))

    assert assessment.decision == "answer"
    assert assessment.sufficient is True
    assert assessment.stop_reason == "evidence_sufficient"
    assert assessment.model_confidence == 0.2


def test_missing_aspect_uses_one_bounded_retry() -> None:
    assessment = evaluate_retrieval_sufficiency(**_kwargs(covered_aspects=(), retry_available=True))

    assert assessment.decision == "retry"
    assert assessment.missing_aspects == ("definition",)
    assert assessment.stop_reason is None


def test_cross_source_gap_delegates_to_research_after_first_pass() -> None:
    assessment = evaluate_retrieval_sufficiency(
        **_kwargs(
            required_aspects=("definition", "comparison"),
            covered_aspects=("definition",),
            agentic_candidate=True,
        )
    )

    assert assessment.decision == "delegate_research"
    assert assessment.route_reason == "cross_source_or_multi_hop_gap"


def test_model_confidence_cannot_accept_citation_free_answer() -> None:
    assessment = evaluate_retrieval_sufficiency(**_kwargs(citation_refs=(), model_confidence=1.0))

    assert assessment.decision == "abstain"
    assert assessment.sufficient is False


def test_second_round_with_no_new_citation_abstains() -> None:
    assessment = evaluate_retrieval_sufficiency(
        **_kwargs(
            round_index=2,
            covered_aspects=(),
            previous_citation_refs=("cite-1",),
        )
    )

    assert assessment.decision == "abstain"
    assert assessment.stop_reason == "no_new_evidence"


def test_second_round_cannot_relabel_unchanged_evidence_as_sufficient() -> None:
    assessment = evaluate_retrieval_sufficiency(
        **_kwargs(
            round_index=2,
            previous_citation_refs=("cite-1",),
            model_confidence=1.0,
        )
    )

    assert assessment.decision == "abstain"
    assert assessment.sufficient is False
    assert assessment.stop_reason == "no_new_evidence"


def test_conflict_is_not_answerable_and_public_receipt_hides_aspects() -> None:
    assessment = evaluate_retrieval_sufficiency(**_kwargs(conflict_count=1))

    assert assessment.decision == "abstain"
    public = assessment.to_public_payload(run_id="run-1")
    assert public["conflict_count"] == 1
    assert "definition" not in repr(public)
    assert "cite-1" not in repr(public)


def test_empty_required_aspects_are_rejected_instead_of_vacuously_answered() -> None:
    try:
        evaluate_retrieval_sufficiency(**_kwargs(required_aspects=()))
    except ValueError as exc:
        assert "required aspect" in str(exc)
    else:
        raise AssertionError("empty required aspects must be rejected")
