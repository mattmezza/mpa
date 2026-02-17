"""Memory store — initializes schema, queries memories, and extracts new ones."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
from anthropic import AsyncAnthropic

log = logging.getLogger(__name__)

_SCHEMA_FILE = Path(__file__).resolve().parent.parent / "schema" / "memory.sql"

_CONSOLIDATION_PROMPT = """\
You are reviewing short-term memories stored by a personal AI assistant.
Your job is to decide which short-term memories contain facts worth keeping
permanently, and compact them into long-term memories.

## Existing long-term memories (for deduplication)
{existing_long_term}

## Short-term memories to review
{short_term_entries}

For each short-term memory, decide:
1. PROMOTE — the fact is durable and worth keeping. Compact it aggressively
   into a short, dense long-term memory. Strip dates, times, and situational
   framing. Keep only the core fact.
   Examples:
   - "Matteo is at the airport, flight to Rome boards at 15:40" → discard (ephemeral)
   - "Matteo mentioned he has a standing lunch with Marco every Friday" → promote: \
"Has a standing Friday lunch with Marco"
   - "Simge is visiting her parents this weekend" → discard (time-bound)
   - "Matteo said he switched to a standing desk at work" → promote: \
"Uses a standing desk at work"
2. DISCARD — the fact is time-bound, stale, or already captured in long-term memory.

Return a JSON array of objects to promote. Each object:
  {{"category": "<category>", "subject": "<who/what>", \
"content": "<compacted fact>"}}

Categories: preference, relationship, fact, routine, work, health, travel

Rules:
- Be ruthless. Most short-term memories should be discarded.
- Only promote facts that would still be useful weeks or months from now.
- Compact aggressively: strip temporal context, keep the core fact.
- Do NOT promote anything that duplicates or overlaps with existing long-term memories.
- If a short-term memory refines or updates an existing long-term memory,
  promote it with the updated content (it will replace the old one).
- Use lowercase for subject (e.g. "matteo", "simge").
- If nothing is worth promoting, return an empty array: []

Respond with ONLY the JSON array, no other text."""

_EXTRACTION_PROMPT = """\
Given this conversation exchange, identify any facts worth remembering.

User: {user_msg}
Assistant: {agent_msg}

For each fact, classify it:
- LONG_TERM: preferences, relationships, routines, biographical facts — things that stay true
- SHORT_TERM: situational context, temporary states, time-bound info — things that expire

Return a JSON array. Each element must be one of:
  {{"tier": "LONG_TERM", "category": "<category>", \
"subject": "<who/what>", "content": "<the fact>"}}
  {{"tier": "SHORT_TERM", "content": "<the fact>", \
"context": "<why stored>", "ttl_hours": <int>}}

Categories: preference, relationship, fact, routine, work, health, travel

Rules:
- Only extract genuinely useful facts. Skip greetings, filler,
  and anything already obvious from context.
