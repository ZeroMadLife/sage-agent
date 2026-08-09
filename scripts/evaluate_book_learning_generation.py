"""Run the real LLMWiki answer stage over the verified long-book index.

This evaluator keeps retrieval, bounded recovery, generation, and judging as
separate receipts. It never asks a model for chain-of-thought and never stores
the raw book corpus in a tracked report.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import tempfile
import time
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from core.knowledge.benchmark import KnowledgeBenchmarkQueryV2, load_benchmark_v2, passage_id
from core.knowledge.benchmark_runner import (
    BenchmarkIndexFactory,
    build_benchmark_store,
    cleanup_benchmark_store,
    load_embedding_provider,
    load_manifest,
)
from core.knowledge.postgres_index import (
    PostgresKnowledgeIndex,
    PostgresKnowledgeIndexConfig,
)
from core.knowledge.postgres_retrieval import PgTextsearchBm25Retriever
from core.knowledge.retrieval import (
    DenseEmbeddingProvider,
    KnowledgeAblationPolicy,
    KnowledgeRetrievalMode,
    KnowledgeSearchHit,
)
from core.llm import create_llm
from evals.book_learning_claims import (
    ClaimEvidenceGoldCase,
    claim_eval_cases_from_report,
    evaluate_claim_evidence,
    load_claim_evidence_gold,
    missing_claim_brief,
)
from evals.book_learning_stages import GenerationEvalCase, evaluate_generation

_DEFAULT_PROVIDER_FACTORY = "scripts.benchmark_providers.doubao_multimodal:create_provider"
_MAX_EVIDENCE_ITEMS = 12
_MAX_EVIDENCE_EXCERPT_CHARS = 1_200


class ModelInvocationError(RuntimeError):
    """Bounded, secret-free receipt for one failed model stage."""

    def __init__(
        self,
        *,
        stage: str,
        error_type: str,
        elapsed_ms: int,
        prompt_chars: int,
    ) -> None:
        super().__init__(f"{stage} failed: {error_type}")
        self.stage = stage
        self.error_type = error_type
        self.elapsed_ms = elapsed_ms
        self.prompt_chars = prompt_chars


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
        default=_DEFAULT_PROVIDER_FACTORY,
    )
    parser.add_argument(
        "--backend",
        choices=("sqlite", "postgres"),
        default="sqlite",
    )
    parser.add_argument(
        "--postgres-sparse",
        choices=("native", "bm25"),
        default="native",
    )
    parser.add_argument(
        "--retrieval-mode",
        choices=("sparse", "dense", "hybrid"),
        default="hybrid",
    )
    parser.add_argument(
        "--postgres-dsn",
        default=os.environ.get("SAGE_BOOK_BENCHMARK_POSTGRES_DSN", ""),
        help="PostgreSQL DSN; prefer SAGE_BOOK_BENCHMARK_POSTGRES_DSN",
    )
    parser.add_argument(
        "--claim-gold",
        type=Path,
        default=repo_root / "evals" / "book_learning_claim_gold_v1.jsonl",
    )
    parser.add_argument("--generator-model", default="doubao:Doubao-Seed-2.0-pro")
    parser.add_argument("--judge-model", default="deepseek:deepseek-v4-flash")
    parser.add_argument("--strategy", choices=_strategies(), default="contextual_chunk")
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--max-recovery-queries", type=int, default=2)
    parser.add_argument("--max-cases", type=int)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--request-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skip-fetch", action="store_true")
    args = parser.parse_args()
    if not args.skip_fetch:
        raise RuntimeError("generation runner requires --skip-fetch after corpus verification")
    if not 1 <= args.max_recovery_queries <= 2:
        raise ValueError("max recovery queries must be between 1 and 2")
    if args.request_timeout_seconds <= 0:
        raise ValueError("request timeout seconds must be positive")
    if args.backend == "postgres" and not args.postgres_dsn.strip():
        raise ValueError("PostgreSQL generation evaluation requires a DSN")

    return asyncio.run(_run(args, repo_root))


def _generation_index_factory(
    *,
    backend: str,
    postgres_dsn: str,
    postgres_sparse: str,
) -> BenchmarkIndexFactory | None:
    if backend == "sqlite":
        return None
    if backend != "postgres":
        raise ValueError("unknown generation evaluation backend")
    if not postgres_dsn.strip():
        raise ValueError("PostgreSQL generation evaluation requires a DSN")
    if postgres_sparse not in {"native", "bm25"}:
        raise ValueError("unknown PostgreSQL sparse strategy")
    sparse_retriever = PgTextsearchBm25Retriever() if postgres_sparse == "bm25" else None

    def factory(
        workspace_id: str,
        provider: DenseEmbeddingProvider,
        policy: KnowledgeAblationPolicy,
    ) -> PostgresKnowledgeIndex:
        return PostgresKnowledgeIndex(
            PostgresKnowledgeIndexConfig(dsn=postgres_dsn),
            workspace_id=workspace_id,
            embedding_provider=provider,
            ablation_policy=policy,
            sparse_retriever=sparse_retriever,
        )

    return factory


async def _run(args: argparse.Namespace, repo_root: Path) -> int:
    manifest = load_manifest(repo_root, args.manifest.resolve())
    source_queries = load_benchmark_v2(repo_root / manifest.dataset)
    queries = _select_queries(source_queries, args.case_id, args.max_cases)
    all_claim_gold = {
        case.query_id: case for case in load_claim_evidence_gold(args.claim_gold.resolve())
    }
    try:
        claim_gold = tuple(all_claim_gold[query.query_id] for query in queries)
    except KeyError as exc:
        raise ValueError(f"claim gold is missing benchmark query: {exc.args[0]}") from exc
    provider = load_embedding_provider(args.provider_factory)
    policy = KnowledgeAblationPolicy(strategy=args.strategy)
    # 评测需要把 provider 故障和质量结果分开；关闭 SDK 隐式重试，避免
    # 单个 case 的实际耗时超过报告中的 request_timeout_seconds。
    llm_options = _evaluation_llm_options(args.request_timeout_seconds)
    generator = create_llm(args.generator_model, temperature=0.0, **llm_options)
    judge = create_llm(args.judge_model, temperature=0.0, **llm_options)
    index_factory = _generation_index_factory(
        backend=args.backend,
        postgres_dsn=args.postgres_dsn,
        postgres_sparse=args.postgres_sparse,
    )
    workspace_id = f"sage-book-generation-{uuid.uuid4().hex}"
    retrieval_mode = cast(KnowledgeRetrievalMode, args.retrieval_mode)

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
            index_factory=index_factory,
            workspace_id=workspace_id,
        )
        try:
            records: list[dict[str, Any]] = []
            eval_cases: list[GenerationEvalCase] = []
            for query in queries:
                case_started = time.perf_counter()
                try:
                    record, eval_case = await _evaluate_case(
                        query,
                        claim_gold=all_claim_gold[query.query_id],
                        store=store,
                        generator=generator,
                        judge=judge,
                        top_k=args.top_k,
                        retrieval_mode=retrieval_mode,
                        max_recovery_queries=args.max_recovery_queries,
                        request_timeout_seconds=args.request_timeout_seconds,
                        available_passages=available_passages,
                    )
                except ModelInvocationError as failure:
                    record, eval_case = _failed_case(
                        query,
                        failure,
                        latency_ms=round((time.perf_counter() - case_started) * 1_000),
                        available_passage_count=len(available_passages),
                    )
                records.append(record)
                eval_cases.append(eval_case)
            index_summary = asdict(store.index_summary())
        finally:
            cleanup_benchmark_store(store)

    claim_report_source = {"stage": "llmwiki_generation", "cases": records}
    claim_observations = claim_eval_cases_from_report(claim_report_source)
    claim_evidence = evaluate_claim_evidence(claim_gold, claim_observations)
    claim_evidence["gold"] = _claim_gold_receipt(repo_root, args.claim_gold)
    result = {
        "schema_version": 1,
        "stage": "llmwiki_generation",
        "protocol": {
            "generator_model": args.generator_model,
            "judge_model": args.judge_model,
            "top_k": args.top_k,
            "max_recovery_queries": args.max_recovery_queries,
            "request_timeout_seconds": args.request_timeout_seconds,
            "chain_of_thought_required": False,
            "answer_requires_citation_contract": True,
            "judge_is_auxiliary_to_server_gate": True,
            "claim_evidence_online_gate_activated": False,
            "offline_missing_claim_brief": {
                "enabled": True,
                "source": "frozen_claim_gold",
                "passage_ids_exposed_to_planner": False,
                "online_runtime": False,
            },
            "context_scope": "retrieved_candidate_passages",
            "retrieval_backend": args.backend,
            "postgres_sparse": args.postgres_sparse if args.backend == "postgres" else None,
            "retrieval_mode": retrieval_mode,
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
        "index": index_summary,
        "runtime_metrics": _runtime_metrics(records),
        "observed_models": _observed_models(records),
        "claim_evidence": claim_evidence,
        "metrics": evaluate_generation(eval_cases),
        "cases": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0


def _evaluation_llm_options(timeout_seconds: float) -> dict[str, Any]:
    """Return bounded client options for deterministic provider receipts."""

    return {"max_retries": 0, "timeout": timeout_seconds}


async def _evaluate_case(
    query: KnowledgeBenchmarkQueryV2,
    *,
    claim_gold: ClaimEvidenceGoldCase,
    store: Any,
    generator: Any,
    judge: Any,
    top_k: int,
    retrieval_mode: KnowledgeRetrievalMode,
    max_recovery_queries: int,
    request_timeout_seconds: float,
    available_passages: set[str],
) -> tuple[dict[str, Any], GenerationEvalCase]:
    started = time.perf_counter()
    first_hits = store.search(query.query, top_k=top_k, retrieval_mode=retrieval_mode)
    evidence = _evidence(first_hits)
    first_evidence = list(evidence)
    first_missing_claims = missing_claim_brief(
        claim_gold,
        _passage_ids(evidence),
    )
    planner_prompt = _planner_prompt(
        query.query,
        evidence,
        retry_available=True,
        missing_claims=first_missing_claims,
    )
    planner, planner_usage, planner_model = await _invoke_json(
        generator,
        planner_prompt,
        "planner",
        timeout_seconds=request_timeout_seconds,
    )
    rewrite_queries = _rewrite_queries(planner, max_recovery_queries)
    recovery_records: list[dict[str, Any]] = []
    planner_rounds = 1
    final_missing_claims = first_missing_claims
    if rewrite_queries and planner.get("decision") in {"retry", "delegate_research"}:
        for rewrite in rewrite_queries:
            rewrite_hits = store.search(rewrite, top_k=top_k, retrieval_mode=retrieval_mode)
            rewrite_evidence = _evidence(rewrite_hits)
            recovery_records.append({"query": rewrite, "evidence": rewrite_evidence})
            evidence = _merge_evidence(evidence, rewrite_evidence)
        planner_rounds = 2
        final_missing_claims = missing_claim_brief(
            claim_gold,
            _passage_ids(evidence),
        )

    final_plan = planner
    final_planner_usage: dict[str, int] = {}
    final_planner_model: dict[str, str] = {}
    if recovery_records:
        final_plan, final_planner_usage, final_planner_model = await _invoke_json(
            generator,
            _planner_prompt(
                query.query,
                evidence,
                retry_available=False,
                missing_claims=final_missing_claims,
            ),
            "planner_final",
            timeout_seconds=request_timeout_seconds,
        )
    model_decision = _planner_decision(final_plan)
    answer_payload: dict[str, Any] = {
        "decision": "abstain",
        "answer_markdown": "",
        "claims": [],
        "citation_ids": [],
    }
    answer_usage: dict[str, int] = {}
    answer_model: dict[str, str] = {}
    judge_payload: dict[str, Any] = {}
    judge_usage: dict[str, int] = {}
    judge_model: dict[str, str] = {}
    if model_decision == "answer":
        answer_payload, answer_usage, answer_model = await _invoke_json(
            generator,
            _answer_prompt(query.query, evidence),
            "answer",
            timeout_seconds=request_timeout_seconds,
        )
        if answer_payload.get("decision") == "answer":
            judge_payload, judge_usage, judge_model = await _invoke_json(
                judge,
                _judge_prompt(
                    query,
                    claim_gold,
                    evidence,
                    answer_payload,
                ),
                "judge",
                timeout_seconds=request_timeout_seconds,
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
    gold_claim_ids = tuple(claim.claim_id for claim in claim_gold.claims)
    covered_gold_claim_ids = _known_ids(judge_payload.get("covered_gold_claim_ids"), gold_claim_ids)
    contradicted_gold_claim_ids = _known_ids(
        judge_payload.get("contradicted_gold_claim_ids"), gold_claim_ids
    )
    unsupported_gold_claim_ids = _known_ids(
        judge_payload.get("unsupported_gold_claim_ids"), gold_claim_ids
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
        "missing_claims_first_pass": list(first_missing_claims),
        "missing_claims_before_final_plan": list(final_missing_claims),
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
            "answer_claim_coverage": (
                round(len(covered_gold_claim_ids) / len(gold_claim_ids), 4)
                if gold_claim_ids
                else None
            ),
            "answer_correct": (
                accepted_decision == "answer"
                and set(gold_claim_ids).issubset(covered_gold_claim_ids)
                and not contradicted_gold_claim_ids
                if gold_claim_ids
                else None
            ),
        },
        "usage": usage,
        "models": {
            "planner": planner_model,
            "planner_final": final_planner_model,
            "answer": answer_model,
            "judge": judge_model,
        },
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
        gold_claim_ids=gold_claim_ids,
        covered_gold_claim_ids=covered_gold_claim_ids,
        contradicted_gold_claim_ids=contradicted_gold_claim_ids,
        unsupported_gold_claim_ids=unsupported_gold_claim_ids,
        context_precision=context_precision,
        context_recall=context_recall,
        faithfulness=faithfulness,
        answer_relevance=answer_relevance,
    )


def _planner_prompt(
    query: str,
    evidence: list[dict[str, Any]],
    *,
    retry_available: bool,
    missing_claims: Sequence[Mapping[str, str]] = (),
) -> str:
    return _json_prompt(
        "You are Sage's bounded retrieval planner. Decide whether the supplied evidence is enough to answer the user. "
        "Use only the evidence; do not invent facts. If an aspect is missing and retry is available, return at most two precise search rewrites. "
        "Never reveal reasoning or chain-of-thought.",
        {
            "question": query,
            "retry_available": retry_available,
            "missing_claims": [
                {
                    "claim_id": str(item.get("claim_id", "")),
                    "statement": str(item.get("statement", "")),
                }
                for item in missing_claims
                if str(item.get("claim_id", "")).strip() and str(item.get("statement", "")).strip()
            ],
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
    claim_gold: ClaimEvidenceGoldCase,
    evidence: list[dict[str, Any]],
    answer: Mapping[str, Any],
) -> str:
    return _json_prompt(
        "You are an independent RAG judge. Check every generated answer claim against the evidence, and separately compare the final answer with each atomic gold claim. "
        "A covered gold claim must be explicitly stated or semantically entailed by the final answer. A contradicted gold claim conflicts with the final answer. "
        "An unsupported gold claim is mentioned by the answer but lacks support in the supplied evidence. Do not reward plausible world knowledge. Return only JSON and no reasoning.",
        {
            "question": query.query,
            "gold_claims": [
                {"claim_id": claim.claim_id, "statement": claim.statement}
                for claim in claim_gold.claims
            ],
            "legacy_required_claims": list(query.required_claims),
            "forbidden_claims": list(query.forbidden_claims),
            "evidence": evidence,
            "answer": answer,
            "output_schema": {
                "covered_gold_claim_ids": ["gold claim ids covered by the final answer"],
                "contradicted_gold_claim_ids": ["gold claim ids contradicted by the final answer"],
                "unsupported_gold_claim_ids": [
                    "gold claim ids mentioned but unsupported by evidence"
                ],
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


async def _invoke_json(
    llm: Any, prompt: str, stage: str, *, timeout_seconds: float = 120.0
) -> tuple[dict[str, Any], dict[str, int], dict[str, str]]:
    started = time.perf_counter()
    try:
        async with asyncio.timeout(timeout_seconds):
            response = await llm.ainvoke(prompt)
        text = _response_text(response)
        payload = _parse_json_object(text, stage)
    except TimeoutError as exc:
        raise ModelInvocationError(
            stage=stage,
            error_type="timeout",
            elapsed_ms=round((time.perf_counter() - started) * 1_000),
            prompt_chars=len(prompt),
        ) from exc
    except Exception as exc:
        raise ModelInvocationError(
            stage=stage,
            error_type=("invalid_response" if isinstance(exc, ValueError) else "provider_error"),
            elapsed_ms=round((time.perf_counter() - started) * 1_000),
            prompt_chars=len(prompt),
        ) from exc
    return payload, _usage(response), _model_receipt(response)


def _failed_case(
    query: KnowledgeBenchmarkQueryV2,
    failure: ModelInvocationError,
    *,
    latency_ms: int,
    available_passage_count: int,
) -> tuple[dict[str, Any], GenerationEvalCase]:
    record = {
        "query_id": query.query_id,
        "query": query.query,
        "answerable": query.answerable,
        "category": query.category,
        "split": query.split,
        "model_decision": "error",
        "accepted_decision": "abstain",
        "stop_reason": f"provider_{failure.error_type}",
        "planner_rounds": 0,
        "rewrite_queries": [],
        "first_pass_evidence": [],
        "recovery": [],
        "final_evidence": [],
        "answer": {},
        "judge": {},
        "metrics": {
            "context_precision": None,
            "context_recall": None,
            "faithfulness": None,
            "answer_relevance": None,
        },
        "usage": {},
        "models": {},
        "latency_ms": latency_ms,
        "available_passage_count": available_passage_count,
        "failure": {
            "stage": failure.stage,
            "error_type": failure.error_type,
            "call_latency_ms": failure.elapsed_ms,
            "prompt_chars": failure.prompt_chars,
        },
    }
    return record, GenerationEvalCase(
        case_id=query.query_id,
        answerable=query.answerable,
        final_decision="abstain",
        required_claims=query.required_claims,
        present_claims=(),
        unsupported_claims=(),
        answer_citations=(),
        supported_citations=(),
        evaluation_status="provider_error",
    )


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
    return _bounded_evidence(
        [
            {
                "citation_id": hit.citation_id,
                "passage_id": passage_id(
                    hit.chunk.source_relative_path,
                    " / ".join(hit.chunk.heading_path or (hit.chunk.title,)),
                ),
                "section": " / ".join(hit.chunk.heading_path),
                "excerpt": hit.chunk.text,
                "rank": hit.rank,
                "sparse_score": hit.sparse_score,
                "dense_score": hit.dense_score,
            }
            for hit in hits
        ]
    )


def _passage_ids(evidence: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            str(item.get("passage_id", "")).strip()
            for item in evidence
            if str(item.get("passage_id", "")).strip()
        )
    )


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
    return _bounded_evidence(merged)


def _bounded_evidence(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = [
        {
            **item,
            "excerpt": str(item.get("excerpt", ""))[:_MAX_EVIDENCE_EXCERPT_CHARS],
        }
        for item in evidence
    ]
    selected: list[dict[str, Any]] = []
    selected_citations: set[str] = set()
    selected_passages: set[str] = set()
    for item in normalized:
        passage = str(item.get("passage_id", ""))
        citation = str(item.get("citation_id", ""))
        if passage in selected_passages or citation in selected_citations:
            continue
        selected.append(item)
        selected_passages.add(passage)
        selected_citations.add(citation)
        if len(selected) >= _MAX_EVIDENCE_ITEMS:
            return selected
    for item in normalized:
        citation = str(item.get("citation_id", ""))
        if citation in selected_citations:
            continue
        selected.append(item)
        selected_citations.add(citation)
        if len(selected) >= _MAX_EVIDENCE_ITEMS:
            break
    return selected


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


def _known_ids(value: Any, allowed: tuple[str, ...]) -> tuple[str, ...]:
    requested = set(_string_list(value))
    return tuple(item for item in allowed if item in requested)


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


def _model_receipt(response: Any) -> dict[str, str]:
    metadata = getattr(response, "response_metadata", None)
    if not isinstance(metadata, Mapping):
        return {}
    receipt: dict[str, str] = {}
    for source, target in (
        ("model_name", "model_name"),
        ("model", "model_name"),
        ("system_fingerprint", "system_fingerprint"),
        ("finish_reason", "finish_reason"),
    ):
        value = metadata.get(source)
        if value is not None and target not in receipt:
            receipt[target] = str(value)[:200]
    token_usage = metadata.get("token_usage")
    if isinstance(token_usage, Mapping):
        value = token_usage.get("model_name") or token_usage.get("model")
        if value is not None and "model_name" not in receipt:
            receipt["model_name"] = str(value)[:200]
    return receipt


def _sum_usage(*usages: Mapping[str, int]) -> dict[str, int]:
    result: dict[str, int] = {}
    for usage in usages:
        for key, value in usage.items():
            result[key] = result.get(key, 0) + value
    return result


def _runtime_metrics(records: list[dict[str, Any]]) -> dict[str, object]:
    latencies = [int(record["latency_ms"]) for record in records]
    usage = _sum_usage(
        *(record["usage"] for record in records if isinstance(record.get("usage"), Mapping))
    )
    provider_failures = sum(isinstance(record.get("failure"), Mapping) for record in records)
    return {
        "case_count": len(records),
        "provider_failure_count": provider_failures,
        "provider_failure_rate": round(provider_failures / len(records), 4),
        "recovery_activation_rate": round(
            sum(bool(record["rewrite_queries"]) for record in records) / len(records), 4
        ),
        "accepted_answer_rate": round(
            sum(record["accepted_decision"] == "answer" for record in records) / len(records),
            4,
        ),
        "p50_latency_ms": _percentile(latencies, 0.50),
        "p95_latency_ms": _percentile(latencies, 0.95),
        "token_usage": usage,
        "cost_usd": None,
        "cost_status": "not_computed_without_frozen_price_table",
    }


def _observed_models(records: list[dict[str, Any]]) -> dict[str, list[str]]:
    observed: dict[str, set[str]] = {}
    for record in records:
        models = record.get("models")
        if not isinstance(models, Mapping):
            continue
        for stage, raw in models.items():
            if isinstance(raw, Mapping) and raw.get("model_name"):
                observed.setdefault(str(stage), set()).add(str(raw["model_name"]))
    return {stage: sorted(values) for stage, values in sorted(observed.items())}


def _percentile(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * percentile + 0.9999) - 1))
    return ordered[index]


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


def _claim_gold_receipt(repo_root: Path, path: Path) -> dict[str, object]:
    resolved = path.resolve()
    try:
        dataset = resolved.relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        dataset = str(resolved)
    return {
        "dataset": dataset,
        "sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
        "review_status": "seed_manual",
        "production_claim_allowed": False,
    }


__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
