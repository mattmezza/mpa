"""Conversation history — persists user/assistant turns in SQLite."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import aiosqlite

log = logging.getLogger(__name__)

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS conversation_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at DATETIME DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_turns_lookup
    ON conversation_turns(channel, user_id, created_at);
"""


class ConversationHistory:
    """Stores and retrieves conversation turns per user+channel."""

    def __init__(self, db_path: str = "data/agent.db", max_turns: int = 20):
        self.db_path = db_path
        self.max_turns = max_turns
        self._ready = False

    async def _ensure_schema(self) -> None:
        if self._ready:
            return
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(_SCHEMA)
        self._ready = True

    async def get_messages(self, channel: str, user_id: str) -> list[dict]:
        """Return the last N turns as Anthropic-format messages."""
        await self._ensure_schema()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT role, content FROM (
                    SELECT role, content, created_at
                    FROM conversation_turns
                    WHERE channel = ? AND user_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                ) sub ORDER BY created_at ASC
                """,
                (channel, user_id, self.max_turns),
            )
            rows = await cursor.fetchall()

        return [{"role": role, "content": json.loads(content)} for role, content in rows]

    async def add_turn(self, channel: str, user_id: str, role: str, content: str) -> None:
        """Store a single message (user or assistant text)."""
        await self._ensure_schema()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO conversation_turns (channel, user_id, role, content) "
                "VALUES (?, ?, ?, ?)",
                (channel, user_id, role, json.dumps(content)),
            )
            await db.commit()

    async def clear(self, channel: str, user_id: str) -> None:
        """Clear conversation history for a user+channel pair."""
        await self._ensure_schema()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM conversation_turns WHERE channel = ? AND user_id = ?",
                (channel, user_id),
            )
            await db.commit()
