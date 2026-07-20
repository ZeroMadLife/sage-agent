"""Coding session store tests."""

from pathlib import Path

from core.coding.persistence import CodingSessionStore


def test_historical_session_summary_defaults_to_legacy_profile(tmp_path: Path) -> None:
    store = CodingSessionStore(tmp_path)
    store.save(
        {
            "id": "legacy-session",
            "workspace_root": str(tmp_path),
            "created_at": "2026-07-16T00:00:00Z",
            "updated_at": "2026-07-16T00:00:00Z",
            "history": [{"role": "user", "content": "hello"}],
        }
    )

    assert store.list_sessions()[0]["runtime_profile"] == "legacy"


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
            "pinned": False,
            "archived": False,
            "workspace_root": "/tmp/new",
            "created_at": "2026-07-08T10:00:00",
            "updated_at": "2026-07-08T10:20:00",
            "runtime_mode": "plan",
            "runtime_profile": "legacy",
            "message_count": 1,
        },
        {
            "session_id": "s-old",
            "title": "读 README",
            "pinned": False,
            "archived": False,
            "workspace_root": "/tmp/old",
            "created_at": "2026-07-08T09:00:00",
            "updated_at": "2026-07-08T09:10:00",
            "runtime_mode": "default",
            "runtime_profile": "legacy",
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


def test_session_store_skips_sessions_with_unknown_runtime_profiles(tmp_path: Path) -> None:
    store = CodingSessionStore(tmp_path)
    store.save({
        "id": "s-invalid",
        "workspace_root": "/tmp/invalid",
        "created_at": "2026-07-08T10:00:00",
        "updated_at": "2026-07-08T10:20:00",
        "runtime_profile": "future_profile",
        "history": [{"role": "user", "content": "invalid"}],
    })
    store.save({
        "id": "s-valid",
        "workspace_root": "/tmp/valid",
        "created_at": "2026-07-08T09:00:00",
        "updated_at": "2026-07-08T09:10:00",
        "history": [{"role": "user", "content": "valid"}],
    })

    assert [item["session_id"] for item in store.list_sessions()] == ["s-valid"]


def test_session_store_empty_session_title_is_新会话(tmp_path: Path) -> None:
    """A session with no user messages has the title 新会话."""
    from core.coding.persistence.session_store import _session_title

    assert _session_title([], "/tmp/tour-agent") == "新会话"
    assert _session_title([{"role": "assistant", "content": "hi"}], "/tmp/tour-agent") == "新会话"
    # Non-empty history still uses the first user message.
    assert (
        _session_title([{"role": "user", "content": "读 README"}], "/tmp/tour-agent") == "读 README"
    )


def test_session_store_metadata_is_atomic_and_pinned_first(tmp_path: Path) -> None:
    store = CodingSessionStore(tmp_path)
    for session_id, updated_at in (("s-old", "2026-07-08T09:10:00"), ("s-new", "2026-07-08T10:20:00")):
        store.save({
            "id": session_id,
            "workspace_root": "/tmp/repo",
            "created_at": updated_at,
            "updated_at": updated_at,
            "history": [{"role": "user", "content": session_id}],
        })

    summary = store.update_metadata("s-old", title="  Review harness  ", pinned=True)

    assert summary["title"] == "Review harness"
    assert summary["pinned"] is True
    assert [item["session_id"] for item in store.list_sessions()] == ["s-old", "s-new"]
    archived = store.update_metadata("s-old", archived=True)
    assert archived["archived"] is True
    assert store.list_sessions() == [store.list_sessions(include_archived=True)[1]]


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


def test_session_store_messages_filters_internal_goal_followup_prompt(tmp_path: Path) -> None:
    store = CodingSessionStore(tmp_path)
    store.save(
        {
            "id": "s-goal-followup",
            "workspace_root": "/tmp/repo",
            "created_at": "2026-07-08T09:00:00",
            "updated_at": "2026-07-08T09:10:00",
            "history": [
                {
                    "role": "user",
                    "content": "这是用户已显式启用的受限自动跟进。\n\n目标：继续读取证据",
                    "created_at": "2026-07-08T09:00:01",
                },
                {
                    "role": "assistant",
                    "content": "我继续检查公开证据。",
                    "created_at": "2026-07-08T09:00:03",
                },
            ],
        }
    )

    assert store.messages("s-goal-followup") == [
        {
            "role": "assistant",
            "content": "我继续检查公开证据。",
            "created_at": "2026-07-08T09:00:03",
        }
    ]
    assert store.list_sessions()[0]["title"] == "新会话"
