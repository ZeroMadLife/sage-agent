"""Run the real LLMWiki answer stage over the verified long-book index.

This evaluator keeps retrieval, bounded recovery, generation, and judging as
separate receipts. It never asks a model for chain-of-thought and never stores
the raw book corpus in a tracked report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from core.knowledge.benchmark import KnowledgeBenchmarkQueryV2, load_benchmark_v2, passage_id
from core.knowledge.benchmark_runner import (
    build_benchmark_store,
    load_embedding_provider,
    load_manifest,
)
from core.knowledge.retrieval import KnowledgeAblationPolicy, KnowledgeSearchHit
from core.llm import create_llm
from evals.book_learning_stages import GenerationEvalCase, evaluate_generation


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=repo_root / "evals" / "book_learning_benchmark_v1_manifest.json",
    )
    parser.add_argument(
        "--provider-factory",
        default="scripts.benchmark_providers.fastembed_local:create_provider",
    )
    parser.add_argument("--generator-model", default="doubao:Doubao-Seed-2.0-pro")
    parser.add_argument("--judge-model", default="deepseek:deepseek-v4-flash")
    parser.add_argument("--strategy", choices=_strategies(), default="contextual_chunk")
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--max-recovery-queries", type=int, default=2)
    parser.add_argument("--max-cases", type=int)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skip-fetch", action="store_true")
    args = parser.parse_args()
    if not args.skip_fetch:
        raise RuntimeError("generation runner requires --skip-fetch after corpus verification")
    if not 1 <= args.max_recovery_queries <= 2:
        raise ValueError("max recovery queries must be between 1 and 2")

    return asyncio.run(_run(args, repo_root))


async def _run(args: argparse.Namespace, repo_root: Path) -> int:
    manifest = load_manifest(repo_root, args.manifest.resolve())
    source_queries = load_benchmark_v2(repo_root / manifest.dataset)
    queries = _select_queries(source_queries, args.case_id, args.max_cases)
    provider = load_embedding_provider(args.provider_factory)
    policy = KnowledgeAblationPolicy(strategy=args.strategy)
    generator = create_llm(args.generator_model, temperature=0.0)
    judge = create_llm(args.judge_model, temperature=0.0)

    with tempfile.TemporaryDirectory(
        prefix="sage-book-generation-", dir=repo_root / ".coding"
    ) as temp:
        store, chunking, available_passages = build_benchmark_store(
            repo_root,
            manifest,
            workspace_path=Path(temp) / "workspace",
            database_path=Path(temp) / "knowledge.sqlite3",
            embedding_provider=provider,
            ablation_policy=policy,
            query_texts=tuple(query.query for query in source_queries),
        )
        records: list[dict[str, Any]] = []
        eval_cases: list[GenerationEvalCase] = []
        for query in queries:
            record, eval_case = await _evaluate_case(
                query,
                store=store,
                generator=generator,
                judge=judge,
                top_k=args.top_k,
                max_recovery_queries=args.max_recovery_queries,
                available_passages=available_passages,
            )
            records.append(record)
            eval_cases.append(eval_case)

    result = {
        "schema_version": 1,
        "stage": "llmwiki_generation",
        "protocol": {
            "generator_model": args.generator_model,
            "judge_model": args.judge_model,
            "max_recovery_queries": args.max_recovery_queries,
            "chain_of_thought_required": False,
            "answer_requires_citation_contract": True,
            "judge_is_auxiliary_to_server_gate": True,
            "context_scope": "retrieved_candidate_passages",
        },
        "benchmark": {
            "benchmark_id": manifest.benchmark_id,
            "benchmark_revision": manifest.benchmark_revision,
            "dataset_sha256": manifest.dataset_sha256,
            "corpus_file_count": len(manifest.files),
            "case_count": len(queries),
            "source_commit": _git_value(repo_root, "rev-parse", "HEAD"),
            "source_dirty": bool(_git_value(repo_root, "status", "--porcelain")),
        },
        "provider": {
            "model_id": provider.model_id,
            "model_revision": provider.model_revision,
            "dimensions": provider.dimensions,
            "supports_semantic_recall": provider.supports_semantic_recall,
        },
        "chunking": chunking,
        "metrics": evaluate_generation(eval_cases),
        "cases": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0


async def _evaluate_case(
    query: KnowledgeBenchmarkQueryV2,
    *,
    store: Any,
    generator: Any,
    judge: Any,
    top_k: int,
    max_recovery_queries: int,
    available_passages: set[str],
) -> tuple[dict[str, Any], GenerationEvalCase]:
    started = time.perf_counter()
    first_hits = store.search(query.query, top_k=top_k)
    evidence = _evidence(first_hits)
    first_evidence = list(evidence)
    planner_prompt = _planner_prompt(query.query, evidence, retry_available=True)
    planner, planner_usage = await _invoke_json(generator, planner_prompt, "planner")
    rewrite_queries = _rewrite_queries(planner, max_recovery_queries)
    recovery_records: list[dict[str, Any]] = []
    planner_rounds = 1
    if rewrite_queries and planner.get("decision") in {"retry", "delegate_research"}:
        for rewrite in rewrite_queries:
            rewrite_hits = store.search(rewrite, top_k=top_k)
            rewrite_evidence = _evidence(rewrite_hits)
            recovery_records.append({"query": rewrite, "evidence": rewrite_evidence})
            evidence = _merge_evidence(evidence, rewrite_evidence)
            planner_rounds += 1

    final_plan = planner
    final_planner_usage: dict[str, int] = {}
    if recovery_records:
        final_plan, final_planner_usage = await _invoke_json(
            generator,
            _planner_prompt(query.query, evidence, retry_available=False),
            "planner_final",
        )
    model_decision = _planner_decision(final_plan)
    answer_payload: dict[str, Any] = {
        "decision": "abstain",
        "answer_markdown": "",
        "claims": [],
        "citation_ids": [],
    }
    answer_usage: dict[str, int] = {}
    judge_payload: dict[str, Any] = {}
    judge_usage: dict[str, int] = {}
    if model_decision == "answer":
        answer_payload, answer_usage = await _invoke_json(
            generator,
            _answer_prompt(query.query, evidence),
            "answer",
        )
        if answer_payload.get("decision") == "answer":
            judge_payload, judge_usage = await _invoke_json(
                judge,
                _judge_prompt(
                    query,
                    evidence,
                    answer_payload,
                ),
                "judge",
            )

    allowed_citations = {item["citation_id"] for item in evidence}
    answer_citations = tuple(
        citation
        for citation in _string_list(answer_payload.get("citation_ids"))
        if citation in allowed_citations
    )
    claims = _claims(answer_payload.get("claims"))
    generated_claims = tuple(item["claim_id"] for item in claims)
    supported_generated_claims = tuple(
        claim_id
        for claim_id in _string_list(judge_payload.get("supported_claim_ids"))
        if claim_id in generated_claims
    )
    judge_supported_claims = set(supported_generated_claims)
    unsupported_claims = tuple(
        claim_id for claim_id in generated_claims if claim_id not in judge_supported_claims
    )
    supported_citations = tuple(
        citation
        for citation in _string_list(judge_payload.get("supported_citation_ids"))
        if citation in answer_citations
    )
    present_claims = tuple(
        claim
        for claim in query.required_claims
        if claim in _string_list(judge_payload.get("covered_required_claims"))
    )
    accepted_decision = _accepted_decision(
        model_decision=model_decision,
        answer_payload=answer_payload,
        answer_citations=answer_citations,
    )
    context_precision, context_recall = _context_metrics(query, evidence)
    faithfulness = _optional_score(judge_payload.get("faithfulness"))
    answer_relevance = _optional_score(judge_payload.get("answer_relevance"))
    elapsed_ms = round((time.perf_counter() - started) * 1_000)
    usage = _sum_usage(planner_usage, final_planner_usage, answer_usage, judge_usage)
    record = {
        "query_id": query.query_id,
        "query": query.query,
        "answerable": query.answerable,
        "category": query.category,
        "split": query.split,
        "model_decision": model_decision,
        "accepted_decision": accepted_decision,
        "stop_reason": _stop_reason(model_decision, accepted_decision, evidence),
        "planner_rounds": planner_rounds,
        "rewrite_queries": rewrite_queries,
        "first_pass_evidence": first_evidence,
        "recovery": recovery_records,
        "final_evidence": evidence,
        "answer": answer_payload,
        "judge": judge_payload,
        "metrics": {
            "context_precision": context_precision,
            "context_recall": context_recall,
            "faithfulness": faithfulness,
            "answer_relevance": answer_relevance,
        },
        "usage": usage,
        "latency_ms": elapsed_ms,
        "available_passage_count": len(available_passages),
    }
    return record, GenerationEvalCase(
        case_id=query.query_id,
        answerable=query.answerable,
        final_decision=accepted_decision,
        required_claims=query.required_claims,
        present_claims=present_claims,
        unsupported_claims=unsupported_claims,
        answer_citations=answer_citations,
        supported_citations=supported_citations,
        generated_claims=generated_claims,
        supported_generated_claims=supported_generated_claims,
        context_precision=context_precision,
        context_recall=context_recall,
        faithfulness=faithfulness,
        answer_relevance=answer_relevance,
    )


def _planner_prompt(query: str, evidence: list[dict[str, Any]], *, retry_available: bool) -> str:
    return _json_prompt(
        "You are Sage's bounded retrieval planner. Decide whether the supplied evidence is enough to answer the user. "
        "Use only the evidence; do not invent facts. If an aspect is missing and retry is available, return at most two precise search rewrites. "
        "Never reveal reasoning or chain-of-thought.",
        {
            "question": query,
            "retry_available": retry_available,
            "evidence": evidence,
            "output_schema": {
                "decision": "answer|retry|delegate_research|abstain",
                "missing_aspects": ["short labels"],
                "rewrite_queries": ["at most two search queries"],
                "stop_reason": "evidence_sufficient|missing_aspects|no_new_evidence|insufficient_evidence|budget_exhausted",
            },
        },
    )


def _answer_prompt(query: str, evidence: list[dict[str, Any]]) -> str:
    return _json_prompt(
        "You are Sage's LLMWiki answer writer. Answer only from the supplied evidence. Write a concise useful answer, "
        "and attach each factual claim to one or more exact citation_id values from the evidence. If evidence is insufficient, abstain. "
        "Do not expose chain-of-thought or retrieval steps.",
        {
            "question": query,
            "evidence": evidence,
            "output_schema": {
                "decision": "answer|abstain",
                "answer_markdown": "final user-facing answer without hidden reasoning",
                "claims": [{"claim_id": "c1", "text": "claim", "citation_ids": ["kcite..."]}],
                "citation_ids": ["kcite..."],
            },
        },
    )


def _judge_prompt(
    query: KnowledgeBenchmarkQueryV2,
    evidence: list[dict[str, Any]],
    answer: Mapping[str, Any],
) -> str:
    return _json_prompt(
        "You are an independent RAG judge. Check every answer claim against the evidence, and check whether citations actually support it. "
        "Do not reward plausible world knowledge. Return only JSON and no reasoning.",
        {
            "question": query.query,
            "required_claims": list(query.required_claims),
            "forbidden_claims": list(query.forbidden_claims),
            "evidence": evidence,
            "answer": answer,
            "output_schema": {
                "covered_required_claims": ["exact required claim strings covered by answer"],
                "supported_claim_ids": ["claim ids fully supported by evidence"],
                "unsupported_claim_ids": ["claim ids not fully supported"],
                "supported_citation_ids": ["citation ids that support the cited claim"],
                "forbidden_claims_present": ["forbidden claim strings present"],
                "faithfulness": 0.0,
                "answer_relevance": 0.0,
            },
        },
    )


def _json_prompt(instruction: str, payload: Mapping[str, Any]) -> str:
    return (
        instruction
        + "\nReturn one JSON object with exactly the requested shape.\n"
        + json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )


async def _invoke_json(llm: Any, prompt: str, stage: str) -> tuple[dict[str, Any], dict[str, int]]:
    response = await llm.ainvoke(prompt)
    text = _response_text(response)
    payload = _parse_json_object(text, stage)
    return payload, _usage(response)


def _response_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return str(content)


def _parse_json_object(text: str, stage: str) -> dict[str, Any]:
    candidate = text.strip()
    if "```" in candidate:
        candidate = candidate.replace("```json", "").replace("```", "").strip()
    start, end = candidate.find("{"), candidate.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"{stage} did not return a JSON object")
    payload = json.loads(candidate[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError(f"{stage} JSON result must be an object")
    return payload


def _evidence(hits: Iterable[KnowledgeSearchHit]) -> list[dict[str, Any]]:
    return [
        {
            "citation_id": hit.citation_id,
            "passage_id": passage_id(
                hit.chunk.source_relative_path,
                " / ".join(hit.chunk.heading_path or (hit.chunk.title,)),
            ),
            "section": " / ".join(hit.chunk.heading_path),
            "excerpt": hit.chunk.text[:2_000],
            "rank": hit.rank,
            "sparse_score": hit.sparse_score,
            "dense_score": hit.dense_score,
        }
        for hit in hits
    ]


def _merge_evidence(
    first: list[dict[str, Any]], second: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in (*first, *second):
        citation_id = str(item["citation_id"])
        if citation_id not in seen:
            seen.add(citation_id)
            merged.append(item)
    return merged


def _claims(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    claims: list[dict[str, Any]] = []
    for index, raw in enumerate(value[:32], start=1):
        if not isinstance(raw, dict):
            continue
        claim_id = str(raw.get("claim_id", f"c{index}"))[:80]
        text = str(raw.get("text", "")).strip()[:2_000]
        citations = _string_list(raw.get("citation_ids"))
        if claim_id and text:
            claims.append({"claim_id": claim_id, "text": text, "citation_ids": citations})
    return claims


def _context_metrics(
    query: KnowledgeBenchmarkQueryV2,
    evidence: list[dict[str, Any]],
) -> tuple[float | None, float | None]:
    if not query.answerable:
        return None, None
    gold = {item.document_id for item in query.relevant}
    returned = tuple(dict.fromkeys(str(item["passage_id"]) for item in evidence))
    relevant = sum(item in gold for item in returned)
    return (
        round(relevant / len(returned), 4) if returned else 0.0,
        round(len(set(returned) & gold) / len(gold), 4) if gold else 1.0,
    )


def _accepted_decision(
    *, model_decision: str, answer_payload: Mapping[str, Any], answer_citations: tuple[str, ...]
) -> str:
    if model_decision != "answer" or answer_payload.get("decision") != "answer":
        return "abstain"
    claims = _claims(answer_payload.get("claims"))
    if not answer_citations or not claims:
        return "abstain"
    allowed = set(answer_citations)
    if any(
        not item["citation_ids"] or not set(item["citation_ids"]).issubset(allowed)
        for item in claims
    ):
        return "abstain"
    return "answer"


def _stop_reason(
    model_decision: str, accepted_decision: str, evidence: list[dict[str, Any]]
) -> str:
    if accepted_decision == "answer":
        return "evidence_sufficient"
    if model_decision in {"retry", "delegate_research"}:
        return "insufficient_evidence"
    if model_decision == "answer":
        return "synthesis_uncited"
    return "abstain_no_evidence" if not evidence else "abstain_insufficient_evidence"


def _rewrite_queries(payload: Mapping[str, Any], limit: int) -> tuple[str, ...]:
    decision = str(payload.get("decision", "abstain"))
    if decision not in {"retry", "delegate_research"}:
        return ()
    return tuple(value[:2_000] for value in _string_list(payload.get("rewrite_queries")))[:limit]


def _planner_decision(payload: Mapping[str, Any]) -> str:
    decision = str(payload.get("decision", "abstain"))
    if decision not in {"answer", "retry", "delegate_research", "abstain"}:
        return "abstain"
    return decision


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _optional_score(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return max(0.0, min(1.0, float(value)))


def _usage(response: Any) -> dict[str, int]:
    metadata = getattr(response, "usage_metadata", None)
    if not isinstance(metadata, Mapping):
        return {}
    values: dict[str, int] = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        value = metadata.get(key)
        if isinstance(value, int) and value >= 0:
            values[key] = value
    return values


def _sum_usage(*usages: Mapping[str, int]) -> dict[str, int]:
    result: dict[str, int] = {}
    for usage in usages:
        for key, value in usage.items():
            result[key] = result.get(key, 0) + value
    return result


def _select_queries(
    queries: tuple[KnowledgeBenchmarkQueryV2, ...], case_ids: list[str], max_cases: int | None
) -> tuple[KnowledgeBenchmarkQueryV2, ...]:
    if case_ids:
        selected = tuple(query for query in queries if query.query_id in set(case_ids))
        if len(selected) != len(set(case_ids)):
            raise ValueError("unknown book benchmark case id")
    else:
        selected = queries
    if max_cases is not None:
        if max_cases < 1:
            raise ValueError("max cases must be positive")
        selected = selected[:max_cases]
    return selected


def _strategies() -> tuple[str, ...]:
    return (
        "baseline",
        "contextual_chunk",
        "parent_child",
        "described_parent_child",
        "semantic_boundary",
    )


def _git_value(root: Path, *args: str) -> str:
    import subprocess

    completed = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False, timeout=10
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
