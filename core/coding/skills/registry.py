"""Skill registry and slash command resolution."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from sage_harness import resolve_skill_activation, skill_revision

from core.coding.skills.skill import (
    Skill,
    discover_skills,
    parse_slash_command,
)

_DISCOVERY_PRECEDENCE = (
    "builtin",
    "user",
    "project:skills",
    "project:.coding/skills",
)


class SkillLifecycleError(RuntimeError):
    """Skill catalog 或当前激活引用与冻结 Plan 不一致。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class SkillLifecycleSnapshot:
    """Skill 的发现版本与激活 allowlist，不包含 Skill 正文。"""

    catalog_revision: str
    discovery_precedence: tuple[str, ...] = _DISCOVERY_PRECEDENCE
    activation_ref: str = ""
    activation_revision: str = ""
    allowed_tools: tuple[str, ...] = ()
    snapshot_hash: str = ""

    def __post_init__(self) -> None:
        """校验激活引用并计算 canonical 生命周期 hash。"""
        if not self.catalog_revision.strip():
            raise ValueError("skill catalog revision must be non-empty")
        if not self.discovery_precedence:
            raise ValueError("skill discovery precedence must not be empty")
        if tuple(sorted(set(self.allowed_tools))) != self.allowed_tools:
            raise ValueError("skill allowlist must be sorted and unique")
        if self.activation_ref and not self.activation_revision:
            raise ValueError("active skill requires an activation revision")
        if not self.activation_ref and (self.activation_revision or self.allowed_tools):
            raise ValueError("inactive skill cannot carry activation data")
        expected = _snapshot_hash(self._hash_payload())
        if self.snapshot_hash and self.snapshot_hash != expected:
            raise ValueError("skill lifecycle snapshot hash mismatch")
        object.__setattr__(self, "snapshot_hash", expected)

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SkillLifecycleSnapshot:
        """从 Plan JSON 重建并校验 Skill 生命周期快照。"""
        raw_precedence = value.get("discovery_precedence", _DISCOVERY_PRECEDENCE)
        raw_allowlist = value.get("allowed_tools", ())
        if not isinstance(raw_precedence, list | tuple) or not isinstance(
            raw_allowlist, list | tuple
        ):
            raise ValueError("skill lifecycle sequences are invalid")
        return cls(
            catalog_revision=str(value.get("catalog_revision", "")),
            discovery_precedence=tuple(str(item) for item in raw_precedence),
            activation_ref=str(value.get("activation_ref", "")),
            activation_revision=str(value.get("activation_revision", "")),
            allowed_tools=tuple(str(item) for item in raw_allowlist),
            snapshot_hash=str(value.get("snapshot_hash", "")),
        )

    def as_dict(self) -> dict[str, object]:
        """返回不含 Skill 正文的 JSON 投影。"""
        return {
            "catalog_revision": self.catalog_revision,
            "discovery_precedence": list(self.discovery_precedence),
            "activation_ref": self.activation_ref,
            "activation_revision": self.activation_revision,
            "allowed_tools": list(self.allowed_tools),
            "snapshot_hash": self.snapshot_hash,
        }

    def _hash_payload(self) -> dict[str, object]:
        return {
            "catalog_revision": self.catalog_revision,
            "discovery_precedence": list(self.discovery_precedence),
            "activation_ref": self.activation_ref,
            "activation_revision": self.activation_revision,
            "allowed_tools": list(self.allowed_tools),
        }


