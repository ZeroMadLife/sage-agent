"""Claim-aware evidence coverage metrics for long-book RAG receipts.

This module evaluates whether retrieval found the passages required for each
atomic gold claim. It does not infer semantic support and it does not activate
the online sufficiency gate; answer faithfulness remains a separate judge task.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

CoverageMode = Literal["any", "all"]
FinalDecision = Literal["answer", "abstain"]
EvaluationStatus = Literal["completed", "provider_error"]


class _ClaimCaseReceipt(TypedDict):
    query_id: str
    answerable: bool
    expected_decision: FinalDecision
    final_decision: FinalDecision | None
    evaluation_status: EvaluationStatus
    required_claim_count: int
    first_round_covered_claim_ids: list[str]
    final_covered_claim_ids: list[str]
    missing_claim_ids: list[str]
    first_round_claim_coverage: float | None
    final_claim_coverage: float | None
    first_round_complete: bool
    evidence_complete: bool
    claim_receipts: list[dict[str, object]]


@dataclass(frozen=True, slots=True)
class ClaimEvidenceRequirement:
    """One atomic fact and the exact passages that can establish it."""

    claim_id: str
    statement: str
    passage_ids: tuple[str, ...]
    coverage_mode: CoverageMode

    def __post_init__(self) -> None:
        if not self.claim_id.strip() or not self.statement.strip():
            raise ValueError("claim id and statement are required")
        if self.coverage_mode not in {"any", "all"}:
            raise ValueError("claim coverage mode must be any or all")
        if not self.passage_ids or any(not item.strip() for item in self.passage_ids):
            raise ValueError("claim passage ids must be non-empty")
        if len(self.passage_ids) != len(set(self.passage_ids)):
            raise ValueError("claim passage ids must be unique")


@dataclass(frozen=True, slots=True)
class ClaimEvidenceGoldCase:
    """Versionable claim requirements for one benchmark query."""

    query_id: str
    answerable: bool
    expected_decision: FinalDecision
    claims: tuple[ClaimEvidenceRequirement, ...]

    def __post_init__(self) -> None:
        if not self.query_id.strip():
            raise ValueError("claim gold query id is required")
        if self.expected_decision not in {"answer", "abstain"}:
            raise ValueError("claim gold expected decision is invalid")
        if self.answerable and self.expected_decision != "answer":
            raise ValueError("answerable claim gold must expect answer")
        if not self.answerable and self.expected_decision != "abstain":
            raise ValueError("unanswerable claim gold must expect abstain")
        if self.answerable and not self.claims:
            raise ValueError("answerable claim gold requires at least one claim")
        if not self.answerable and self.claims:
            raise ValueError("unanswerable claim gold cannot bind positive claims")
        claim_ids = [claim.claim_id for claim in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("claim ids must be unique within a query")


@dataclass(frozen=True, slots=True)
class ClaimEvidenceEvalCase:
    """Observed first/final passage IDs and the optional gate decision."""

    query_id: str
    first_round_passage_ids: tuple[str, ...]
    final_passage_ids: tuple[str, ...]
    final_decision: FinalDecision | None
    evaluation_status: EvaluationStatus = "completed"

    def __post_init__(self) -> None:
        if not self.query_id.strip():
            raise ValueError("claim evaluation query id is required")
        if self.final_decision not in {"answer", "abstain", None}:
            raise ValueError("claim evaluation decision is invalid")
        if self.evaluation_status not in {"completed", "provider_error"}:
            raise ValueError("claim evaluation status is invalid")
        if self.evaluation_status == "provider_error" and self.final_decision is not None:
            raise ValueError("provider errors cannot carry a quality decision")


def missing_claim_brief(
    gold_case: ClaimEvidenceGoldCase,
    passage_ids: Iterable[str],
) -> tuple[dict[str, str], ...]:
    """Return the atomic facts not covered by the observed passages.

    This helper is intentionally an offline evaluation seam. It exposes claim
    IDs and statements to a bounded planner so recovery can be measured against
    frozen gold; passage IDs stay private to the evaluator and are never sent
    as rewrite instructions.
    """

    if not gold_case.answerable:
        return ()
    observed = {str(value).strip() for value in passage_ids if str(value).strip()}
    missing: list[dict[str, str]] = []
    for claim in gold_case.claims:
        required = set(claim.passage_ids)
        matched = required.intersection(observed)
        if not _claim_complete(claim.coverage_mode, required, matched):
            missing.append({"claim_id": claim.claim_id, "statement": claim.statement})
    return tuple(missing)


def load_claim_evidence_gold(path: Path) -> tuple[ClaimEvidenceGoldCase, ...]:
    """Load a strict JSONL claim dataset with line-addressable failures."""

    cases: list[ClaimEvidenceGoldCase] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw: Any = json.loads(line)
            cases.append(_gold_case(raw))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid claim evidence gold at line {line_number}") from exc
    query_ids = [case.query_id for case in cases]
    if not cases or len(query_ids) != len(set(query_ids)):
        raise ValueError("claim evidence gold requires unique query ids")
    return tuple(cases)


def claim_eval_cases_from_report(report: Mapping[str, Any]) -> tuple[ClaimEvidenceEvalCase, ...]:
    """Project Sage retrieval or generation receipts onto the claim evaluator."""

    raw_cases = report.get("cases")
    if not isinstance(raw_cases, list | tuple) or not raw_cases:
        raise ValueError("claim evaluation report requires cases")
    generation = report.get("stage") == "llmwiki_generation" or any(
        isinstance(item, Mapping) and "first_pass_evidence" in item for item in raw_cases
    )
    cases: list[ClaimEvidenceEvalCase] = []
    for raw in raw_cases:
        if not isinstance(raw, Mapping) or not str(raw.get("query_id", "")).strip():
            raise ValueError("claim evaluation report case is invalid")
        query_id = str(raw["query_id"])
        if generation:
            provider_error = isinstance(raw.get("failure"), Mapping)
            cases.append(
                ClaimEvidenceEvalCase(
                    query_id=query_id,
                    first_round_passage_ids=_evidence_passage_ids(raw.get("first_pass_evidence")),
                    final_passage_ids=_evidence_passage_ids(raw.get("final_evidence")),
                    final_decision=(
                        None if provider_error else _decision(raw.get("accepted_decision"))
                    ),
                    evaluation_status="provider_error" if provider_error else "completed",
                )
            )
        else:
            passages = _string_ids(raw.get("retrieved_documents"))
            cases.append(
                ClaimEvidenceEvalCase(
                    query_id=query_id,
                    first_round_passage_ids=passages,
                    final_passage_ids=passages,
                    final_decision=None,
                )
            )
    query_ids = [case.query_id for case in cases]
    if len(query_ids) != len(set(query_ids)):
        raise ValueError("claim evaluation report query ids must be unique")
    return tuple(cases)


def evaluate_claim_evidence(
    gold_cases: Iterable[ClaimEvidenceGoldCase],
    eval_cases: Iterable[ClaimEvidenceEvalCase],
) -> dict[str, object]:
    """Measure claim coverage, recovery gain, and offline gate readiness."""

    gold = tuple(gold_cases)
    observed = tuple(eval_cases)
    if not gold or not observed:
        raise ValueError("claim evidence evaluation requires gold and observed cases")
    gold_by_id = _unique_by_id(gold, "claim gold")
    observed_by_id = _unique_by_id(observed, "claim observations")
    if set(gold_by_id) != set(observed_by_id):
        raise ValueError("claim evidence gold and observations must contain the same query ids")

    receipts = [_evaluate_case(gold_by_id[case.query_id], case) for case in observed]
    completed = [item for item in receipts if item["evaluation_status"] == "completed"]
    answerable = [item for item in completed if item["answerable"]]
    unanswerable = [item for item in completed if not item["answerable"]]
    decision_cases = [item for item in completed if item["final_decision"] is not None]
    accepted = [item for item in decision_cases if item["final_decision"] == "answer"]
    complete = [item for item in answerable if item["evidence_complete"]]
    recoverable = [item for item in answerable if not item["first_round_complete"]]

    readiness_true_positive = sum(bool(item["evidence_complete"]) for item in accepted)
    metrics: dict[str, object] = {
        "case_count": len(receipts),
        "evaluated_case_count": len(completed),
        "provider_failure_count": len(receipts) - len(completed),
        "answerable_case_count": len(answerable),
        "unanswerable_case_count": len(unanswerable),
        "required_claim_count": sum(item["required_claim_count"] for item in answerable),
        "first_pass_claim_evidence_coverage": _optional_mean(
            cast(float, item["first_round_claim_coverage"]) for item in answerable
        ),
        "claim_evidence_coverage": _optional_mean(
            cast(float, item["final_claim_coverage"]) for item in answerable
        ),
        "bundle_completeness_rate": _optional_rate(
            bool(item["evidence_complete"]) for item in answerable
        ),
        "claim_recovery_gain": _optional_mean(
            cast(float, item["final_claim_coverage"])
            - cast(float, item["first_round_claim_coverage"])
            for item in answerable
        ),
        "recovery_resolution_rate": _optional_rate(
            bool(item["evidence_complete"]) for item in recoverable
        ),
        "answer_readiness_precision": (
            round(readiness_true_positive / len(accepted), 4)
            if accepted
            else (0.0 if decision_cases else None)
        ),
        "answer_readiness_recall": (
            (
                round(
                    sum(
                        item["final_decision"] == "answer" and item["evidence_complete"]
                        for item in complete
                    )
                    / len(complete),
                    4,
                )
                if complete
                else 0.0
            )
            if decision_cases
            else None
        ),
        "insufficient_acceptance_rate": (
            round(
                sum(not bool(item["evidence_complete"]) for item in accepted) / len(accepted),
                4,
            )
            if accepted
            else (0.0 if decision_cases else None)
        ),
        "false_acceptance_rate": _optional_rate(
            item["final_decision"] == "answer" for item in unanswerable
        )
        if decision_cases
        else None,
        "correct_abstention_rate": _optional_rate(
            item["final_decision"] == "abstain" for item in unanswerable
        )
        if decision_cases
        else None,
    }
    return {
        "schema_version": 1,
        "stage": "claim_evidence_sufficiency",
        "protocol": {
            "coverage_unit": "required_claim",
            "passage_matching": "exact_id",
            "answerable_requires_all_claims": True,
            "unanswerable_never_becomes_complete_from_similar_evidence": True,
            "online_gate_activated": False,
            "chain_of_thought_required": False,
        },
        "metrics": metrics,
        "cases": receipts,
    }


def _evaluate_case(
    gold: ClaimEvidenceGoldCase, observed: ClaimEvidenceEvalCase
) -> _ClaimCaseReceipt:
    if observed.evaluation_status == "provider_error":
        return {
            "query_id": gold.query_id,
            "answerable": gold.answerable,
            "expected_decision": gold.expected_decision,
            "final_decision": None,
            "evaluation_status": observed.evaluation_status,
            "required_claim_count": len(gold.claims),
            "first_round_covered_claim_ids": [],
            "final_covered_claim_ids": [],
            "missing_claim_ids": [claim.claim_id for claim in gold.claims],
            "first_round_claim_coverage": None,
            "final_claim_coverage": None,
            "first_round_complete": False,
            "evidence_complete": False,
            "claim_receipts": [],
        }

    first_passages = set(observed.first_round_passage_ids)
    final_passages = set(observed.final_passage_ids)
    claim_receipts: list[dict[str, object]] = []
    first_covered: list[str] = []
    final_covered: list[str] = []
    for claim in gold.claims:
        required = set(claim.passage_ids)
        first_matched = required.intersection(first_passages)
        final_matched = required.intersection(final_passages)
        first_complete = _claim_complete(claim.coverage_mode, required, first_matched)
        final_complete = _claim_complete(claim.coverage_mode, required, final_matched)
        if first_complete:
            first_covered.append(claim.claim_id)
        if final_complete:
            final_covered.append(claim.claim_id)
        claim_receipts.append(
            {
                "claim_id": claim.claim_id,
                "coverage_mode": claim.coverage_mode,
                "required_passage_count": len(required),
                "first_round_matched_passage_ids": sorted(first_matched),
                "final_matched_passage_ids": sorted(final_matched),
                "first_round_covered": first_complete,
                "final_covered": final_complete,
            }
        )

    required_count = len(gold.claims)
    first_coverage = len(first_covered) / required_count if required_count else 0.0
    final_coverage = len(final_covered) / required_count if required_count else 0.0
    first_complete = bool(
        gold.answerable and required_count and len(first_covered) == required_count
    )
    evidence_complete = bool(
        gold.answerable and required_count and len(final_covered) == required_count
    )
    return {
        "query_id": gold.query_id,
        "answerable": gold.answerable,
        "expected_decision": gold.expected_decision,
        "final_decision": observed.final_decision,
        "evaluation_status": observed.evaluation_status,
        "required_claim_count": required_count,
        "first_round_covered_claim_ids": first_covered,
        "final_covered_claim_ids": final_covered,
        "missing_claim_ids": [
            claim.claim_id for claim in gold.claims if claim.claim_id not in final_covered
        ],
        "first_round_claim_coverage": round(first_coverage, 4),
        "final_claim_coverage": round(final_coverage, 4),
        "first_round_complete": first_complete,
        "evidence_complete": evidence_complete,
        "claim_receipts": claim_receipts,
    }


def _gold_case(raw: Any) -> ClaimEvidenceGoldCase:
    expected_fields = {"query_id", "answerable", "expected_decision", "claims"}
    if not isinstance(raw, dict) or set(raw) != expected_fields:
        raise ValueError("claim gold fields do not match the contract")
    if (
        not isinstance(raw["query_id"], str)
        or not isinstance(raw["answerable"], bool)
        or not isinstance(raw["expected_decision"], str)
        or not isinstance(raw["claims"], list)
    ):
        raise TypeError("claim gold fields have invalid types")
    claims: list[ClaimEvidenceRequirement] = []
    for item in raw["claims"]:
        expected_claim_fields = {"claim_id", "statement", "passage_ids", "coverage_mode"}
        if not isinstance(item, dict) or set(item) != expected_claim_fields:
            raise ValueError("claim requirement fields do not match the contract")
        if (
            not isinstance(item["claim_id"], str)
            or not isinstance(item["statement"], str)
            or not isinstance(item["passage_ids"], list)
            or any(not isinstance(value, str) for value in item["passage_ids"])
            or not isinstance(item["coverage_mode"], str)
        ):
            raise TypeError("claim requirement fields have invalid types")
        claims.append(
            ClaimEvidenceRequirement(
                claim_id=item["claim_id"],
                statement=item["statement"],
                passage_ids=tuple(item["passage_ids"]),
                coverage_mode=cast(CoverageMode, item["coverage_mode"]),
            )
        )
    return ClaimEvidenceGoldCase(
        query_id=raw["query_id"],
        answerable=raw["answerable"],
        expected_decision=cast(FinalDecision, raw["expected_decision"]),
        claims=tuple(claims),
    )


def _unique_by_id(values: Sequence[Any], label: str) -> dict[str, Any]:
    result = {str(value.query_id): value for value in values}
    if len(result) != len(values):
        raise ValueError(f"{label} query ids must be unique")
    return result


def _claim_complete(mode: CoverageMode, required: set[str], matched: set[str]) -> bool:
    return bool(matched) if mode == "any" else required.issubset(matched)


def _evidence_passage_ids(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError("generation evidence must be a list")
    passages: list[str] = []
    for item in value:
        if not isinstance(item, Mapping) or not str(item.get("passage_id", "")).strip():
            raise ValueError("generation evidence requires passage ids")
        passages.append(str(item["passage_id"]).strip())
    return tuple(dict.fromkeys(passages))


def _string_ids(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list | tuple) or any(not isinstance(item, str) for item in value):
        raise ValueError("retrieval documents must be a list of strings")
    return tuple(dict.fromkeys(item.strip() for item in value if item.strip()))


def _decision(value: Any) -> FinalDecision:
    if value not in {"answer", "abstain"}:
        raise ValueError("generation claim evaluation requires an accepted decision")
    return cast(FinalDecision, value)


def _optional_mean(values: Iterable[float]) -> float | None:
    normalized = list(values)
    return round(sum(normalized) / len(normalized), 4) if normalized else None


def _optional_rate(values: Iterable[bool]) -> float | None:
    normalized = list(values)
    return round(sum(normalized) / len(normalized), 4) if normalized else None


__all__ = [
    "ClaimEvidenceEvalCase",
    "ClaimEvidenceGoldCase",
    "ClaimEvidenceRequirement",
    "claim_eval_cases_from_report",
    "evaluate_claim_evidence",
    "load_claim_evidence_gold",
    "missing_claim_brief",
]
