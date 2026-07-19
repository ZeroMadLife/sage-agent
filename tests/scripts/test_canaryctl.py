"""Canary CI/CD controller tests."""

# ruff: noqa: I001 - Ruff 0.8 and 0.15 disagree on the tests/scripts import section.

import json
from pathlib import Path

import pytest

from scripts.canaryctl import (
    CanaryConfig,
    CanaryController,
    CanaryError,
    config_from_values,
    parse_check_runs,
    validate_commit,
)

SHA = "a" * 40
NEXT_SHA = "b" * 40


def _config(tmp_path: Path, **overrides: object) -> CanaryConfig:
    (tmp_path / "deploy-key").touch()
    values: dict[str, object] = {
        "repo_root": str(tmp_path),
        "remote_host": "sage-deploy@sage-agent-canary.tail74531c.ts.net",
        "remote_app": "/opt/sage/app",
        "ssh_key": str(tmp_path / "deploy-key"),
        "env_file": "/etc/sage/env",
        "docker_host": "unix:///run/user/1002/docker.sock",
        "health_url": "https://sage-agent-canary.tail74531c.ts.net/health",
        "github_repo": "ZeroMadLife/sage-agent",
        "branch": "dev/sage-v7",
        "state_root": str(tmp_path / "state"),
        "host_key_alias": "121.40.185.188",
        "git_bin": "/usr/bin/true",
        "gh_bin": "/usr/bin/false",
        "ssh_bin": "/usr/bin/env",
        "cc_connect_bin": "/usr/bin/printf",
        "notify_project": "sage",
        "notify_session": "feishu:test",
        "auto_deploy": True,
    }
    values.update(overrides)
    return config_from_values(values)


def test_check_runs_require_latest_success_for_every_gate() -> None:
    payload = {
        "check_runs": [
            {
                "name": "python",
                "status": "completed",
                "conclusion": "failure",
                "completed_at": "2026-07-19T10:00:00Z",
            },
            {
                "name": "python",
                "status": "completed",
                "conclusion": "success",
                "completed_at": "2026-07-19T11:00:00Z",
            },
            {
                "name": "backend-quality",
                "status": "completed",
                "conclusion": "success",
                "completed_at": "2026-07-19T11:00:00Z",
            },
            {
                "name": "frontend-quality",
                "status": "completed",
                "conclusion": "success",
                "completed_at": "2026-07-19T11:00:00Z",
            },
        ]
    }

    assert parse_check_runs(json.dumps(payload)) == {
        "python": "success",
        "backend-quality": "success",
        "frontend-quality": "success",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"check_runs": []},
        {
            "check_runs": [
                {
                    "name": "python",
                    "status": "in_progress",
                    "conclusion": None,
                }
            ]
        },
    ],
)
def test_check_runs_block_missing_or_incomplete_gates(payload: dict[str, object]) -> None:
    with pytest.raises(CanaryError):
        parse_check_runs(json.dumps(payload))


def test_config_rejects_branch_or_remote_socket_drift(tmp_path: Path) -> None:
    with pytest.raises(CanaryError, match="dev/sage-v7"):
        _config(tmp_path, branch="main")
    with pytest.raises(CanaryError, match="rootless socket"):
        _config(tmp_path, docker_host="unix:///var/run/docker.sock")


def test_remote_deploy_script_has_fixed_order_and_quoted_sha(tmp_path: Path) -> None:
    controller = CanaryController(_config(tmp_path))

    script = controller._remote_deploy_script(NEXT_SHA)

    assert "preflight" in script
    assert "fetch --prune origin refs/heads/dev/sage-v7" in script
    assert f"checkout --detach {NEXT_SHA}" in script
    assert "--execute apply --tag" in script
    assert script.index("preflight") < script.index("checkout") < script.index("--execute apply")
    with pytest.raises(CanaryError):
        controller._remote_deploy_script("main")


def test_deploy_requires_ci_and_updates_private_state(tmp_path: Path) -> None:
    config = _config(tmp_path)
    calls: list[tuple[list[str], str | None]] = []

    def runner(command, input_text, timeout):
        calls.append((list(command), input_text))
        if (
            command[:2] == ["/usr/bin/false", "api"]
            and f"commits/{NEXT_SHA}" in command[2]
        ):
            return type("Result", (), {"returncode": 0, "stdout": json.dumps({"check_runs": [
                {"name": "python", "status": "completed", "conclusion": "success"},
                {"name": "backend-quality", "status": "completed", "conclusion": "success"},
                {"name": "frontend-quality", "status": "completed", "conclusion": "success"},
            ]}), "stderr": ""})()
        if command and command[0] == "/usr/bin/true" and command[1] == "-C":
            return type("Result", (), {"returncode": 0, "stdout": f"{NEXT_SHA}\trefs/heads/dev/sage-v7\n", "stderr": ""})()
        if command and command[0] == "/usr/bin/env":
            script = input_text or ""
            if "rev-parse HEAD" in script and "checkout" not in script:
                return type("Result", (), {"returncode": 0, "stdout": f"{SHA}\n", "stderr": ""})()
            if "deployctl.py" in script and " status" in script:
                return type("Result", (), {"returncode": 0, "stdout": f'{{"status":"healthy","current":"{SHA}"}}\n', "stderr": ""})()
            return type("Result", (), {"returncode": 0, "stdout": "{}\n", "stderr": ""})()
        raise AssertionError(command)

    result = CanaryController(config, runner=runner).deploy(NEXT_SHA)

    assert result == {"status": "deployed", "sha": NEXT_SHA, "previous": SHA}
    state = json.loads(config.state_path.read_text(encoding="utf-8"))
    assert state["last_deployed_sha"] == NEXT_SHA
    assert config.state_path.stat().st_mode & 0o777 == 0o600
    assert all("APP_SECRET_KEY" not in (input_text or "") for _, input_text in calls)


def test_check_notifies_only_on_health_transition(tmp_path: Path) -> None:
    config = _config(tmp_path)
    healthy = True
    notifications: list[str] = []

    def runner(command, input_text, timeout):
        if command and command[0] == "/usr/bin/env":
            return type("Result", (), {"returncode": 0, "stdout": '{"status":"healthy"}', "stderr": ""})()
        if command and command[0] == "/usr/bin/printf":
            notifications.append(input_text or "")
            return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        raise AssertionError(command)

    def probe(_url: str) -> bool:
        return healthy

    controller = CanaryController(config, runner=runner, http_probe=probe)
    assert controller.check()["healthy"] is True
    healthy = False
    assert controller.check()["healthy"] is False
    assert controller.check()["healthy"] is False
    healthy = True
    assert controller.check()["healthy"] is True
    assert len(notifications) == 2


def test_availability_rejects_degraded_server_status(tmp_path: Path) -> None:
    def runner(command, input_text, timeout):
        assert command[0] == "/usr/bin/env"
        assert input_text and input_text.startswith("set -eu\n")
        return type(
            "Result",
            (),
            {"returncode": 0, "stdout": '{"status":"degraded"}', "stderr": ""},
        )()

    report = CanaryController(
        _config(tmp_path), runner=runner, http_probe=lambda _url: True
    ).availability()

    assert report["http"] is True
    assert report["remote"] is False
    assert report["healthy"] is False


def test_validate_commit_rejects_shell_text() -> None:
    with pytest.raises(CanaryError):
        validate_commit("a" * 40 + "; touch /tmp/pwned")
