"""把可执行 ToolBundle 投影为本轮可持久化的能力契约。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sage_harness import McpLifecycleSnapshot

from core.coding.skills import SkillLifecycleSnapshot

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
    mcp_lifecycle: McpLifecycleSnapshot | None = None
    skill_lifecycle: SkillLifecycleSnapshot | None = None
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
        if self.skill_lifecycle is not None:
            lifecycle_active = bool(self.skill_lifecycle.activation_ref)
            if lifecycle_active != self.skill_scope_active:
                raise ValueError("skill lifecycle activation does not match tool scope")
            if self.skill_lifecycle.allowed_tools != self.skill_allowlist:
                raise ValueError("skill lifecycle allowlist does not match tool scope")
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
            mcp_lifecycle=bundle.mcp_lifecycle,
            skill_lifecycle=bundle.skill_lifecycle,
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> ToolBundleSnapshot:
        """从 Plan JSON 重建完整能力快照并校验嵌套生命周期 hash。"""
        resident = _string_sequence(value.get("resident_ids"), "resident_ids")
        deferred = _string_sequence(value.get("deferred_ids"), "deferred_ids")
        allowlist = _string_sequence(value.get("skill_allowlist", ()), "skill_allowlist")
        raw_mcp = value.get("mcp_lifecycle")
        raw_skill = value.get("skill_lifecycle")
        if raw_mcp is not None and not isinstance(raw_mcp, Mapping):
            raise ValueError("mcp lifecycle must be an object")
        if raw_skill is not None and not isinstance(raw_skill, Mapping):
            raise ValueError("skill lifecycle must be an object")
        count = value.get("capability_count")
        if isinstance(count, bool) or not isinstance(count, int):
            raise ValueError("capability_count must be an integer")
        return cls(
            catalog_hash=str(value.get("catalog_hash", "")),
            capability_revision=str(value.get("capability_revision", "")),
            resident_ids=resident,
            deferred_ids=deferred,
            capability_count=count,
            skill_scope_active=value.get("skill_scope_active") is True,
            skill_allowlist=allowlist,
            mcp_lifecycle=(
                McpLifecycleSnapshot.from_mapping(raw_mcp) if raw_mcp is not None else None
            ),
            skill_lifecycle=(
                SkillLifecycleSnapshot.from_mapping(raw_skill) if raw_skill is not None else None
            ),
            snapshot_hash=str(value.get("snapshot_hash", "")),
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
            "mcp_lifecycle": (
                self.mcp_lifecycle.as_dict() if self.mcp_lifecycle is not None else None
            ),
            "skill_lifecycle": (
                self.skill_lifecycle.as_dict() if self.skill_lifecycle is not None else None
            ),
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
            "mcp_lifecycle": (
                self.mcp_lifecycle.as_dict() if self.mcp_lifecycle is not None else None
            ),
            "skill_lifecycle": (
                self.skill_lifecycle.as_dict() if self.skill_lifecycle is not None else None
            ),
        }


def _snapshot_hash(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _string_sequence(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        raise ValueError(f"{field} must be a sequence")
    return tuple(str(item) for item in value)


__all__ = ["ToolBundleSnapshot"]