- Use lowercase for subject (e.g. "matteo", "simge").
- For LONG_TERM, always set category and subject.
- For SHORT_TERM, you MUST set ttl_hours by reasoning about how long
  the fact stays relevant. Guidelines:
  - 2-4h: trivial, task-at-hand context ("looking at flights now")
  - 8-12h: day-scoped situations ("working from home today")
  - 24-48h: near-term plans ("dinner with Marco tomorrow")
  - 72-168h: week-scoped context ("Simge visiting parents this week")
  - If the user says when to forget ("remind me tomorrow", "for the
    next two days"), use that as the TTL.
- If nothing is worth remembering, return an empty array: []

Respond with ONLY the JSON array, no other text."""


class MemoryStore:
    """Two-tier memory system backed by SQLite.

    The LLM reads and writes memories via the sqlite3 CLI (taught by
    skills/memory.md).  This class handles schema initialisation,
    provides async helpers to query both tiers for injection into the
    system prompt, and runs automatic memory extraction after each
    conversation turn.
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

    # -- Automatic memory extraction --

    async def extract_memories(
        self, llm: AsyncAnthropic, model: str, user_msg: str, agent_msg: str
    ) -> int:
        """Extract facts from a conversation turn and store them.

        Makes a secondary LLM call (cheap/fast model) to identify facts
        worth remembering, then writes them to the appropriate tier.

        Returns the number of memories stored.
        """
        prompt = _EXTRACTION_PROMPT.format(user_msg=user_msg, agent_msg=agent_msg)

        try:
            response = await llm.messages.create(
                model=model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception:
            log.exception("Memory extraction LLM call failed")
            return 0

        raw = ""
        for block in response.content:
            if hasattr(block, "text"):
                raw = block.text.strip()
                break
        # Strip markdown code fences if the LLM wrapped its response
        if raw.startswith("```"):
            # Remove opening fence (```json or ```)
            raw = raw.split("\n", 1)[-1]
            # Remove closing fence
            if raw.endswith("```"):
                raw = raw[:-3].rstrip()
        try:
            memories = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("Memory extraction returned non-JSON: %s", raw[:200])
            return 0

        if not isinstance(memories, list):
            log.warning("Memory extraction returned non-list: %s", type(memories).__name__)
            return 0

        stored = 0
        for mem in memories:
            try:
                tier = mem.get("tier", "").upper()
                if tier == "LONG_TERM":
                    stored += await self._store_long_term(mem)
                elif tier == "SHORT_TERM":
                    stored += await self._store_short_term(mem)
                else:
                    log.warning("Unknown memory tier: %s", tier)
            except Exception:
                log.exception("Failed to store extracted memory: %s", mem)

        if stored:
            log.info("Extracted and stored %d memories", stored)
        return stored

    async def _store_long_term(self, mem: dict) -> int:
        """Store a long-term memory, skipping if a similar one exists."""
        category = mem.get("category", "fact")
        subject = mem.get("subject", "")
        content = mem.get("content", "")
        if not content:
            return 0

        await self._ensure_schema()
        async with aiosqlite.connect(self.db_path) as db:
            # Check for duplicates: same subject + overlapping content
            cursor = await db.execute(
                "SELECT id, content FROM long_term WHERE subject = ?",
                (subject,),
            )
            existing = await cursor.fetchall()
            content_lower = content.lower()
            for row in existing:
                if content_lower in row[1].lower() or row[1].lower() in content_lower:
                    # Update the existing memory if the new content is more detailed
                    if len(content) > len(row[1]):
                        await db.execute(
                            "UPDATE long_term SET content = ?, updated_at = datetime('now') "
                            "WHERE id = ?",
                            (content, row[0]),
                        )
                        await db.commit()
                        log.debug("Updated long-term memory %d: %s", row[0], content[:80])
                    return 0

            await db.execute(
                "INSERT INTO long_term (category, subject, content, source, confidence) "
                "VALUES (?, ?, ?, 'conversation', 'stated')",
                (category, subject, content),
            )
            await db.commit()
            log.debug("Stored long-term memory: [%s] %s: %s", category, subject, content[:80])
            return 1

    async def _store_short_term(self, mem: dict) -> int:
        """Store a short-term memory with a LLM-determined TTL."""
        content = mem.get("content", "")
        context = mem.get("context", "")
        ttl_hours = mem.get("ttl_hours")
        if not content:
            return 0
        if not ttl_hours or not isinstance(ttl_hours, int | float):
            log.warning("Short-term memory missing ttl_hours, skipping: %s", content[:80])
            return 0

        expires_at = datetime.now(tz=UTC) + timedelta(hours=ttl_hours)
        # Store in SQLite-compatible format (no timezone suffix, always UTC)
        expires_str = expires_at.strftime("%Y-%m-%d %H:%M:%S")

        await self._ensure_schema()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO short_term (content, context, expires_at) VALUES (?, ?, ?)",
                (content, context, expires_str),
            )
            await db.commit()
            log.debug("Stored short-term memory (TTL %dh): %s", ttl_hours, content[:80])
            return 1

    # -- Consolidation & cleanup --

    async def consolidate_and_cleanup(self, llm: AsyncAnthropic, model: str) -> dict:
        """Consolidate short-term memories and clean up expired ones.

        1. Fetch all non-expired short-term memories.
        2. Ask the LLM which ones should be promoted to long-term (compacted).
        3. Store the promotions.
        4. Delete all expired short-term rows.

        Returns a summary dict with counts.
        """
        await self._ensure_schema()

        # Fetch non-expired short-term memories (with IDs for logging)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, content, context, created_at, expires_at FROM short_term "
                "WHERE expires_at > datetime('now') "
                "ORDER BY created_at ASC",
            )
            active_short_term = [dict(row) for row in await cursor.fetchall()]

        promoted = 0
        if active_short_term:
            promoted = await self._run_consolidation_llm(llm, model, active_short_term)

        # Delete all expired short-term memories
        expired_count = await self._delete_expired_short_term()

        summary = {
            "active_reviewed": len(active_short_term),
            "promoted_to_long_term": promoted,
            "expired_deleted": expired_count,
        }
        log.info(
            "Memory consolidation complete: %d active reviewed, %d promoted, %d expired deleted",
            summary["active_reviewed"],
            summary["promoted_to_long_term"],
            summary["expired_deleted"],
        )
        return summary

    async def _run_consolidation_llm(
        self, llm: AsyncAnthropic, model: str, short_term_rows: list[dict]
    ) -> int:
        """Ask the LLM which short-term memories to promote to long-term."""
        # Build the short-term entries block
        st_lines = []
        for row in short_term_rows:
            entry = f"- {row['content']}"
            if row.get("context"):
                entry += f" (context: {row['context']})"
            st_lines.append(entry)
        st_block = "\n".join(st_lines)

        # Build existing long-term summary for deduplication
        long_term = await self.get_long_term()
        if long_term:
            lt_lines = [f"- [{m['category']}] {m['subject']}: {m['content']}" for m in long_term]
            lt_block = "\n".join(lt_lines)
        else:
            lt_block = "(none)"

        prompt = _CONSOLIDATION_PROMPT.format(
            existing_long_term=lt_block,
            short_term_entries=st_block,
        )

        try:
            response = await llm.messages.create(
                model=model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception:
            log.exception("Consolidation LLM call failed")
            return 0

        raw = ""
        for block in response.content:
            if hasattr(block, "text"):
                raw = block.text.strip()
                break

        try:
            promotions = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("Consolidation LLM returned non-JSON: %s", raw[:200])
            return 0

        if not isinstance(promotions, list):
            log.warning("Consolidation LLM returned non-list: %s", type(promotions).__name__)
            return 0

        stored = 0
        for mem in promotions:
            try:
                count = await self._store_long_term(
                    {
                        "category": mem.get("category", "fact"),
                        "subject": mem.get("subject", ""),
                        "content": mem.get("content", ""),
                    }
                )
                stored += count
            except Exception:
                log.exception("Failed to store promoted memory: %s", mem)

        if stored:
            log.info("Consolidation promoted %d short-term memories to long-term", stored)
        return stored

    async def _delete_expired_short_term(self) -> int:
        """Delete all expired short-term memories. Returns the count deleted."""
        await self._ensure_schema()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM short_term WHERE expires_at < datetime('now')")
            count = cursor.rowcount
            await db.commit()
            if count:
                log.info("Deleted %d expired short-term memories", count)
            return count
