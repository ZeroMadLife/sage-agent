"""Tests for the bounded private Canary deployment controller."""

from pathlib import Path

import pytest

from scripts.deployctl import (
    DeployConfig,
    DeployController,
    DeployError,
    deployment_visible_socket_requirements,
    parse_env_file,
    redact,
    sandbox_limit_probe_command,
    validate_commit_tag,
    validate_environment,
)


def _valid_environment() -> dict[str, str]:
    secret = "x" * 40
    return {
        "APP_ENV": "production",
        "APP_SECRET_KEY": secret,
        "CLOUD_DEV_LOGIN_ENABLED": "false",
        "CLOUD_FRONTEND_URL": "https://sage.example.ts.net",
        "GITHUB_OAUTH_CLIENT_ID": "client-id",
        "GITHUB_OAUTH_CLIENT_SECRET": secret,
        "GITHUB_OAUTH_REDIRECT_URI": "https://sage.example.ts.net/api/v1/cloud/auth/github/callback",
        "GITHUB_OAUTH_TRANSACTION_SECRET": secret,
        "GITHUB_TOKEN_ENCRYPTION_SECRET": secret,
        "MODEL_PROVIDER_ENCRYPTION_SECRET": secret,
        "POSTGRES_DB": "sage",
        "POSTGRES_PASSWORD": secret,
        "POSTGRES_USER": "sage",
        "REDIS_PASSWORD": secret,
        "SAGE_API_GID": "0",
        "SAGE_API_UID": "0",
        "SAGE_CODING_SANDBOX_IMAGE": "docker.m.daocloud.io/library/python:3.12-slim",
        "SAGE_CODING_SANDBOX_PROVIDER": "container",
        "SAGE_DOCKER_REGISTRY": "docker.m.daocloud.io",
        "SAGE_ROOTLESS_DOCKER_SOCKET": "/run/user/1002/sage-sandbox.sock",
        "SAGE_SANDBOX_DOCKER_SOCKET": "/run/user/1003/docker.sock",
        "SAGE_SANDBOX_UID": "1003",
    }


def _write_env(path: Path, values: dict[str, str]) -> None:
    path.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def test_environment_validation_rejects_placeholders_without_leaking_values(
    tmp_path: Path,
) -> None:
    values = _valid_environment()
    secret = "REPLACE_WITH_super-secret-that-must-not-leak"
    values["APP_SECRET_KEY"] = secret
    env_file = tmp_path / "env"
    _write_env(env_file, values)

    with pytest.raises(DeployError) as exc_info:
        validate_environment(env_file, parse_env_file(env_file))

    assert "APP_SECRET_KEY" in str(exc_info.value)
    assert secret not in str(exc_info.value)


def test_environment_validation_accepts_private_production_file(tmp_path: Path) -> None:
    env_file = tmp_path / "env"
    values = _valid_environment()
    _write_env(env_file, values)

    validate_environment(env_file, parse_env_file(env_file))


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("SAGE_CODING_SANDBOX_IMAGE", "--privileged", "安全镜像引用"),
        (
            "SAGE_SANDBOX_DOCKER_SOCKET",
            "/run/user/1002/docker.sock",
            "SAGE_SANDBOX_UID",
        ),
    ],
)
def test_environment_validation_rejects_ambiguous_sandbox_config(
    tmp_path: Path,
    key: str,
    value: str,
    message: str,
) -> None:
    values = _valid_environment()
    values[key] = value
    env_file = tmp_path / "env"
    _write_env(env_file, values)

    with pytest.raises(DeployError, match=message):
        validate_environment(env_file, parse_env_file(env_file))


def test_environment_validation_rejects_a_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target"
    _write_env(target, _valid_environment())
    env_file = tmp_path / "env"
    env_file.symlink_to(target)

    with pytest.raises(DeployError, match="符号链接"):
        validate_environment(env_file, parse_env_file(env_file))


@pytest.mark.parametrize("tag", ["abc1234", "A" * 40, "0" * 39, "main", "0" * 41])
def test_commit_tag_must_be_a_full_lowercase_sha(tag: str) -> None:
    with pytest.raises(DeployError):
        validate_commit_tag(tag)


def test_dry_run_plan_never_calls_external_commands(tmp_path: Path) -> None:
    env_file = tmp_path / "env"
    _write_env(env_file, _valid_environment())

    def unexpected_runner(*_args, **_kwargs):
        raise AssertionError("dry-run must not execute commands")

    controller = DeployController(
        DeployConfig(
            repo_root=tmp_path,
            compose_file=tmp_path / "compose.yml",
            env_file=env_file,
            state_file=tmp_path / "state.json",
            backup_root=tmp_path / "backups",
            gateway_url="http://127.0.0.1:8080/health",
        ),
        runner=unexpected_runner,
    )

    result = controller.apply("0" * 40, execute=False)

    assert result["mode"] == "dry-run"
    assert result["steps"] == [
        "preflight",
        "build",
        "database backup",
        "migration",
        "health",
    ]


def test_redaction_removes_every_configured_secret() -> None:
    values = _valid_environment()
    message = " ".join(values[key] for key in ("APP_SECRET_KEY", "POSTGRES_PASSWORD"))

    assert redact(message, values) == "[REDACTED] [REDACTED]"


def test_deploy_user_only_stats_sockets_visible_through_its_runtime() -> None:
    requirements = deployment_visible_socket_requirements(
        "unix:///run/user/1002/docker.sock",
        "/run/user/1002/sage-sandbox.sock",
    )

    assert [path for _, path, _ in requirements] == [
        "/run/user/1002/docker.sock",
        "/run/user/1002/sage-sandbox.sock",
    ]
    assert "/run/user/1003/docker.sock" not in {path for _, path, _ in requirements}


def test_sandbox_probe_exercises_every_required_limit_without_network() -> None:
    command = sandbox_limit_probe_command(
        "unix:///run/user/1002/sage-sandbox.sock",
        "registry.example/python:3.12-slim",
    )

    assert command[:4] == [
        "docker",
        "--host",
        "unix:///run/user/1002/sage-sandbox.sock",
        "run",
    ]
    assert "--pull=never" in command
    assert command[command.index("--network") + 1] == "none"
    assert command[command.index("--pids-limit") + 1] == "32"
    assert command[command.index("--memory") + 1] == "64m"
    assert command[command.index("--cpus") + 1] == "0.25"
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert "no-new-privileges" in command
