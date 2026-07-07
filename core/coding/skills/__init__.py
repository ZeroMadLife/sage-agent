"""Sage coding skills: SKILL.md discovery, slash command resolution."""

from core.coding.skills.registry import SkillRegistry
from core.coding.skills.skill import Skill, discover_skills, parse_slash_command

__all__ = ["Skill", "SkillRegistry", "discover_skills", "parse_slash_command"]
