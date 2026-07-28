from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from core.knowledge.datasets import (
    CorpusManifestEntry,
    EvalCase,
    EvalDatasetManifest,
    load_versioned_dataset,
    validate_eval_dataset,
)
from scripts.validate_knowledge_dataset import build_summary

REPO_ROOT = Path(__file__).parents[3]
DATASET_PATH = REPO_ROOT / "knowledge" / "eval" / "dataset.json"


def test_committed_corpus_and_eval_contract_is_frozen_and_traceable() -> None:
    dataset = load_versioned_dataset(REPO_ROOT, DATASET_PATH)

    assert dataset.manifest.dataset_id == "sage-official-agent-fullstack-v1"
    assert dataset.manifest.dataset_revision == "2026-07-27.1"
    assert len(dataset.corpus) == 9
    assert len(dataset.cases) == 80
    assert {item.project for item in dataset.corpus} == {
        "fastapi",
        "langgraph",
        "pgvector",
        "postgresql",
    }
    assert {item.source_type for item in dataset.corpus} == {
        "official_docs",
        "official_github",
    }
    assert {item.license for item in dataset.corpus} == {"MIT", "PostgreSQL"}
    assert dataset.split_counts == {"dev": 40, "calibration": 20, "test": 20}
    assert all(item.commit_sha in item.source_url for item in dataset.corpus)
    assert all(item.review_status == "approved" for item in dataset.corpus)


def test_validation_summary_is_machine_readable() -> None:
    summary = build_summary(REPO_ROOT, DATASET_PATH)

    assert summary == {
        "dataset_id": "sage-official-agent-fullstack-v1",
        "dataset_revision": "2026-07-27.1",
        "corpus_count": 9,
        "case_count": 80,
        "split_counts": {"dev": 40, "calibration": 20, "test": 20},
        "frozen_test": True,
    }


def test_committed_snapshots_and_dataset_files_match_their_hashes() -> None:
    dataset = load_versioned_dataset(REPO_ROOT, DATASET_PATH)

    for item in dataset.corpus:
        snapshot = REPO_ROOT / item.snapshot_path
        assert snapshot.is_file()
        assert item.content_hash == "sha256:" + hashlib.sha256(snapshot.read_bytes()).hexdigest()

    manifest_path = REPO_ROOT / dataset.manifest.corpus_manifest
    cases_path = REPO_ROOT / dataset.manifest.cases
    assert dataset.manifest.corpus_manifest_sha256 == _sha256(manifest_path)
    assert dataset.manifest.cases_sha256 == _sha256(cases_path)


def test_committed_eval_references_only_approved_sources_without_split_leakage() -> None:
    dataset = load_versioned_dataset(REPO_ROOT, DATASET_PATH)
    source_ids = {item.corpus_id for item in dataset.corpus}
    groups: dict[str, set[str]] = {}

    for case in dataset.cases:
        assert set(case.required_sources) <= source_ids
        assert {item.corpus_id for item in case.required_passages} <= source_ids
        groups.setdefault(case.leakage_group, set()).add(case.dataset_split)

    assert all(len(splits) == 1 for splits in groups.values())
    assert len({case.query.casefold().strip() for case in dataset.cases}) == 80


def test_json_schemas_are_strict_and_list_runtime_fields() -> None:
    corpus_schema = json.loads(
        (REPO_ROOT / "knowledge" / "corpus" / "manifest.schema.json").read_text()
    )
    case_schema = json.loads((REPO_ROOT / "knowledge" / "eval" / "case.schema.json").read_text())
    dataset_schema = json.loads(
        (REPO_ROOT / "knowledge" / "eval" / "dataset.schema.json").read_text()
    )

    assert corpus_schema["additionalProperties"] is False
    assert case_schema["additionalProperties"] is False
    assert dataset_schema["additionalProperties"] is False
    assert set(corpus_schema["required"]) == set(CorpusManifestEntry.model_fields)
    assert set(case_schema["required"]) == set(EvalCase.model_fields)
    assert set(dataset_schema["required"]) == set(EvalDatasetManifest.model_fields)


