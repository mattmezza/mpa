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
        """Return the last *max_turns* user-assistant **pairs** with timestamps.

        Each returned dict has ``role``, ``content`` and ``created_at`` keys.
        The limit is applied to *pairs* (not individual rows) so the history
        never starts with an orphaned assistant reply.
        """
        await self._ensure_schema()
        # We select the N most-recent *user* rows by id and then grab every
        # row whose id >= the smallest of those.  Because the assistant reply
        # is always inserted right after the user message, this guarantees we
        # never slice in the middle of a pair.
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT role, content, created_at FROM conversation_turns
                WHERE channel = ? AND user_id = ?
                  AND id >= (
                      SELECT MIN(id) FROM (
                          SELECT id
                          FROM conversation_turns
                          WHERE channel = ? AND user_id = ? AND role = 'user'
                          ORDER BY id DESC
                          LIMIT ?
                      )
                  )
                ORDER BY id ASC
                """,
                (channel, user_id, channel, user_id, self.max_turns),
            )
            rows = await cursor.fetchall()

        return [
            {"role": role, "content": json.loads(content), "created_at": created_at}
            for role, content, created_at in rows
        ]

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
