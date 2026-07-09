"""Skills system tests: discovery, frontmatter, slash command resolution."""

from __future__ import annotations

from pathlib import Path

from core.coding.skills import SkillRegistry, discover_skills, parse_slash_command
from core.coding.skills.skill import Skill, parse_frontmatter


def test_discover_skills_loads_bundled_skills(tmp_path: Path) -> None:
    """Bundled coding and domain skills are discovered."""
    skills = discover_skills(tmp_path)
    assert set(skills) >= {"review", "test", "commit", "travel", "travel-planning"}
    assert skills["review"].source == "builtin"
    assert skills["review"].description
    assert skills["review"].prompt
    assert "generate_itinerary" in skills["travel"].allowed_tools


def test_skill_render_replaces_arguments(tmp_path: Path) -> None:
    """$ARGUMENTS placeholder is substituted."""
    skill = Skill(name="deploy", prompt="Deploy to $ARGUMENTS", source="builtin")
    assert skill.render("staging") == "Deploy to staging"


def test_skill_render_replaces_skill_dir() -> None:
    """${SAGE_SKILL_DIR} placeholder is substituted."""
    skill = Skill(name="x", prompt="dir=${SAGE_SKILL_DIR}", source="builtin", skill_root="/tmp/x")
    assert skill.render() == "dir=/tmp/x"


def test_parse_slash_command_extracts_name_and_args() -> None:
    """/name args → ('name', 'args')."""
    assert parse_slash_command("/review") == ("review", "")
    assert parse_slash_command("/deploy staging") == ("deploy", "staging")
    assert parse_slash_command("not a slash") == ("", "")
    assert parse_slash_command("/") == ("", "")


def test_parse_frontmatter_parses_metadata_and_body() -> None:
    """Frontmatter YAML-ish is parsed into metadata dict + body string."""
    text = "---\nname: review\ndescription: Audit code\nallowed-tools: read_file, search\n---\nBody here."
    metadata, body = parse_frontmatter(text)
    assert metadata["name"] == "review"
    assert metadata["description"] == "Audit code"
    assert metadata["allowed_tools"] == ["read_file", "search"]
    assert body.strip() == "Body here."


def test_parse_frontmatter_handles_no_frontmatter() -> None:
    """No frontmatter returns empty metadata and full body."""
    metadata, body = parse_frontmatter("Just body.")
    assert metadata == {}
    assert body == "Just body."


def test_user_skills_override_bundled(tmp_path: Path, monkeypatch) -> None:
    """User skills dir overrides bundled skill of the same name."""
    user_dir = tmp_path / "home" / ".sage" / "skills" / "review"
    user_dir.mkdir(parents=True)
    (user_dir / "SKILL.md").write_text(
        "---\nname: review\ndescription: User override\n---\nUser body.",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    skills = discover_skills(tmp_path)
    assert skills["review"].source == "user"
    assert skills["review"].description == "User override"


def test_project_skills_load_from_repo_skills_dir(tmp_path: Path) -> None:
    """Project skills loaded from <repo>/skills/<name>/SKILL.md."""
    project_dir = tmp_path / "skills" / "deploy"
    project_dir.mkdir(parents=True)
    (project_dir / "SKILL.md").write_text(
        "---\nname: deploy\ndescription: Deploy project\n---\nDeploy body.",
        encoding="utf-8",
    )
    skills = discover_skills(tmp_path)
    assert "deploy" in skills
    assert skills["deploy"].source == "project"


def test_skill_registry_resolve_known_command(tmp_path: Path) -> None:
    """Registry resolves a known slash command to the skill."""
    registry = SkillRegistry(root=tmp_path)
    skill, command, args = registry.resolve("/review")
    assert skill is not None
    assert skill.name == "review"
    assert command == "review"
    assert args == ""


def test_skill_registry_resolves_travel_alias(tmp_path: Path) -> None:
    """The v5 travel domain skill is invokable as /travel."""
    registry = SkillRegistry(root=tmp_path)

    skill, command, args = registry.resolve("/travel 杭州两天五百")

    assert skill is not None
    assert skill.name == "travel"
    assert command == "travel"
    assert args == "杭州两天五百"


def test_skill_registry_resolve_unknown_command(tmp_path: Path) -> None:
    """Unknown slash command returns (None, command, args)."""
    registry = SkillRegistry(root=tmp_path)
    skill, command, args = registry.resolve("/nonexistent")
    assert skill is None
    assert command == "nonexistent"
    assert args == ""


def test_skill_registry_resolve_non_slash(tmp_path: Path) -> None:
    """Non-slash input returns (None, '', '')."""
    registry = SkillRegistry(root=tmp_path)
    skill, command, args = registry.resolve("read README.md")
    assert skill is None
    assert command == ""
    assert args == ""