def test_corpus_contract_rejects_unpinned_or_escaping_sources() -> None:
    payload = _corpus_payload()
    payload["source_url"] = "https://github.com/example/project/blob/main/docs.md"
    with pytest.raises(ValidationError, match="commit_sha"):
        CorpusManifestEntry.model_validate(payload)

    payload = _corpus_payload()
    payload["snapshot_path"] = "../outside.md"
    with pytest.raises(ValidationError, match="relative"):
        CorpusManifestEntry.model_validate(payload)

    payload = _corpus_payload()
    payload["upstream_path"] = "../upstream.md"
    with pytest.raises(ValidationError, match="upstream_path"):
        CorpusManifestEntry.model_validate(payload)


def test_eval_contract_rejects_answerability_and_visual_inconsistency() -> None:
    payload = _case_payload()
    payload["answerable"] = False
    with pytest.raises(ValidationError, match="unanswerable"):
        EvalCase.model_validate(payload)

    payload = _case_payload()
    payload["gold_bbox"] = [1.0, 2.0, 3.0, 4.0]
    with pytest.raises(ValidationError, match="gold_page"):
        EvalCase.model_validate(payload)


def test_eval_contract_rejects_blank_claims_and_duplicate_passages() -> None:
    payload = _case_payload()
    payload["required_claims"] = ["   "]
    with pytest.raises(ValidationError, match="claims"):
        EvalCase.model_validate(payload)

    payload = _case_payload()
    payload["required_passages"].append(payload["required_passages"][0])
    with pytest.raises(ValidationError, match="passages"):
        EvalCase.model_validate(payload)


def test_dataset_validation_rejects_unknown_sources_and_cross_split_groups() -> None:
    corpus = (CorpusManifestEntry.model_validate(_corpus_payload()),)
    unknown = _case_payload()
    unknown["required_sources"] = ["missing-source"]
    unknown["required_passages"][0]["corpus_id"] = "missing-source"
    with pytest.raises(ValueError, match="unknown corpus"):
        validate_eval_dataset(corpus, (EvalCase.model_validate(unknown),))

    first = EvalCase.model_validate(_case_payload())
    second_payload = _case_payload()
    second_payload["case_id"] = "case-002"
    second_payload["query"] = "A different wording"
    second_payload["dataset_split"] = "test"
    second = EvalCase.model_validate(second_payload)
    with pytest.raises(ValueError, match="leakage group"):
        validate_eval_dataset(corpus, (first, second))


def test_dataset_validation_rejects_a_missing_passage_anchor(tmp_path: Path) -> None:
    snapshot = tmp_path / "knowledge" / "corpus" / "snapshots" / "example" / "docs.md"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text("# Example\n\n## Different heading\n\nEvidence.\n", encoding="utf-8")
    corpus_payload = _corpus_payload()
    corpus_payload["content_hash"] = _sha256(snapshot)
    corpus = (CorpusManifestEntry.model_validate(corpus_payload),)
    cases = (EvalCase.model_validate(_case_payload()),)

    with pytest.raises(ValueError, match="passage anchor does not exist"):
        validate_eval_dataset(corpus, cases, repo_root=tmp_path)


def _corpus_payload() -> dict[str, Any]:
    commit = "a" * 40
    return {
        "corpus_id": "example-doc",
        "source_url": f"https://github.com/example/project/blob/{commit}/docs.md",
        "source_type": "official_docs",
        "project": "example",
        "version": "main",
        "commit_sha": commit,
        "retrieved_at": "2026-07-27T00:00:00Z",
        "license": "MIT",
        "license_url": f"https://github.com/example/project/blob/{commit}/LICENSE",
        "upstream_path": "docs.md",
        "upstream_content_hash": "sha256:" + "b" * 64,
        "snapshot_path": "knowledge/corpus/snapshots/example/docs.md",
        "content_hash": "sha256:" + "c" * 64,
        "parser_version": "sage.extractive-markdown@1",
        "modality": "text",
        "review_status": "approved",
    }


def _case_payload() -> dict[str, Any]:
    return {
        "case_id": "case-001",
        "leakage_group": "group-001",
        "query": "What does the source say?",
        "category": "exact_api",
        "dataset_split": "dev",
        "answerable": True,
        "modality": "text",
        "required_sources": ["example-doc"],
        "required_passages": [
            {"corpus_id": "example-doc", "anchor": "Core behavior", "relevance": 3}
        ],
        "required_claims": ["The source states the core behavior."],
        "forbidden_claims": [],
        "gold_page": None,
        "gold_bbox": None,
        "provenance": "human_curated",
    }


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
