"""Tests for ConversationHistory."""

from __future__ import annotations

import pytest

from core.history import ConversationHistory


@pytest.mark.asyncio
async def test_add_and_get_messages_respects_order_and_limit(tmp_path) -> None:
    db_path = str(tmp_path / "agent.db")
    history = ConversationHistory(db_path=db_path, max_turns=2)

    await history.add_turn("telegram", "u1", "user", "hi")
    await history.add_turn("telegram", "u1", "assistant", "hello")
    await history.add_turn("telegram", "u1", "user", "how are you")

    messages = await history.get_messages("telegram", "u1")

    assert messages == [
        {"role": "user", "content": "how are you"},
        {"role": "assistant", "content": "hello"},
    ]


@pytest.mark.asyncio
async def test_clear_removes_history(tmp_path) -> None:
    db_path = str(tmp_path / "agent.db")
    history = ConversationHistory(db_path=db_path, max_turns=5)

    await history.add_turn("telegram", "u1", "user", "hi")
    await history.clear("telegram", "u1")

    messages = await history.get_messages("telegram", "u1")
    assert messages == []
