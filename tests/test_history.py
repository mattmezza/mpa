"""Tests for ConversationHistory."""

from __future__ import annotations

import pytest

from core.history import ConversationHistory


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