def _snapshot_hash(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class SkillRegistry:
    """Resolve slash commands against discovered skills."""

    def __init__(self, root: Path | str, home: Path | str | None = None) -> None:
        self.root = Path(root)
        self.home = Path(home) if home else None
        self._skills: dict[str, Skill] = discover_skills(self.root, home=self.home)
        self._revision = self._catalog_revision()

    @property
    def revision(self) -> str:
        """返回当前已发现 Skill catalog 的稳定 revision。"""
        return self._revision

    @property
    def discovery_precedence(self) -> tuple[str, ...]:
        """返回 catalog 覆盖顺序，供 Plan 固化并在恢复时复核。"""
        return _DISCOVERY_PRECEDENCE

    @property
    def skills(self) -> dict[str, Skill]:
        return dict(self._skills)

    def list(self) -> list[Skill]:
        return [self._skills[name] for name in sorted(self._skills)]

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def resolve(self, text: str) -> tuple[Skill | None, str, str]:
        """Return ``(skill, command, arguments)`` for a slash command string."""
        command, arguments = parse_slash_command(text)
        if not command:
            return None, "", ""
        return self._skills.get(command), command, arguments

    def render_skill(self, name: str, arguments: str = "") -> str | None:
        """Return the expanded prompt for a skill, or ``None`` if unknown."""
        skill = self._skills.get(name)
        if skill is None:
            return None
        return skill.render(arguments)

    def lifecycle_snapshot(self, text: str | None = None) -> SkillLifecycleSnapshot:
        """为一次 Turn 生成 Skill 生命周期引用，不保存正文或宿主路径。"""
        activation = resolve_skill_activation(self, text or "")
        return SkillLifecycleSnapshot(
            catalog_revision=self._revision,
            discovery_precedence=_DISCOVERY_PRECEDENCE,
            activation_ref=(
                f"skill://{self._safe_source(activation)}/{activation.name}"
                if activation is not None
                else ""
            ),
            activation_revision=activation.revision if activation is not None else "",
            allowed_tools=(
                tuple(sorted(activation.allowed_tools)) if activation is not None else ()
            ),
        )

    def validate_lifecycle(self, expected: SkillLifecycleSnapshot) -> SkillLifecycleSnapshot:
        """在恢复进入 ToolBundle 前拒绝 catalog、激活版本或 allowlist 漂移。"""
        if expected.discovery_precedence != _DISCOVERY_PRECEDENCE:
            raise SkillLifecycleError("skill_discovery_precedence_mismatch")
        if expected.catalog_revision != self._revision:
            raise SkillLifecycleError("skill_catalog_revision_mismatch")
        if not expected.activation_ref:
            if expected.activation_revision or expected.allowed_tools:
                raise SkillLifecycleError("skill_activation_invalid")
            return expected
        current = self._snapshot_for_ref(expected.activation_ref)
        if current is None:
            raise SkillLifecycleError("skill_activation_missing")
        if (
            current.activation_ref != expected.activation_ref
            or current.activation_revision != expected.activation_revision
            or current.allowed_tools != expected.allowed_tools
        ):
            raise SkillLifecycleError("skill_activation_mismatch")
        return expected

    def _catalog_revision(self) -> str:
        payload = [
            {
                "name": skill.name,
                "source": skill.source,
                "revision": skill_revision(skill),
                "user_invocable": skill.user_invocable,
            }
            for skill in self.list()
        ]
        return _snapshot_hash({"precedence": list(_DISCOVERY_PRECEDENCE), "skills": payload})

    @staticmethod
    def _safe_source(activation: object) -> str:
        source = str(getattr(activation, "path", "")).split("/")
        return source[2] if len(source) > 2 and source[2] else "application"

    def _snapshot_for_ref(self, activation_ref: str) -> SkillLifecycleSnapshot | None:
        prefix = "skill://"
        if not activation_ref.startswith(prefix):
            return None
        reference = activation_ref[len(prefix) :].split("/", 1)
        if len(reference) != 2:
            return None
        source, name = reference
        skill = self._skills.get(name)
        if skill is None:
            return None
        if self._safe_source(resolve_skill_activation(self, f"/{name}") or object()) != source:
            return None
        activation = resolve_skill_activation(self, f"/{name}")
        if activation is None:
            return None
        return SkillLifecycleSnapshot(
            catalog_revision=self._revision,
            discovery_precedence=_DISCOVERY_PRECEDENCE,
            activation_ref=activation_ref,
            activation_revision=activation.revision,
            allowed_tools=tuple(sorted(activation.allowed_tools)),
        )
