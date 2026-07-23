"""Canary CI/CD controller tests."""

# ruff: noqa: I001 - Ruff 0.8 and 0.15 disagree on the tests/scripts import section.

import json
from pathlib import Path

import pytest

from scripts.canaryctl import (
    DEFAULT_PUBLIC_HEALTH_URL,
    DEFAULT_REMOTE_HOST,
    CanaryConfig,
    CanaryController,
    CanaryDeferred,
    CanaryError,
    config_from_values,
    parse_check_runs,
    validate_commit,
)

SHA = "a" * 40
NEXT_SHA = "b" * 40
REQUIRED_CHECKS = (
    "python",
    "backend-quality",
    "frontend-quality",
    "public-release",
)


def test_default_management_channel_uses_public_key_only_ssh() -> None:
    assert DEFAULT_REMOTE_HOST == "sage-deploy@121.40.185.188"
    assert DEFAULT_PUBLIC_HEALTH_URL == "https://sagecompanion.top/"


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
        "curl_bin": "/bin/echo",
        "openssl_bin": "/usr/bin/true",
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
            {
                "name": "public-release",
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
        "public-release": "success",
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
    with pytest.raises(CanaryDeferred):
        parse_check_runs(json.dumps(payload))


def test_sync_waits_for_ci_without_incrementing_failures(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.state_root.mkdir(parents=True)
    config.state_path.write_text(
        json.dumps(
            {
                "consecutive_sync_failures": 2,
                "auto_deploy_paused": False,
                "last_sync_error": "previous failure",
            }
        ),
        encoding="utf-8",
    )
    controller = CanaryController(config)
    controller.latest_sha = lambda: NEXT_SHA

    def deferred(_sha: str) -> dict[str, object]:
        raise CanaryDeferred("GitHub CI 仍在运行: python")

    controller.deploy = deferred

    result = controller.sync()

    assert result == {
        "status": "waiting-ci",
        "reason": "GitHub CI 仍在运行: python",
    }
    state = json.loads(config.state_path.read_text(encoding="utf-8"))
    assert state["consecutive_sync_failures"] == 2
    assert state["auto_deploy_paused"] is False
    assert state["last_sync_error"] == "GitHub CI 仍在运行: python"


def test_config_rejects_branch_or_remote_socket_drift(tmp_path: Path) -> None:
    with pytest.raises(CanaryError, match="dev/sage-v7"):
        _config(tmp_path, branch="main")
    with pytest.raises(CanaryError, match="rootless socket"):
        _config(tmp_path, docker_host="unix:///var/run/docker.sock")


def test_remote_deploy_script_has_fixed_order_and_quoted_sha(tmp_path: Path) -> None:
    controller = CanaryController(_config(tmp_path))

    script = controller._remote_deploy_script(NEXT_SHA)

    assert "status --porcelain" in script
    assert "fetch --prune origin refs/heads/dev/sage-v7" in script
    assert 'while [ "$attempt" -le 3 ]' in script
    assert 'test "$fetched" = 1' in script
    assert "sleep 10" in script
    assert f"checkout --detach {NEXT_SHA}" in script
    assert "--execute cleanup" in script
    assert "preflight" in script
    assert "--execute apply --tag" in script
    assert (
        script.index("checkout")
        < script.index("--execute cleanup")
        < script.index("preflight")
        < script.index("--execute apply")
    )
    with pytest.raises(CanaryError):
        controller._remote_deploy_script("main")


def test_deploy_requires_ci_and_updates_private_state(tmp_path: Path) -> None:
    config = _config(tmp_path)
    calls: list[tuple[list[str], str | None, int]] = []

    def runner(command, input_text, timeout):
        calls.append((list(command), input_text, timeout))
        if command[:2] == ["/usr/bin/false", "api"] and f"commits/{NEXT_SHA}" in command[2]:
            return type(
                "Result",
                (),
                {
                    "returncode": 0,
                    "stdout": json.dumps(
                        {
                            "check_runs": [
                                {"name": "python", "status": "completed", "conclusion": "success"},
                                {
                                    "name": "backend-quality",
                                    "status": "completed",
                                    "conclusion": "success",
                                },
                                {
                                    "name": "frontend-quality",
                                    "status": "completed",
                                    "conclusion": "success",
                                },
                                {
                                    "name": "public-release",
                                    "status": "completed",
                                    "conclusion": "success",
                                },
                            ]
                        }
                    ),
                    "stderr": "",
                },
            )()
        if command and command[0] == "/usr/bin/true" and command[1] == "-C":
            return type(
                "Result",
                (),
                {"returncode": 0, "stdout": f"{NEXT_SHA}\trefs/heads/dev/sage-v7\n", "stderr": ""},
            )()
        if command and command[0] == "/usr/bin/env":
            script = input_text or ""
            if "rev-parse HEAD" in script and "checkout" not in script:
                return type("Result", (), {"returncode": 0, "stdout": f"{SHA}\n", "stderr": ""})()
            if "deployctl.py" in script and " status" in script:
                return type(
                    "Result",
                    (),
                    {
                        "returncode": 0,
                        "stdout": f'{{"status":"healthy","current":"{SHA}"}}\n',
                        "stderr": "",
                    },
                )()
            if "sage-public-releasectl" in script and '"action":"status"' in script:
                return type(
                    "Result",
                    (),
                    {
                        "returncode": 0,
                        "stdout": f'{{"status":"healthy","current":"{SHA}"}}\n',
                        "stderr": "",
                    },
                )()
            if "sage-public-releasectl" in script and '"action":"apply"' in script:
                return type(
                    "Result",
                    (),
                    {
                        "returncode": 0,
                        "stdout": f'{{"status":"deployed","tag":"{NEXT_SHA}","previous":"{SHA}"}}\n',
                        "stderr": "",
                    },
                )()
            return type("Result", (), {"returncode": 0, "stdout": "{}\n", "stderr": ""})()
        if command and command[0] == "/bin/echo":
            return type(
                "Result",
                (),
                {
                    "returncode": 0,
                    "stdout": "<title>ZeroMadLife / Sage</title>",
                    "stderr": "",
                },
            )()
        raise AssertionError(command)

    result = CanaryController(config, runner=runner).deploy(NEXT_SHA)

    assert result == {
        "status": "deployed",
        "sha": NEXT_SHA,
        "previous": SHA,
        "public_previous": SHA,
    }
    state = json.loads(config.state_path.read_text(encoding="utf-8"))
    assert state["last_deployed_sha"] == NEXT_SHA
    assert config.state_path.stat().st_mode & 0o777 == 0o600
    deploy_calls = [
        (command, timeout)
        for command, input_text, timeout in calls
        if input_text and "checkout --detach" in input_text
    ]
    assert len(deploy_calls) == 1
    deploy_command, deploy_timeout = deploy_calls[0]
    assert "ServerAliveInterval=30" in deploy_command
    assert "ServerAliveCountMax=20" in deploy_command
    assert deploy_timeout == 7200
    assert all("APP_SECRET_KEY" not in (input_text or "") for _, input_text, _ in calls)


def test_check_notifies_only_on_health_transition(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        curl_bin="/usr/bin/false",
        openssl_bin="/usr/bin/false",
    )
    healthy = True
    notifications: list[str] = []

    def runner(command, input_text, timeout):
        if command and command[0] == "/usr/bin/false":
            return type("Result", (), {"returncode": 1, "stdout": "", "stderr": ""})()
        if command and command[0] == "/usr/bin/env":
            return type(
                "Result", (), {"returncode": 0, "stdout": '{"status":"healthy"}', "stderr": ""}
            )()
        if command and command[0] == "/usr/bin/printf":
            notifications.append(input_text or "")
            return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        raise AssertionError(command)

    def probe(_url: str) -> bool:
        return healthy

    controller = CanaryController(config, runner=runner, http_probe=probe, public_http_probe=probe)
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
        _config(tmp_path),
        runner=runner,
        http_probe=lambda _url: True,
        public_http_probe=lambda _url: True,
    ).availability()

    assert report["http"] is True
    assert report["remote"] is False
    assert report["healthy"] is False


def test_availability_falls_back_to_proxy_free_curl(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(command, input_text, timeout):
        calls.append(list(command))
        if command[0] == "/bin/echo":
            return type(
                "Result",
                (),
                {
                    "returncode": 0,
                    "stdout": "<title>ZeroMadLife / Sage</title>",
                    "stderr": "",
                },
            )()
        if command[0] == "/usr/bin/env":
            return type(
                "Result",
                (),
                {"returncode": 0, "stdout": '{"status":"healthy"}', "stderr": ""},
            )()
        raise AssertionError(command)

    report = CanaryController(
        _config(tmp_path),
        runner=runner,
        http_probe=lambda _url: False,
        public_http_probe=lambda _url: False,
    ).availability()

    assert report["healthy"] is True
    curl = next(command for command in calls if command[0] == "/bin/echo")
    assert curl[curl.index("--noproxy") + 1] == "*"


def test_private_https_probe_falls_back_to_authenticated_loopback(tmp_path: Path) -> None:
    calls: list[tuple[list[str], str | None, int]] = []

    def runner(command, input_text, timeout):
        calls.append((list(command), input_text, timeout))
        if command[0] == "/usr/bin/false":
            return type("Result", (), {"returncode": 1, "stdout": "", "stderr": ""})()
        if command[0] == "/usr/bin/env":
            return type(
                "Result",
                (),
                {"returncode": 0, "stdout": '{"status":"ok"}', "stderr": ""},
            )()
        raise AssertionError(command)

    controller = CanaryController(
        _config(tmp_path, curl_bin="/usr/bin/false"),
        runner=runner,
        http_probe=lambda _url: False,
    )

    assert controller._http_healthy() is True
    ssh_call = next(call for call in calls if call[0][0] == "/usr/bin/env")
    assert ssh_call[1] == (
        "set -eu\n"
        "curl --noproxy '*' --fail --silent --show-error --max-time 15 "
        "http://127.0.0.1:8080/health"
    )
    assert ssh_call[2] == 20


def test_private_loopback_probe_rejects_unexpected_payload(tmp_path: Path) -> None:
    def runner(command, input_text, timeout):
        if command[0] == "/usr/bin/false":
            return type("Result", (), {"returncode": 1, "stdout": "", "stderr": ""})()
        if command[0] == "/usr/bin/env":
            return type(
                "Result",
                (),
                {"returncode": 0, "stdout": '{"status":"healthy"}', "stderr": ""},
            )()
        raise AssertionError(command)

    controller = CanaryController(
        _config(tmp_path, curl_bin="/usr/bin/false"),
        runner=runner,
        http_probe=lambda _url: False,
    )

    assert controller._http_healthy() is False


def test_public_https_wait_allows_initial_certificate_issuance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = iter((False, False, True))
    sleeps: list[int] = []
    controller = CanaryController(_config(tmp_path))
    controller._public_http_healthy = lambda: next(attempts)
    monkeypatch.setattr("scripts.canaryctl.time.sleep", sleeps.append)

    assert controller._wait_public_http_healthy() is True
    assert sleeps == [1, 1]


def test_public_https_probe_falls_back_to_openssl_http_request(tmp_path: Path) -> None:
    calls: list[tuple[list[str], str | None, int]] = []

    def runner(command, input_text, timeout):
        calls.append((list(command), input_text, timeout))
        if command[0] == "/usr/bin/false":
            return type("Result", (), {"returncode": 1, "stdout": "", "stderr": ""})()
        if command[0] == "/usr/bin/true":
            return type(
                "Result",
                (),
                {
                    "returncode": 0,
                    "stdout": "HTTP/1.1 200 OK\r\n\r\n<title>ZeroMadLife / Sage</title>",
                    "stderr": "",
                },
            )()
        raise AssertionError(command)

    controller = CanaryController(
        _config(
            tmp_path,
            curl_bin="/usr/bin/false",
            openssl_bin="/usr/bin/true",
            public_health_url="https://sagecompanion.top/path?q=1",
        ),
        runner=runner,
        public_http_probe=lambda _url: False,
    )

    assert controller._public_http_healthy() is True
    openssl_call = next(call for call in calls if call[0][0] == "/usr/bin/true")
    assert openssl_call[0] == [
        "/usr/bin/true",
        "s_client",
        "-quiet",
        "-connect",
        "sagecompanion.top:443",
        "-servername",
        "sagecompanion.top",
    ]
    assert openssl_call[1] == (
        "GET /path?q=1 HTTP/1.1\r\n" "Host: sagecompanion.top\r\n" "Connection: close\r\n\r\n"
    )
    assert openssl_call[2] == 20


def test_public_https_openssl_probe_rejects_invalid_port(tmp_path: Path) -> None:
    controller = CanaryController(
        _config(tmp_path, public_health_url="https://sagecompanion.top:invalid/"),
        runner=lambda *_args, **_kwargs: pytest.fail("runner must not be called"),
    )

    assert controller._openssl_public_http_healthy() is False


def test_run_once_reuses_proxy_free_probe_after_deploy(tmp_path: Path) -> None:
    config = _config(tmp_path)
    controller = CanaryController(config, http_probe=lambda _url: False)
    curl_calls = 0

    def fake_http() -> bool:
        nonlocal curl_calls
        curl_calls += 1
        return True

    controller._http_healthy = fake_http
    controller.check = lambda: {"healthy": True, "private_healthy": True}
    controller.sync = lambda: {"status": "deployed", "sha": NEXT_SHA}
    controller._public_http_healthy = fake_http

    result = controller.run_once()

    assert result["status"] == "ok"
    assert curl_calls == 2


def test_run_once_can_repair_an_unhealthy_public_facade(tmp_path: Path) -> None:
    controller = CanaryController(_config(tmp_path))
    sync_calls = 0

    controller.check = lambda: {
        "healthy": False,
        "private_healthy": True,
        "public_healthy": False,
    }

    def sync() -> dict[str, object]:
        nonlocal sync_calls
        sync_calls += 1
        return {"status": "waiting-ci"}

    controller.sync = sync

    result = controller.run_once()

    assert result["status"] == "unhealthy"
    assert sync_calls == 1


def test_up_to_date_sync_refreshes_audit_state(tmp_path: Path) -> None:
    config = _config(tmp_path)

    def runner(command, input_text, timeout):
        if command[:2] == ["/usr/bin/false", "api"]:
            return type(
                "Result",
                (),
                {
                    "returncode": 0,
                    "stdout": json.dumps(
                        {
                            "check_runs": [
                                {
                                    "name": name,
                                    "status": "completed",
                                    "conclusion": "success",
                                }
                                for name in REQUIRED_CHECKS
                            ]
                        }
                    ),
                    "stderr": "",
                },
            )()
        if command and command[0] == "/usr/bin/env":
            script = input_text or ""
            if "rev-parse HEAD" in script and "checkout" not in script:
                stdout = f"{NEXT_SHA}\n"
            elif ("deployctl.py" in script and " status" in script) or (
                "sage-public-releasectl" in script and '"action":"status"' in script
            ):
                stdout = json.dumps({"status": "healthy", "current": NEXT_SHA})
            else:
                raise AssertionError(script)
            return type(
                "Result",
                (),
                {
                    "returncode": 0,
                    "stdout": stdout,
                    "stderr": "",
                },
            )()
        raise AssertionError(command)

    controller = CanaryController(config, runner=runner)
    result = controller.deploy(NEXT_SHA)

    assert result == {"status": "up-to-date", "sha": NEXT_SHA}
    state = json.loads(config.state_path.read_text(encoding="utf-8"))
    assert state["last_deployed_sha"] == NEXT_SHA
    assert state["consecutive_sync_failures"] == 0


def test_validate_commit_rejects_shell_text() -> None:
    with pytest.raises(CanaryError):
        validate_commit("a" * 40 + "; touch /tmp/pwned")


def test_public_release_request_is_fixed_json_and_uses_bounded_sudo(tmp_path: Path) -> None:
    calls: list[str] = []

    def runner(command, input_text, timeout):
        calls.append(input_text or "")
        return type(
            "Result",
            (),
            {"returncode": 0, "stdout": '{"status":"healthy"}', "stderr": ""},
        )()

    controller = CanaryController(_config(tmp_path), runner=runner)
    assert controller._remote_public_request("apply", NEXT_SHA) == {"status": "healthy"}

    assert len(calls) == 1
    assert "sudo -n /usr/local/sbin/sage-public-releasectl" in calls[0]
    assert '"action":"apply"' in calls[0]
    assert NEXT_SHA in calls[0]
    with pytest.raises(CanaryError):
        controller._remote_public_request("apply", "main")
