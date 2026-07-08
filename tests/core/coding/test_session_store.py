"""Coding session store tests."""

from pathlib import Path

from core.coding.session_store import CodingSessionStore


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
            "history": [],
        }
    )

    summaries = store.list_sessions()

    assert summaries == [
        {
            "session_id": "s-new",
            "title": "new",
            "workspace_root": "/tmp/new",
            "created_at": "2026-07-08T10:00:00",
            "updated_at": "2026-07-08T10:20:00",
            "runtime_mode": "plan",
            "message_count": 0,
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
