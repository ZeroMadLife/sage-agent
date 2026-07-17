from __future__ import annotations

import subprocess

import pytest

from core.loop_harness.errors import LoopBlockedError
from core.loop_harness.notifier import CcConnectNotifier


def test_notifier_sends_payload_over_stdin(tmp_path, monkeypatch) -> None:
    binary = tmp_path / "cc-connect"
    binary.touch()
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, "Message sent successfully.\n", "")

    monkeypatch.setattr("core.loop_harness.notifier.subprocess.run", fake_run)
    notifier = CcConnectNotifier(cc_connect_bin=binary)

    notifier.send(
        project="sage",
        session_key="feishu:chat:user",
        message="Loop 日报\n无异常",
    )

    args, kwargs = calls[0]
    assert "Loop 日报" not in args
    assert args[-2:] == ["--session", "feishu:chat:user"]
    assert kwargs["input"] == "Loop 日报\n无异常"
    assert "OPENAI_API_KEY" not in kwargs["env"]


@pytest.mark.parametrize(
    ("project", "session_key"),
    (
        ("sage-review", "feishu:chat:user"),
        ("sage", "relay:chat:user"),
        ("sage", "feishu:chat user"),
    ),
)
def test_notifier_rejects_invalid_target(tmp_path, project, session_key) -> None:
    notifier = CcConnectNotifier(cc_connect_bin=tmp_path / "cc-connect")

    with pytest.raises(LoopBlockedError) as exc:
        notifier.send(project=project, session_key=session_key, message="状态")

    assert exc.value.code == "BLOCKED_NOTIFY"
