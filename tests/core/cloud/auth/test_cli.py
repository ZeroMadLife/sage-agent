"""Tests for operator-only cloud authentication commands."""

from datetime import datetime
from unittest.mock import AsyncMock

from core.cloud.auth import cli


def test_create_invite_prints_only_the_generated_code(monkeypatch, capsys) -> None:
    create = AsyncMock(return_value="generated-one-time-code")
    monkeypatch.setattr(cli, "create_one_time_invite", create)

    assert cli.main(["create-invite", "--email", " Owner@Example.com "]) == 0

    create.assert_awaited_once_with("Owner@Example.com")
    output = capsys.readouterr().out
    assert output == "一次性邀请码（仅显示一次）: generated-one-time-code\n"


def test_device_operations_are_available(monkeypatch, capsys) -> None:
    class Session:
        session_id = "session-1"
        device_name = "iPhone Safari"
        expires_at = datetime(2026, 7, 18)
        last_seen_at = None

    list_devices = AsyncMock(return_value=[Session()])
    monkeypatch.setattr(cli, "list_devices", list_devices)

    assert cli.main(["list-devices", "--email", "owner@example.com"]) == 0
    list_devices.assert_awaited_once_with("owner@example.com")
    assert "session-1 | iPhone Safari" in capsys.readouterr().out
