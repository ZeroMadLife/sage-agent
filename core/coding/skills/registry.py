"""Skill registry and slash command resolution."""

from __future__ import annotations

from pathlib import Path

from core.coding.skills.skill import (
    Skill,
    discover_skills,
    parse_slash_command,
)


class SkillRegistry:
    """Resolve slash commands against discovered skills."""

    def __init__(self, root: Path | str, home: Path | str | None = None) -> None:
        self.root = Path(root)
        self.home = Path(home) if home else None
        self._skills: dict[str, Skill] = discover_skills(self.root, home=self.home)

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
