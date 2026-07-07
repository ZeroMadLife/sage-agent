"""Skill discovery, prompt expansion, and slash workflow execution."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


@dataclass(frozen=True)
class Skill:
    """A reusable prompt workflow invokable via ``/name [args]``."""

    name: str
    description: str = ""
    prompt: str = ""
    source: str = "builtin"
    skill_root: str = ""
    allowed_tools: tuple[str, ...] = ()
    argument_hint: str = ""
    user_invocable: bool = True

    def render(self, arguments: str = "") -> str:
        """Expand the skill body, substituting ``$ARGUMENTS``."""
        text = self.prompt
        replacements = {
            "$ARGUMENTS": str(arguments),
            "${SAGE_SKILL_DIR}": self.skill_root,
            "${PICO_SKILL_DIR}": self.skill_root,
        }
        if self.argument_hint:
            replacements[f"${{{self.argument_hint}}}"] = str(arguments)
        for old, new in replacements.items():
            text = text.replace(old, new)
        return text.strip()

    def metadata(self) -> dict[str, Any]:
        """Return a JSON-serialisable metadata snapshot."""
        return {
            "name": self.name,
            "description": self.description,
            "source": self.source,
            "allowed_tools": list(self.allowed_tools),
            "argument_hint": self.argument_hint,
            "user_invocable": self.user_invocable,
        }


def discover_skills(root: Path | str, home: Path | str | None = None) -> dict[str, Skill]:
    """Load skills from bundled, user, and project locations.

    Later loads override earlier ones, mirroring Pico's precedence:
    bundled → ``~/.sage/skills`` → ``<repo>/skills`` / ``<repo>/.coding/skills``.
    """
    root_path = Path(root)
    skills: dict[str, Skill] = {}

    bundled_dir = Path(__file__).parent / "bundled"
    for skill in _load_skills_from_dir(bundled_dir, source="builtin"):
        skills[skill.name] = skill

    user_dir = Path(home or Path.home()) / ".sage" / "skills"
    for skill in _load_skills_from_dir(user_dir, source="user"):
        skills[skill.name] = skill

    for project_dir in (root_path / "skills", root_path / ".coding" / "skills"):
        for skill in _load_skills_from_dir(project_dir, source="project"):
            skills[skill.name] = skill

    return dict(sorted(skills.items()))


def parse_slash_command(text: str) -> tuple[str, str]:
    """Split ``/name args`` into ``(name, args)``; empty strings when not a slash."""
    text = str(text).strip()
    if not text.startswith("/") or text == "/":
        return "", ""
    command, _, arguments = text[1:].partition(" ")
    return command.strip(), arguments.strip()


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse YAML-ish frontmatter and return ``(metadata, body)``."""
    match = FRONTMATTER_RE.match(str(text))
    if not match:
        return {}, str(text)
    metadata: dict[str, Any] = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip().lower().replace("-", "_")] = _parse_value(value.strip())
    return metadata, str(text)[match.end() :]


def _load_skills_from_dir(skills_dir: Path | str, source: str) -> list[Skill]:
    skills_dir = Path(skills_dir).expanduser()
    if not skills_dir.exists():
        return []
    files: list[Path] = []
    for path in sorted(skills_dir.iterdir()):
        if path.is_dir() and (path / "SKILL.md").is_file():
            files.append(path / "SKILL.md")
        elif path.is_file() and path.suffix.lower() == ".md":
            files.append(path)
    return [skill for path in files if (skill := _load_skill_file(path, source=source))]


def _load_skill_file(path: Path, source: str) -> Skill | None:
    metadata, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    default_name = path.parent.name if path.name == "SKILL.md" else path.stem
    name = str(metadata.get("name") or default_name).strip().lstrip("/")
    if not name:
        return None
    return Skill(
        name=name,
        description=_string(metadata.get("description")),
        prompt=body.strip(),
        source=source,
        skill_root=str(path.parent),
        allowed_tools=tuple(_list_value(metadata.get("allowed_tools"))),
        argument_hint=_string(metadata.get("arguments") or metadata.get("argument_hint")),
        user_invocable=_bool_value(metadata.get("user_invocable", True)),
    )


def _parse_value(value: str) -> Any:
    value = value.strip().strip("\"'")
    if value.lower() in {"true", "yes"}:
        return True
    if value.lower() in {"false", "no"}:
        return False
    if "," in value:
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


def _list_value(value: Any) -> list[str]:
    if isinstance(value, list | tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _string(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, list | tuple):
        return ", ".join(str(item) for item in value)
    return str(value)


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off"}
