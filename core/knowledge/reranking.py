"""Pinned local Cross-Encoder adapter for bounded PR-6 reranking experiments."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from threading import RLock
from typing import Any

FASTEMBED_RERANK_RUNTIME_REVISION = "fastembed-0.8.0-cross-encoder"
DEFAULT_CROSS_ENCODER_MODEL = "BAAI/bge-reranker-base"
DEFAULT_CROSS_ENCODER_REPOSITORY = "BAAI/bge-reranker-base"
DEFAULT_CROSS_ENCODER_MODEL_REVISION = "2cfc18c9415c912f9d8155881c133215df768a70"
_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True, slots=True)
class FastEmbedCrossEncoderConfig:
    model: str
    repository: str
    model_revision: str
    cache_dir: Path
    local_files_only: bool = False

    def __post_init__(self) -> None:
        model = self.model.strip()
        repository = self.repository.strip()
        revision = self.model_revision.strip().casefold()
        if not model or len(model) > 200:
            raise ValueError("Cross-Encoder model is required")
        if (
            len(repository) > 200
            or repository.count("/") != 1
            or any(character.isspace() for character in repository)
        ):
            raise ValueError("Cross-Encoder repository must use owner/name format")
        if _COMMIT_PATTERN.fullmatch(revision) is None:
            raise ValueError(
                "Cross-Encoder model revision must be a 40-character hexadecimal commit"
            )
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "repository", repository)
        object.__setattr__(self, "model_revision", revision)
        object.__setattr__(self, "cache_dir", self.cache_dir.expanduser().resolve())


class FastEmbedCrossEncoderProvider:
    """Local ONNX reranker loaded from an immutable Hugging Face snapshot."""

    estimated_cost_usd: float | None = 0.0

    def __init__(self, config: FastEmbedCrossEncoderConfig) -> None:
        self.config = config
        self.model_id = f"fastembed-rerank.{config.model}"
        self.model_revision = f"{config.model_revision}+{FASTEMBED_RERANK_RUNTIME_REVISION}"
        self._model: Any | None = None
        self._lock = RLock()

    def prepare(self) -> None:
        self._load_model()

    def rerank(self, query: str, documents: tuple[str, ...]) -> tuple[float, ...]:
        if not documents:
            return ()
        try:
            raw = tuple(float(value) for value in self._load_model().rerank(query, documents))
            scores = tuple(_sigmoid(value) for value in raw)
        except (TypeError, ValueError, ArithmeticError) as exc:
            raise RuntimeError("FastEmbed Cross-Encoder reranking failed") from exc
        if len(scores) != len(documents) or any(not math.isfinite(value) for value in scores):
            raise RuntimeError("FastEmbed Cross-Encoder returned invalid scores")
        return scores

    def _load_model(self) -> Any:
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                fastembed = import_module("fastembed")
                if str(fastembed.__version__) != "0.8.0":
                    raise RuntimeError("unsupported FastEmbed runtime version")
                huggingface_hub = import_module("huggingface_hub")
                snapshot_path = huggingface_hub.snapshot_download(
                    repo_id=self.config.repository,
                    revision=self.config.model_revision,
                    cache_dir=str(self.config.cache_dir),
                    local_files_only=self.config.local_files_only,
                )
                cross_encoder = import_module("fastembed.rerank.cross_encoder")
                self._model = cross_encoder.TextCrossEncoder(
                    model_name=self.config.model,
                    cache_dir=str(self.config.cache_dir),
                    specific_model_path=str(snapshot_path),
                )
            except Exception as exc:
                raise RuntimeError("FastEmbed Cross-Encoder initialization failed") from exc
        return self._model


def _sigmoid(value: float) -> float:
    if value >= 0.0:
        return 1.0 / (1.0 + math.exp(-value))
    exponential = math.exp(value)
    return exponential / (1.0 + exponential)


__all__ = [
    "DEFAULT_CROSS_ENCODER_MODEL",
    "DEFAULT_CROSS_ENCODER_MODEL_REVISION",
    "DEFAULT_CROSS_ENCODER_REPOSITORY",
    "FASTEMBED_RERANK_RUNTIME_REVISION",
    "FastEmbedCrossEncoderConfig",
    "FastEmbedCrossEncoderProvider",
]
