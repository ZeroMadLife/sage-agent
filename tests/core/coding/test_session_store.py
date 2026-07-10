"""Coding session store tests."""

from pathlib import Path

from core.coding.persistence import CodingSessionStore


def test_session_store_lists_session_summaries(tmp_path: Path) -> None:
    """Session store exposes Hermes-style session summaries for the workbench."""
    store = CodingSessionStore(tmp_path)
    store.save(
        {
            "id": "s-old",
            "workspace_root": "/tmp/old",
            "created_at": "2026-07-08T09:00:00",
            "updated_at": "2026-07-08T09:10:00",
            "runtime_mode": {"mode": "default"},
            "history": [{"role": "user", "content": "读 README"}],
        }
    )
    store.save(
        {
            "id": "s-new",
            "workspace_root": "/tmp/new",
            "created_at": "2026-07-08T10:00:00",
            "updated_at": "2026-07-08T10:20:00",
            "runtime_mode": {"mode": "plan"},
            "history": [{"role": "user", "content": "新需求"}],
        }
    )

    summaries = store.list_sessions()

    assert summaries == [
        {
            "session_id": "s-new",
            "title": "新需求",
            "workspace_root": "/tmp/new",
            "created_at": "2026-07-08T10:00:00",
            "updated_at": "2026-07-08T10:20:00",
            "runtime_mode": "plan",
            "message_count": 1,
        },
        {
            "session_id": "s-old",
            "title": "读 README",
            "workspace_root": "/tmp/old",
            "created_at": "2026-07-08T09:00:00",
            "updated_at": "2026-07-08T09:10:00",
            "runtime_mode": "default",
            "message_count": 1,
        },
    ]


def test_session_store_filters_out_empty_sessions(tmp_path: Path) -> None:
    """Empty sessions (no history) are hidden from list_sessions."""
    store = CodingSessionStore(tmp_path)
    # An empty session that should be filtered out.
    store.save(
        {
            "id": "s-empty",
            "workspace_root": "/tmp/empty",
            "created_at": "2026-07-08T10:00:00",
            "updated_at": "2026-07-08T10:20:00",
            "runtime_mode": {"mode": "default"},
            "history": [],
        }
    )
    # A real session with messages.
    store.save(
        {
            "id": "s-real",
            "workspace_root": "/tmp/real",
            "created_at": "2026-07-08T09:00:00",
            "updated_at": "2026-07-08T09:10:00",
            "runtime_mode": {"mode": "default"},
            "history": [{"role": "user", "content": "hello"}],
        }
    )

    summaries = store.list_sessions()
    session_ids = [item["session_id"] for item in summaries]

    assert "s-empty" not in session_ids
    assert "s-real" in session_ids


def test_session_store_empty_session_title_is_新会话(tmp_path: Path) -> None:
    """A session with no user messages has the title 新会话."""
    from core.coding.persistence.session_store import _session_title

    assert _session_title([], "/tmp/tour-agent") == "新会话"
    assert _session_title([{"role": "assistant", "content": "hi"}], "/tmp/tour-agent") == "新会话"
    # Non-empty history still uses the first user message.
    assert (
        _session_title([{"role": "user", "content": "读 README"}], "/tmp/tour-agent") == "读 README"
    )


def test_session_store_returns_replayable_chat_messages(tmp_path: Path) -> None:
    """Session store normalizes persisted history into UI replay messages."""
    store = CodingSessionStore(tmp_path)
    store.save(
        {
            "id": "s-chat",
            "workspace_root": "/tmp/repo",
            "created_at": "2026-07-08T09:00:00",
            "updated_at": "2026-07-08T09:10:00",
            "runtime_mode": {"mode": "default"},
            "history": [
                {
                    "role": "user",
                    "content": "读 README",
                    "created_at": "2026-07-08T09:00:01",
                },
                {
                    "role": "tool",
                    "content": "raw tool output",
                    "created_at": "2026-07-08T09:00:02",
                },
                {
                    "role": "assistant",
                    "content": "README 里是 Sage。",
                    "created_at": "2026-07-08T09:00:03",
                },
            ],
        }
    )

    assert store.messages("s-chat") == [
        {
            "role": "user",
            "content": "读 README",
            "created_at": "2026-07-08T09:00:01",
        },
        {
            "role": "assistant",
            "content": "README 里是 Sage。",
            "created_at": "2026-07-08T09:00:03",
        },
    ]


def test_session_store_messages_filters_old_style_skill_prompts(tmp_path: Path) -> None:
    """Old-style expanded skill prompts persisted as user messages are filtered out."""
    store = CodingSessionStore(tmp_path)
    expanded_prompt = (
        "你正在使用 Sage 的 travel-planning domain skill。\n\n用户需求：\n\n我要去莆田"
    )
    store.save(
        {
            "id": "s-skill",
            "workspace_root": "/tmp/repo",
            "created_at": "2026-07-08T09:00:00",
            "updated_at": "2026-07-08T09:10:00",
            "runtime_mode": {"mode": "default"},
            "history": [
                {
                    "role": "user",
                    "content": expanded_prompt,
                    "created_at": "2026-07-08T09:00:01",
                },
                {
                    "role": "assistant",
                    "content": "好的，我们来规划莆田行程。",
                    "created_at": "2026-07-08T09:00:03",
                },
            ],
        }
    )

    messages = store.messages("s-skill")

    # The old-style expanded skill prompt is dropped; only the assistant reply remains.
    assert messages == [
        {
            "role": "assistant",
            "content": "好的，我们来规划莆田行程。",
            "created_at": "2026-07-08T09:00:03",
        }
    ]
