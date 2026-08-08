"""把可执行 ToolBundle 投影为本轮可持久化的能力契约。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.harness.tools_adapter import CodingToolBundle


@dataclass(frozen=True, slots=True)
class ToolBundleSnapshot:
    """一次 Turn 可见的能力快照，不持有工具、连接或执行闭包。"""

    catalog_hash: str
    capability_revision: str
    resident_ids: tuple[str, ...]
    deferred_ids: tuple[str, ...]
    capability_count: int
    skill_scope_active: bool = False
    skill_allowlist: tuple[str, ...] = ()
    snapshot_hash: str = ""

    def __post_init__(self) -> None:
        """校验并固定顺序，避免快照被非 canonical 数据污染。"""
        for name, value in (
            ("catalog_hash", self.catalog_hash),
            ("capability_revision", self.capability_revision),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be empty")
        if self.capability_count < 0:
            raise ValueError("capability_count must not be negative")
        for name, values in (
            ("resident_ids", self.resident_ids),
            ("deferred_ids", self.deferred_ids),
            ("skill_allowlist", self.skill_allowlist),
        ):
            if tuple(sorted(set(values))) != values:
                raise ValueError(f"{name} must be sorted and unique")
            if any(not isinstance(item, str) or not item.strip() for item in values):
                raise ValueError(f"{name} must contain non-empty strings")
        if set(self.resident_ids) & set(self.deferred_ids):
            raise ValueError("resident and deferred capability ids must be disjoint")
        if not self.skill_scope_active and self.skill_allowlist:
            raise ValueError("inactive skill scope cannot contain an allowlist")
        expected_hash = _snapshot_hash(self._hash_payload())
        if self.snapshot_hash and self.snapshot_hash != expected_hash:
            raise ValueError("tool bundle snapshot hash mismatch")
        object.__setattr__(self, "snapshot_hash", expected_hash)

    @classmethod
    def from_runtime_bundle(
        cls,
        bundle: CodingToolBundle,
    ) -> ToolBundleSnapshot:
        """从 Runtime ToolBundle 提取稳定元数据，不复制执行对象。"""
        selection_index = bundle.deferred_setup.selection_index
        deferred_ids = (
            tuple(
                sorted(
                    descriptor.capability_id
                    for descriptor in selection_index.registry.list()
                    if descriptor.deferred
                )
            )
            if selection_index is not None
            else ()
        )
        all_ids = set(bundle.capability_ids_by_tool_name.values())
        resident_ids = tuple(sorted(all_ids - set(deferred_ids)))
        allowlist = tuple(sorted(bundle.active_skill_allowed_tools or ()))
        return cls(
            catalog_hash=bundle.deferred_setup.catalog_hash or "resident-only",
            capability_revision=bundle.capability_revision,
            resident_ids=resident_ids,
            deferred_ids=deferred_ids,
            capability_count=bundle.capability_count,
            skill_scope_active=bundle.active_skill_allowed_tools is not None,
            skill_allowlist=allowlist,
        )

    def as_dict(self) -> dict[str, Any]:
        """返回独立的非正文字典，供 Plan/Receipt 适配器使用。"""
        return {
            "catalog_hash": self.catalog_hash,
            "capability_revision": self.capability_revision,
            "resident_ids": list(self.resident_ids),
            "deferred_ids": list(self.deferred_ids),
            "capability_count": self.capability_count,
            "skill_scope_active": self.skill_scope_active,
            "skill_allowlist": list(self.skill_allowlist),
            "snapshot_hash": self.snapshot_hash,
        }

    def _hash_payload(self) -> dict[str, object]:
        return {
            "catalog_hash": self.catalog_hash,
            "capability_revision": self.capability_revision,
            "resident_ids": list(self.resident_ids),
            "deferred_ids": list(self.deferred_ids),
            "capability_count": self.capability_count,
            "skill_scope_active": self.skill_scope_active,
            "skill_allowlist": list(self.skill_allowlist),
        }


def _snapshot_hash(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = ["ToolBundleSnapshot"]
