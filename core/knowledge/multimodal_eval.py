"""Versioned contract for the PR-7 multimodal evidence evaluation set."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.knowledge.parsing import BlockKind

_ID_PATTERN = r"^[a-z0-9][a-z0-9._-]{2,79}$"


class MultimodalEvalCase(BaseModel):
    """One fixture-backed structural/citation case; never used to tune retrieval."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    case_id: str = Field(pattern=_ID_PATTERN)
    leakage_group: str = Field(pattern=_ID_PATTERN)
    dataset_split: Literal["dev", "calibration", "test"]
    fixture_id: Literal["docx_ordered", "png_metadata", "png_no_description", "qwen_region"]
    layer: Literal["L1", "L2"]
    query: str = Field(min_length=3, max_length=500)
    answerable: Literal[True]
    required_text: str = Field(min_length=1, max_length=2_000)
    expected_block_kind: BlockKind
    gold_page: int | None = Field(default=None, ge=1)
    gold_bbox: tuple[float, float, float, float] | None = None
    expected_media_ref: str | None = Field(default=None, max_length=500)
    expected_parser_id: str = Field(min_length=1, max_length=160)
    expected_parser_version: str = Field(min_length=1, max_length=80)
    provenance: Literal["project_authored_fixture"]

    @field_validator("gold_bbox")
    @classmethod
    def _validate_bbox(
        cls, value: tuple[float, float, float, float] | None
    ) -> tuple[float, float, float, float] | None:
        if value is None:
            return None
        x1, y1, x2, y2 = value
        if any(not 0.0 <= item <= 1.0 for item in value) or x2 <= x1 or y2 <= y1:
            raise ValueError("gold_bbox must be a positive normalized region")
        return value

    @model_validator(mode="after")
    def _validate_layer_contract(self) -> MultimodalEvalCase:
        if self.fixture_id == "qwen_region" and self.layer != "L2":
            raise ValueError("qwen_region cases must use L2")
        if self.fixture_id != "qwen_region" and self.layer != "L1":
            raise ValueError("local parser fixtures must use L1")
        if self.gold_bbox is not None and self.gold_page is None:
            raise ValueError("gold_bbox requires gold_page")
        if self.expected_block_kind == "media" and not self.expected_media_ref:
            raise ValueError("media cases require expected_media_ref")
        return self


def load_multimodal_cases(path: Path) -> tuple[MultimodalEvalCase, ...]:
    cases = tuple(
        MultimodalEvalCase.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    ids = [case.case_id for case in cases]
    groups = [case.leakage_group for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("multimodal case ids must be unique")
    if len(groups) != len(set(groups)):
        raise ValueError("multimodal leakage groups must be unique")
    counts = {
        split: sum(case.dataset_split == split for case in cases)
        for split in ("dev", "calibration", "test")
    }
    if counts != {"dev": 6, "calibration": 3, "test": 3}:
        raise ValueError(f"multimodal split counts must be 6/3/3, got {counts}")
    return cases


def multimodal_cases_sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def multimodal_case_dict(case: MultimodalEvalCase) -> dict[str, object]:
    return case.model_dump()


__all__ = [
    "MultimodalEvalCase",
    "load_multimodal_cases",
    "multimodal_case_dict",
    "multimodal_cases_sha256",
]
