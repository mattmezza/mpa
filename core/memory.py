"""Memory store — initializes schema and queries memories for prompt injection."""

from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite

log = logging.getLogger(__name__)

_SCHEMA_FILE = Path(__file__).resolve().parent.parent / "schema" / "memory.sql"


class MemoryStore:
    """Two-tier memory system backed by SQLite.

    The LLM reads and writes memories via the sqlite3 CLI (taught by
    skills/memory.md).  This class handles schema initialisation and
    provides async helpers to query both tiers for injection into the
    system prompt.
    """

    def __init__(self, db_path: str = "data/memory.db", long_term_limit: int = 50):
        self.db_path = db_path
        self.long_term_limit = long_term_limit
        self._ready = False

    async def _ensure_schema(self) -> None:
        if self._ready:
            return
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        schema = _SCHEMA_FILE.read_text()
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(schema)
        self._ready = True

    async def get_long_term(self) -> list[dict]:
        """Retrieve long-term memories for system prompt injection."""
        await self._ensure_schema()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT category, subject, content FROM long_term ORDER BY updated_at DESC LIMIT ?",
                (self.long_term_limit,),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def get_short_term(self) -> list[dict]:
        """Retrieve active (non-expired) short-term memories."""
        await self._ensure_schema()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT content, context FROM short_term "
                "WHERE expires_at > datetime('now') "
                "ORDER BY created_at DESC",
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def format_for_prompt(self) -> str:
        """Format both tiers into a block for the system prompt."""
        sections: list[str] = []

        long_term = await self.get_long_term()
        if long_term:
            lines = [f"- [{m['category']}] {m['subject']}: {m['content']}" for m in long_term]
            sections.append("## Long-term memories\n" + "\n".join(lines))

        short_term = await self.get_short_term()
        if short_term:
            lines = []
            for m in short_term:
                entry = f"- {m['content']}"
                if m.get("context"):
                    entry += f" ({m['context']})"
                lines.append(entry)
            sections.append("## Current context (short-term)\n" + "\n".join(lines))

        return "\n\n".join(sections) if sections else ""
