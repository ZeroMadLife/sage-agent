"""Version-bound relevance policy for evidence retrieval and abstention."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal


class KnowledgeRelevancePolicyError(RuntimeError):
    """A configured relevance policy is incompatible with the active index."""


@dataclass(frozen=True, slots=True)
class KnowledgeRelevancePolicy:
    """Accept evidence only when an absolute retrieval score clears calibration."""

    benchmark_id: str
    benchmark_revision: str
    corpus_revision: str
    embedding_model: str
    embedding_revision: str
    top_k: int
    min_sparse_score: float | None
    min_dense_score: float | None
    min_hybrid_score: float | None = None
    minimum_answerable_recall_ratio: float = 0.9
    schema_version: int = 1

    def __post_init__(self) -> None:
        required = (
            self.benchmark_id,
            self.benchmark_revision,
            self.corpus_revision,
            self.embedding_model,
            self.embedding_revision,
        )
        if any(not value.strip() for value in required):
            raise ValueError("knowledge relevance policy metadata is required")
        if isinstance(self.schema_version, bool) or self.schema_version not in {1, 2}:
            raise ValueError("unsupported knowledge relevance policy schema")
        if self.schema_version == 1 and self.min_hybrid_score is not None:
            raise ValueError("hybrid score thresholds require policy schema version 2")
        if isinstance(self.top_k, bool) or self.top_k < 1 or self.top_k > 50:
            raise ValueError("knowledge relevance policy top_k must be between 1 and 50")
        if (
            self.min_sparse_score is None
            and self.min_dense_score is None
            and self.min_hybrid_score is None
        ):
            raise ValueError("knowledge relevance policy requires a score threshold")
        for value in (self.min_sparse_score, self.min_dense_score, self.min_hybrid_score):
            if value is not None and (
                isinstance(value, bool) or not math.isfinite(value) or value < 0.0
            ):
                raise ValueError(
                    "knowledge relevance score threshold must be finite and non-negative"
                )
        if isinstance(self.minimum_answerable_recall_ratio, bool) or not (
            0.0 < self.minimum_answerable_recall_ratio <= 1.0
        ):
            raise ValueError("minimum answerable recall ratio must be in (0, 1]")

    @property
    def policy_id(self) -> str:
        payload = asdict(self)
        if self.schema_version == 1:
            payload.pop("min_hybrid_score")
        encoded = json.dumps(
            payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return "krp_" + hashlib.sha256(encoded).hexdigest()[:16]

    def assert_provider(self, *, model_id: str, model_revision: str) -> None:
        if (model_id, model_revision) != (self.embedding_model, self.embedding_revision):
            raise KnowledgeRelevancePolicyError(
                "knowledge relevance policy does not match embedding provider"
            )

    def assert_corpus(self, *, corpus_revision: str) -> None:
        if corpus_revision != self.corpus_revision:
            raise KnowledgeRelevancePolicyError(
                "knowledge relevance policy does not match indexed corpus"
            )

    def accepts(
        self,
        *,
        sparse_score: float | None,
        dense_score: float | None,
        hybrid_score: float | None = None,
        retrieval_mode: Literal["sparse", "dense", "hybrid"] | None = None,
    ) -> bool:
        sparse_ok = (
            self.min_sparse_score is not None
            and sparse_score is not None
            and sparse_score >= self.min_sparse_score
        )
        dense_ok = (
            self.min_dense_score is not None
            and dense_score is not None
            and dense_score >= self.min_dense_score
        )
        hybrid_ok = (
            self.min_hybrid_score is not None
            and hybrid_score is not None
            and hybrid_score >= self.min_hybrid_score
        )
        if retrieval_mode == "sparse":
            return sparse_ok
        if retrieval_mode == "dense":
            return dense_ok
        if retrieval_mode == "hybrid" and self.min_hybrid_score is not None:
            return hybrid_ok
        return sparse_ok or dense_ok

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        if self.schema_version == 1:
            payload.pop("min_hybrid_score")
        return {"policy_id": self.policy_id, **payload}

    @classmethod
    def from_dict(cls, raw: Any) -> KnowledgeRelevancePolicy:
        if not isinstance(raw, dict):
            raise TypeError("knowledge relevance policy must be an object")
        schema_version = _integer(raw.get("schema_version"), "schema_version")
        expected = {
            "policy_id",
            "benchmark_id",
            "benchmark_revision",
            "corpus_revision",
            "embedding_model",
            "embedding_revision",
            "top_k",
            "min_sparse_score",
            "min_dense_score",
            "minimum_answerable_recall_ratio",
            "schema_version",
        }
        if schema_version == 2:
            expected.add("min_hybrid_score")
        if set(raw) != expected:
            raise ValueError("knowledge relevance policy fields do not match the schema")
        policy = cls(
            benchmark_id=str(raw["benchmark_id"]),
            benchmark_revision=str(raw["benchmark_revision"]),
            corpus_revision=str(raw["corpus_revision"]),
            embedding_model=str(raw["embedding_model"]),
            embedding_revision=str(raw["embedding_revision"]),
            top_k=_integer(raw["top_k"], "top_k"),
            min_sparse_score=_optional_float(raw["min_sparse_score"]),
            min_dense_score=_optional_float(raw["min_dense_score"]),
            min_hybrid_score=_optional_float(raw.get("min_hybrid_score")),
            minimum_answerable_recall_ratio=_number(
                raw["minimum_answerable_recall_ratio"],
                "minimum_answerable_recall_ratio",
            ),
            schema_version=schema_version,
        )
        if str(raw["policy_id"]) != policy.policy_id:
            raise ValueError("knowledge relevance policy id does not match its contents")
        return policy


def load_relevance_policy(path: Path) -> KnowledgeRelevancePolicy:
    return KnowledgeRelevancePolicy.from_dict(json.loads(path.read_text(encoding="utf-8")))


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError("knowledge relevance score threshold must be numeric")
    return float(value)


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"knowledge relevance policy {name} must be an integer")
    return int(value)


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"knowledge relevance policy {name} must be numeric")
    return float(value)
