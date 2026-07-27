from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from core.knowledge.reranking import (
    FASTEMBED_RERANK_RUNTIME_REVISION,
    FastEmbedCrossEncoderConfig,
    FastEmbedCrossEncoderProvider,
)


def test_fastembed_cross_encoder_pins_snapshot_and_validates_scores(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    downloads: list[dict[str, Any]] = []
    model_calls: list[dict[str, Any]] = []

    def snapshot_download(**kwargs: Any) -> str:
        downloads.append(kwargs)
        return str(tmp_path / "pinned-model")

    class FakeTextCrossEncoder:
        def __init__(self, **kwargs: Any) -> None:
            model_calls.append(kwargs)

        def rerank(self, query: str, documents: tuple[str, ...]) -> list[float]:
            assert query == "query"
            return [float(index) for index, _document in enumerate(documents)]

    modules = {
        "fastembed": SimpleNamespace(__version__="0.8.0"),
        "fastembed.rerank.cross_encoder": SimpleNamespace(TextCrossEncoder=FakeTextCrossEncoder),
        "huggingface_hub": SimpleNamespace(snapshot_download=snapshot_download),
    }
    monkeypatch.setattr("core.knowledge.reranking.import_module", lambda name: modules[name])
    revision = "a" * 40
    provider = FastEmbedCrossEncoderProvider(
        FastEmbedCrossEncoderConfig(
            model="BAAI/bge-reranker-base",
            repository="BAAI/bge-reranker-base",
            model_revision=revision,
            cache_dir=tmp_path / "cache",
            local_files_only=True,
        )
    )

    assert provider.rerank("query", ("first", "second")) == pytest.approx((0.5, 0.7310585786))
    assert provider.model_revision == f"{revision}+{FASTEMBED_RERANK_RUNTIME_REVISION}"
    assert downloads == [
        {
            "repo_id": "BAAI/bge-reranker-base",
            "revision": revision,
            "cache_dir": str(tmp_path / "cache"),
            "local_files_only": True,
        }
    ]
    assert model_calls == [
        {
            "model_name": "BAAI/bge-reranker-base",
            "cache_dir": str(tmp_path / "cache"),
            "specific_model_path": str(tmp_path / "pinned-model"),
        }
    ]


@pytest.mark.parametrize("revision", ["latest", "abc123", "g" * 40])
def test_fastembed_cross_encoder_requires_full_snapshot_revision(
    revision: str,
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="40-character hexadecimal"):
        FastEmbedCrossEncoderConfig(
            model="BAAI/bge-reranker-base",
            repository="BAAI/bge-reranker-base",
            model_revision=revision,
            cache_dir=tmp_path,
        )
