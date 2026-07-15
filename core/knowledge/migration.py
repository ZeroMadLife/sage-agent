"""Stable contracts for one-time historical proposal migration."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class KnowledgeMigrationItem:
    proposal_id: str
    source_root_id: str
    source_relative_path: str
    source_revision: str
    disposition: str
    reason_codes: tuple[str, ...]
    parser_id: str | None = None
    parser_version: str | None = None
    current_source_revision: str | None = None


@dataclass(frozen=True, slots=True)
class KnowledgeMigrationPlan:
    plan_id: str
    items: tuple[KnowledgeMigrationItem, ...]

    @property
    def total(self) -> int:
        return len(self.items)

    def count(self, disposition: str) -> int:
        return sum(item.disposition == disposition for item in self.items)


@dataclass(frozen=True, slots=True)
class KnowledgeMigrationResultItem:
    proposal_id: str
    status: str
    replacement_proposal_id: str | None = None
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class KnowledgeMigrationResult:
    plan_id: str
    items: tuple[KnowledgeMigrationResultItem, ...]

    @property
    def status(self) -> str:
        return "completed_with_errors" if self.count("error") else "completed"

    def count(self, status: str) -> int:
        return sum(item.status == status for item in self.items)


def build_migration_plan(items: list[KnowledgeMigrationItem]) -> KnowledgeMigrationPlan:
    ordered = tuple(sorted(items, key=lambda item: item.proposal_id))
    payload = json.dumps(
        [asdict(item) for item in ordered],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    plan_id = "kmig_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return KnowledgeMigrationPlan(plan_id=plan_id, items=ordered)
