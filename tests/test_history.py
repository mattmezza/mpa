"""Tests for ConversationHistory."""

from __future__ import annotations

import pytest

from core.history import ConversationHistory


# ---------------------------------------------------------------------------
# Injection mode (existing tests)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pair_truncation_keeps_complete_pairs(tmp_path) -> None:
    """max_turns counts user-assistant *pairs*, not individual rows."""
    db_path = str(tmp_path / "agent.db")
    history = ConversationHistory(db_path=db_path, max_turns=1)

    # Two full exchanges
    await history.add_turn("telegram", "u1", "user", "hi")
    await history.add_turn("telegram", "u1", "assistant", "hello")
    await history.add_turn("telegram", "u1", "user", "how are you")
    await history.add_turn("telegram", "u1", "assistant", "good thanks")

    messages = await history.get_messages("telegram", "u1")

    # max_turns=1 → only the last pair
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "how are you"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == "good thanks"


@pytest.mark.asyncio
async def test_multiple_pairs(tmp_path) -> None:
    """max_turns=2 returns the last two complete pairs."""
    db_path = str(tmp_path / "agent.db")
    history = ConversationHistory(db_path=db_path, max_turns=2)

    await history.add_turn("telegram", "u1", "user", "first")
    await history.add_turn("telegram", "u1", "assistant", "reply1")
    await history.add_turn("telegram", "u1", "user", "second")
    await history.add_turn("telegram", "u1", "assistant", "reply2")
    await history.add_turn("telegram", "u1", "user", "third")
    await history.add_turn("telegram", "u1", "assistant", "reply3")

    messages = await history.get_messages("telegram", "u1")

    assert len(messages) == 4
    assert messages[0]["content"] == "second"
    assert messages[1]["content"] == "reply2"
    assert messages[2]["content"] == "third"
    assert messages[3]["content"] == "reply3"


@pytest.mark.asyncio
async def test_messages_include_timestamps(tmp_path) -> None:
    """Returned messages include a created_at key."""
    db_path = str(tmp_path / "agent.db")
    history = ConversationHistory(db_path=db_path, max_turns=5)

    await history.add_turn("telegram", "u1", "user", "hi")
    await history.add_turn("telegram", "u1", "assistant", "hello")

    messages = await history.get_messages("telegram", "u1")

    assert len(messages) == 2
    for msg in messages:
        assert "created_at" in msg
        assert msg["created_at"]  # non-empty


@pytest.mark.asyncio
async def test_clear_removes_history(tmp_path) -> None:
    db_path = str(tmp_path / "agent.db")
    history = ConversationHistory(db_path=db_path, max_turns=5)

    await history.add_turn("telegram", "u1", "user", "hi")
    await history.clear("telegram", "u1")

    messages = await history.get_messages("telegram", "u1")
    assert messages == []


# ---------------------------------------------------------------------------
# Session mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_append_and_retrieve(tmp_path) -> None:
    """Messages appended to a session are returned in order."""
    db_path = str(tmp_path / "agent.db")
    history = ConversationHistory(db_path=db_path)

    msg1 = {"role": "user", "content": "hello"}
    msg2 = {"role": "assistant", "content": "hi there"}
    await history.append_session_message("telegram", "u1", msg1)
    await history.append_session_message("telegram", "u1", msg2)

    session = await history.get_session("telegram", "u1")
    assert len(session) == 2
    assert session[0] == msg1
    assert session[1] == msg2


@pytest.mark.asyncio
async def test_session_append_multiple(tmp_path) -> None:
    """append_session_messages adds a batch in order."""
    db_path = str(tmp_path / "agent.db")
    history = ConversationHistory(db_path=db_path)

    msgs = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
    ]
    await history.append_session_messages("telegram", "u1", msgs)

    session = await history.get_session("telegram", "u1")
    assert session == msgs


@pytest.mark.asyncio
async def test_session_persists_across_instances(tmp_path) -> None:
    """Session data survives creating a new ConversationHistory instance."""
    db_path = str(tmp_path / "agent.db")

    h1 = ConversationHistory(db_path=db_path)
    await h1.append_session_message("telegram", "u1", {"role": "user", "content": "ping"})
    await h1.append_session_message("telegram", "u1", {"role": "assistant", "content": "pong"})

    # New instance — should load from DB
    h2 = ConversationHistory(db_path=db_path)
    session = await h2.get_session("telegram", "u1")
    assert len(session) == 2
    assert session[0]["content"] == "ping"
    assert session[1]["content"] == "pong"


@pytest.mark.asyncio
async def test_session_isolation_by_channel_user(tmp_path) -> None:
    """Sessions are isolated per (channel, user_id)."""
    db_path = str(tmp_path / "agent.db")
    history = ConversationHistory(db_path=db_path)

    await history.append_session_message("telegram", "u1", {"role": "user", "content": "tg-u1"})
    await history.append_session_message("whatsapp", "u1", {"role": "user", "content": "wa-u1"})
    await history.append_session_message("telegram", "u2", {"role": "user", "content": "tg-u2"})

    s1 = await history.get_session("telegram", "u1")
    s2 = await history.get_session("whatsapp", "u1")
    s3 = await history.get_session("telegram", "u2")

    assert len(s1) == 1 and s1[0]["content"] == "tg-u1"
    assert len(s2) == 1 and s2[0]["content"] == "wa-u1"
    assert len(s3) == 1 and s3[0]["content"] == "tg-u2"


@pytest.mark.asyncio
async def test_clear_removes_session(tmp_path) -> None:
    """clear() removes both injection history and session data."""
    db_path = str(tmp_path / "agent.db")
    history = ConversationHistory(db_path=db_path)

    await history.add_turn("telegram", "u1", "user", "injection msg")
    await history.append_session_message(
        "telegram", "u1", {"role": "user", "content": "session msg"}
    )

    await history.clear("telegram", "u1")

    assert await history.get_messages("telegram", "u1") == []
    session = await history.get_session("telegram", "u1")
    assert session == []


@pytest.mark.asyncio
async def test_clear_session_only(tmp_path) -> None:
    """clear_session() only removes session data, not injection history."""
    db_path = str(tmp_path / "agent.db")
    history = ConversationHistory(db_path=db_path)

    await history.add_turn("telegram", "u1", "user", "injection msg")
    await history.append_session_message(
        "telegram", "u1", {"role": "user", "content": "session msg"}
    )

    await history.clear_session("telegram", "u1")

    # Injection history still there
    injection = await history.get_messages("telegram", "u1")
    assert len(injection) == 1

    # Session is empty
    session = await history.get_session("telegram", "u1")
    assert session == []


@pytest.mark.asyncio
async def test_session_preserves_complex_messages(tmp_path) -> None:
    """Session correctly round-trips complex message structures (tool calls)."""
    db_path = str(tmp_path / "agent.db")
    history = ConversationHistory(db_path=db_path)

    tool_msg = {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "Let me search for that."},
            {
                "type": "tool_use",
                "id": "tool_123",
                "name": "web_search",
                "input": {"query": "weather today"},
            },
        ],
    }
    tool_result = {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "tool_123",
                "content": '{"results": []}',
            }
        ],
    }

    await history.append_session_messages("telegram", "u1", [tool_msg, tool_result])

    # New instance to force DB reload
    h2 = ConversationHistory(db_path=db_path)
    session = await h2.get_session("telegram", "u1")
    assert len(session) == 2
    assert session[0] == tool_msg
    assert session[1] == tool_result
