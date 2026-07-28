"""Strict contracts for versioned, review-approved RAG corpus and eval assets."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from core.knowledge.parsing.markdown import MarkdownParser
from core.knowledge.parsing.types import ParseRequest

_SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"
_COMMIT_PATTERN = r"^[0-9a-f]{40}$"
_ID_PATTERN = r"^[a-z0-9][a-z0-9._-]{2,79}$"


class CorpusManifestEntry(BaseModel):
    """One approved, immutable snapshot derived from an official source."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    corpus_id: str = Field(pattern=_ID_PATTERN)
    source_url: str
    source_type: Literal["official_docs", "official_github"]
    project: str = Field(min_length=1, max_length=80)
    version: str = Field(min_length=1, max_length=120)
    commit_sha: str = Field(pattern=_COMMIT_PATTERN)
    retrieved_at: AwareDatetime
    license: Literal["MIT", "PostgreSQL"]
    license_url: str
    upstream_path: str = Field(min_length=1, max_length=500)
    upstream_content_hash: str = Field(pattern=_SHA256_PATTERN)
    snapshot_path: str
    content_hash: str = Field(pattern=_SHA256_PATTERN)
    parser_version: Literal["sage.extractive-markdown@1"]
    modality: Literal["text", "code", "table", "mixed"]
    review_status: Literal["approved"]

    @field_validator("source_url", "license_url")
    @classmethod
    def _validate_https_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("source URLs must use absolute HTTPS")
        return value

    @field_validator("snapshot_path")
    @classmethod
    def _validate_snapshot_path(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("snapshot_path must be a repository-relative path")
        if path.suffix.lower() != ".md":
            raise ValueError("snapshot_path must point to Markdown")
        return path.as_posix()

    @field_validator("upstream_path")
    @classmethod
    def _validate_upstream_path(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("upstream_path must be relative")
        return path.as_posix()

    @model_validator(mode="after")
    def _validate_pin_and_timestamp(self) -> CorpusManifestEntry:
        if self.commit_sha not in self.source_url:
            raise ValueError("source_url must contain commit_sha")
        if self.retrieved_at.utcoffset() != timedelta(0):
            raise ValueError("retrieved_at must use UTC")
        return self


class EvalPassage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    corpus_id: str = Field(pattern=_ID_PATTERN)
    anchor: str = Field(min_length=1, max_length=300)
    relevance: int = Field(ge=1, le=3)


class EvalCase(BaseModel):
    """One human-reviewed case; grouped variants must stay in one split."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    case_id: str = Field(pattern=_ID_PATTERN)
    leakage_group: str = Field(pattern=_ID_PATTERN)
    query: str = Field(min_length=3, max_length=1000)
    category: Literal[
        "exact_api",
        "semantic_paraphrase",
        "multi_document",
        "version_conflict",
        "unanswerable",
        "hard_negative",
        "code_table",
    ]
    dataset_split: Literal["dev", "calibration", "test"]
    answerable: bool
    modality: Literal["text", "code", "table", "image", "mixed"]
    required_sources: tuple[str, ...]
    required_passages: tuple[EvalPassage, ...]
    required_claims: tuple[str, ...]
    forbidden_claims: tuple[str, ...]
    gold_page: int | None = Field(ge=1)
    gold_bbox: tuple[float, float, float, float] | None
    provenance: Literal["human_curated"]

    @field_validator("required_claims", "forbidden_claims")
    @classmethod
    def _validate_claims(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item for item in value) or len(value) != len(set(value)):
            raise ValueError("claims must be non-empty and unique")
        return value

    @model_validator(mode="after")
    def _validate_judgments(self) -> EvalCase:
        if len(self.required_sources) != len(set(self.required_sources)):
            raise ValueError("required_sources must be unique")
        passage_keys = [(item.corpus_id, item.anchor) for item in self.required_passages]
        if len(passage_keys) != len(set(passage_keys)):
            raise ValueError("required_passages must be unique")
        passage_ids = tuple(item.corpus_id for item in self.required_passages)
        if set(passage_ids) != set(self.required_sources):
            raise ValueError("required_sources must match required_passages")
        if self.answerable:
            if not self.required_sources or not self.required_passages or not self.required_claims:
                raise ValueError("answerable cases require sources, passages, and claims")
        else:
            if self.required_sources or self.required_passages or self.required_claims:
                raise ValueError("unanswerable cases cannot contain required evidence")
            if not self.forbidden_claims:
                raise ValueError("unanswerable cases require forbidden claims")
        if self.gold_bbox is not None:
            if self.gold_page is None:
                raise ValueError("gold_bbox requires gold_page")
            x1, y1, x2, y2 = self.gold_bbox
            if x2 <= x1 or y2 <= y1:
                raise ValueError("gold_bbox must have positive area")
        if self.gold_page is not None and self.modality not in {"image", "mixed"}:
            raise ValueError("gold_page is reserved for visual evidence")
        return self


class EvalDatasetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    dataset_id: str = Field(pattern=_ID_PATTERN)
    dataset_revision: str = Field(min_length=1, max_length=120)
    corpus_manifest: str
    corpus_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    cases: str
    cases_sha256: str = Field(pattern=_SHA256_PATTERN)
    split_counts: dict[Literal["dev", "calibration", "test"], int]
    frozen_test: Literal[True]
    reviewed_at: AwareDatetime

    @field_validator("corpus_manifest", "cases")
    @classmethod
    def _validate_relative_path(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("dataset paths must be repository-relative")
        return path.as_posix()

    @model_validator(mode="after")
    def _validate_manifest(self) -> EvalDatasetManifest:
        if set(self.split_counts) != {"dev", "calibration", "test"}:
            raise ValueError("split_counts must define dev, calibration, and test")
        if any(isinstance(value, bool) or value < 1 for value in self.split_counts.values()):
            raise ValueError("split_counts must be positive integers")
        if self.reviewed_at.utcoffset() != timedelta(0):
            raise ValueError("reviewed_at must use UTC")
        return self


@dataclass(frozen=True, slots=True)
class VersionedKnowledgeDataset:
    manifest: EvalDatasetManifest
    corpus: tuple[CorpusManifestEntry, ...]
    cases: tuple[EvalCase, ...]

    @property
    def split_counts(self) -> dict[str, int]:
        return {
            split: sum(case.dataset_split == split for case in self.cases)
            for split in ("dev", "calibration", "test")
        }


def load_versioned_dataset(repo_root: Path, dataset_path: Path) -> VersionedKnowledgeDataset:
    """Load a hash-bound dataset and reject any source, split, or path drift."""

    root = repo_root.resolve()
    manifest_path = _inside(root, dataset_path)
    manifest = EvalDatasetManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    corpus_path = _inside(root, Path(manifest.corpus_manifest))
    cases_path = _inside(root, Path(manifest.cases))
    _validate_digest(corpus_path, manifest.corpus_manifest_sha256)
    _validate_digest(cases_path, manifest.cases_sha256)
    corpus = _load_jsonl(corpus_path, CorpusManifestEntry)
    cases = _load_jsonl(cases_path, EvalCase)
    _validate_corpus(root, corpus)
    validate_eval_dataset(corpus, cases, repo_root=root)
    dataset = VersionedKnowledgeDataset(manifest=manifest, corpus=corpus, cases=cases)
    if dataset.split_counts != dict(manifest.split_counts):
        raise ValueError("eval split counts do not match the dataset manifest")
    return dataset


def validate_eval_dataset(
    corpus: tuple[CorpusManifestEntry, ...],
    cases: tuple[EvalCase, ...],
    *,
    repo_root: Path | None = None,
) -> None:
    if not corpus or not cases:
        raise ValueError("versioned corpus and eval cases must not be empty")
    corpus_ids = [item.corpus_id for item in corpus]
    if len(corpus_ids) != len(set(corpus_ids)):
        raise ValueError("corpus ids must be unique")
    case_ids = [item.case_id for item in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("eval case ids must be unique")
    normalized_queries = [_normalized_query(item.query) for item in cases]
    if len(normalized_queries) != len(set(normalized_queries)):
        raise ValueError("eval queries must be unique after normalization")
    known = set(corpus_ids)
    referenced = {
        corpus_id
        for case in cases
        for corpus_id in (*case.required_sources, *(p.corpus_id for p in case.required_passages))
    }
    unknown = sorted(referenced - known)
    if unknown:
        raise ValueError(f"eval cases reference unknown corpus ids: {', '.join(unknown[:5])}")
    group_splits: dict[str, set[str]] = {}
    for case in cases:
        group_splits.setdefault(case.leakage_group, set()).add(case.dataset_split)
    leaked = sorted(group for group, splits in group_splits.items() if len(splits) != 1)
    if leaked:
        raise ValueError(f"eval leakage group crosses splits: {', '.join(leaked[:5])}")
    if repo_root is not None:
        _validate_passage_anchors(repo_root.resolve(), corpus, cases)


def _load_jsonl(path: Path, model: type[CorpusManifestEntry] | type[EvalCase]) -> tuple[Any, ...]:
    records: list[Any] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(model.model_validate_json(line))
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid {path.name} record at line {line_number}") from exc
    return tuple(records)


def _validate_corpus(root: Path, corpus: tuple[CorpusManifestEntry, ...]) -> None:
    snapshot_paths = [item.snapshot_path for item in corpus]
    if len(snapshot_paths) != len(set(snapshot_paths)):
        raise ValueError("corpus snapshot paths must be unique")
    for item in corpus:
        _validate_digest(_inside(root, Path(item.snapshot_path)), item.content_hash)


def _validate_passage_anchors(
    root: Path,
    corpus: tuple[CorpusManifestEntry, ...],
    cases: tuple[EvalCase, ...],
) -> None:
    available: dict[str, set[str]] = {}
    parser = MarkdownParser()
    for item in corpus:
        snapshot = _inside(root, Path(item.snapshot_path))
        document = parser.parse(
            ParseRequest(
                source_id=item.corpus_id,
                relative_path=item.snapshot_path,
                source_revision=item.content_hash,
                media_type="text/markdown",
                payload=snapshot.read_bytes(),
            )
        )
        available[item.corpus_id] = {
            block.heading_path[-1] for block in document.blocks if block.heading_path
        }
    for case in cases:
        for passage in case.required_passages:
            if passage.anchor not in available[passage.corpus_id]:
                raise ValueError(
                    "eval passage anchor does not exist: " f"{passage.corpus_id}#{passage.anchor}"
                )


def _inside(root: Path, path: Path) -> Path:
    target = path.resolve() if path.is_absolute() else (root / path).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("dataset path escapes repository root") from exc
    return target


def _validate_digest(path: Path, expected: str) -> None:
    if not path.is_file():
        raise ValueError(f"versioned dataset file is missing: {path.name}")
    actual = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(f"versioned dataset hash changed: {path.name}")


def _normalized_query(value: str) -> str:
    return " ".join(value.casefold().split())


__all__ = [
    "CorpusManifestEntry",
    "EvalCase",
    "EvalDatasetManifest",
    "EvalPassage",
    "VersionedKnowledgeDataset",
    "load_versioned_dataset",
    "validate_eval_dataset",
]
