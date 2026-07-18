"""Tests for operator-only cloud authentication commands."""

from unittest.mock import AsyncMock

from core.cloud.auth import cli


def test_create_invite_prints_only_the_generated_code(monkeypatch, capsys) -> None:
    create = AsyncMock(return_value="generated-one-time-code")
    monkeypatch.setattr(cli, "create_one_time_invite", create)

    assert cli.main(["create-invite", "--email", " Owner@Example.com "]) == 0

    create.assert_awaited_once_with("Owner@Example.com")
    output = capsys.readouterr().out
    assert output == "一次性邀请码（仅显示一次）: generated-one-time-code\n"
