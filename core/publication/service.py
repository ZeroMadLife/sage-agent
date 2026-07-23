"""Validation and deterministic export for approved public package candidates."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from public_agent.corpus import PublicPackage

from .repository import PublicationCandidateConflictError, PublicationCandidateRepository
from .types import PublicationCandidate

_SAFE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_MAX_PACKAGE_BYTES = 2 * 1024 * 1024
_MAX_REASON_CHARS = 1000
_MAX_EVIDENCE_REFS = 64
_MAX_EVIDENCE_REF_CHARS = 1000


class PublicationValidationError(ValueError):
    """A candidate cannot cross the explicit public disclosure boundary."""


class PublicationCandidateService:
    def __init__(self, repository: PublicationCandidateRepository) -> None:
        self.repository = repository

    async def create(
        self,
        *,
        owner_id: str,
        package: Mapping[str, Any],
        reason: str,
        evidence_refs: Sequence[str],
    ) -> PublicationCandidate:
        normalized, validated = _validated_package(package)
        normalized_reason = reason.strip()
        if len(normalized_reason) > _MAX_REASON_CHARS:
            raise PublicationValidationError("publication reason is too long")
        refs = _evidence_refs(evidence_refs)
        return await self.repository.create(
            owner_id=owner_id,
            package_id=validated.package_id,
            package_revision=validated.revision,
            package_digest=validated.digest,
            package=normalized,
            reason=normalized_reason,
            evidence_refs=refs,
        )

    async def stage_artifact(self, candidate_id: str, *, owner_id: str) -> dict[str, Any]:
        candidate = await self.repository.get(candidate_id, owner_id=owner_id)
        if candidate.status != "approved":
            raise PublicationCandidateConflictError(
                "publication candidate must be approved before stage export"
            )
        normalized, validated = _validated_package(candidate.package)
        if validated.digest != candidate.package_digest:
            raise PublicationCandidateConflictError("publication candidate digest changed")
        return {
            "candidate_id": candidate.candidate_id,
            "candidate_revision": candidate.revision,
            "package_id": candidate.package_id,
            "package_revision": candidate.package_revision,
            "package_digest": candidate.package_digest,
            "stage_request": {"action": "stage", "package": normalized},
        }


def _validated_package(package: Mapping[str, Any]) -> tuple[dict[str, Any], PublicPackage]:
    normalized = json.loads(
        json.dumps(dict(package), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    encoded = json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if len(encoded) > _MAX_PACKAGE_BYTES:
        raise PublicationValidationError("public package exceeds 2 MiB")
    try:
        validated = PublicPackage.from_payload(normalized)
    except ValueError as exc:
        raise PublicationValidationError(str(exc)) from exc
    if validated.package_id != "sage-public":
        raise PublicationValidationError("public package_id must be sage-public")
    for field, value in (("package_id", validated.package_id), ("revision", validated.revision)):
        if _SAFE_REF.fullmatch(value) is None:
            raise PublicationValidationError(f"public package {field} format is invalid")
    return normalized, validated


def _evidence_refs(values: Sequence[str]) -> tuple[str, ...]:
    if len(values) > _MAX_EVIDENCE_REFS:
        raise PublicationValidationError("too many publication evidence refs")
    normalized: list[str] = []
    for value in values:
        item = str(value).strip()
        if not item or len(item) > _MAX_EVIDENCE_REF_CHARS:
            raise PublicationValidationError("publication evidence ref is invalid")
        if item not in normalized:
            normalized.append(item)
    return tuple(normalized)


__all__ = ["PublicationCandidateService", "PublicationValidationError"]
