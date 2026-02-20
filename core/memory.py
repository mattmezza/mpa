"""Memory store — initializes schema, queries memories, and extracts new ones."""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite

from core.llm import LLMClient

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

{existing_memories_block}\
For each fact, classify it into ONE of these tiers:

LONG_TERM — durable facts that remain true indefinitely:
  - Personal preferences ("prefers oat milk", "favourite editor is Neovim")
  - Relationships ("Marco is Matteo's colleague", "Simge is Matteo's partner")
  - Biographical facts ("lives in Zurich", "works as a software engineer")
  - Routines ("goes to the gym on Mondays and Thursdays")
  DO NOT use LONG_TERM for:
  - Plans, tasks, or events (even recurring ones that haven't been confirmed as routines)
  - Anything time-bound ("working on project X", "has a deadline Friday")
  - Opinions about transient topics ("thinks the new API is buggy")

SHORT_TERM — situational context that expires:
  - Current activities ("working from home today", "debugging the auth flow")
  - Near-term plans ("dinner with Marco tomorrow", "flight to Rome on Friday")
  - Temporary states ("feeling tired", "waiting for a code review")
  - Active projects or tasks ("refactoring the memory system this week")

When in doubt between LONG_TERM and SHORT_TERM, choose SHORT_TERM. Only use
LONG_TERM for facts you are highly confident will still be true months from now.

Return a JSON array (max 3 items). Each element must be one of:
  {{"tier": "LONG_TERM", "category": "<category>", \
"subject": "<who/what>", "content": "<the fact>"}}
  {{"tier": "SHORT_TERM", "content": "<the fact>", \
"context": "<why stored>", "ttl_hours": <int>}}

Categories: preference, relationship, fact, routine, work, health, travel

Rules:
- Extract only genuinely useful, non-obvious facts. Skip greetings, filler,
  acknowledgements, and anything already obvious from the conversation.
- Most conversation turns contain NOTHING worth remembering. Return [] liberally.
- Do NOT extract facts that duplicate or overlap with the existing memories
  listed above.
- Use lowercase for subject (e.g. "matteo", "simge").
- For LONG_TERM, always set category and subject.
- For SHORT_TERM, you MUST set ttl_hours:
  - 2-4h: trivial, task-at-hand context ("looking at flights now")
  - 8-12h: day-scoped situations ("working from home today")
  - 24-48h: near-term plans ("dinner with Marco tomorrow")
  - 72-168h: week-scoped context ("Simge visiting parents this week")
- If nothing is worth remembering, return an empty array: []

Respond with ONLY the JSON array, no other text."""

# Regex to match a fenced code block: ```json ... ``` (or just ``` ... ```)
_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL)


def _extract_json_array(raw: str) -> list | None:
    """Best-effort extraction of a JSON array from an LLM response.

    Handles common quirks:
    - Markdown code fences (```json ... ```)
    - Preamble / trailing prose around the JSON
    - Leading/trailing whitespace
    - Empty responses

    Returns the parsed list on success, or ``None`` if no valid JSON
    array could be extracted.
    """
    raw = raw.strip()
    if not raw:
        return None

    # 1. Try parsing the raw response directly (happy path).
    try:
        result = json.loads(raw)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

    # 2. Try extracting from markdown code fences.
    fence_match = _FENCE_RE.search(raw)
    if fence_match:
        try:
            result = json.loads(fence_match.group(1).strip())
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    # 3. Find the outermost [ ... ] bracket pair in the response.
    start = raw.find("[")
    if start != -1:
        # Walk forward to find the matching closing bracket.
        depth = 0
        in_string = False
        escape = False
        end = -1
        for i in range(start, len(raw)):
            ch = raw[i]
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end != -1:
            try:
                result = json.loads(raw[start : end + 1])
                if isinstance(result, list):
                    return result
            except json.JSONDecodeError:
                pass

    return None


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
        self._last_extraction: float = 0.0  # monotonic timestamp of last extraction

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

    # Maximum number of memories to store per extraction call.
    _MAX_PER_TURN = 3

    async def extract_memories(
        self,
        llm: LLMClient,
        model: str,
        user_msg: str,
        agent_msg: str,
        cooldown_seconds: int = 120,
    ) -> int:
        """Extract facts from a conversation turn and store them.

        Makes a secondary LLM call (cheap/fast model) to identify facts
        worth remembering, then writes them to the appropriate tier.

        If fewer than *cooldown_seconds* have elapsed since the last
        extraction call, the call is skipped entirely (returns 0).

        Returns the number of memories stored.
        """
        now = time.monotonic()
        if now - self._last_extraction < cooldown_seconds:
            log.debug(
                "Skipping memory extraction (cooldown: %.0fs remaining)",
                cooldown_seconds - (now - self._last_extraction),
            )
            return 0
        self._last_extraction = now

        # Build existing-memories block so the LLM can avoid duplicates.
        existing_block = await self._existing_memories_block()

        prompt = _EXTRACTION_PROMPT.format(
            user_msg=user_msg,
            agent_msg=agent_msg,
            existing_memories_block=existing_block,
        )

        try:
            raw = await llm.generate_text(model=model, prompt=prompt, max_tokens=1024)
        except Exception:
            log.exception("Memory extraction LLM call failed")
            return 0

        memories = _extract_json_array(raw)
        if memories is None:
            log.warning("Memory extraction returned non-JSON: %s", raw[:200])
            return 0

        stored = 0
        for mem in memories[: self._MAX_PER_TURN]:
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

        if stored == 0:
            log.info("Memory extraction stored 0 items (model=%s)", model)

        if stored:
            log.info("Extracted and stored %d memories", stored)
        return stored

    async def _existing_memories_block(self) -> str:
        """Build a summary of existing memories for the extraction prompt."""
        long_term = await self.get_long_term()
        short_term = await self.get_short_term()

        if not long_term and not short_term:
            return ""

        parts = ["## Existing memories (do NOT extract duplicates)\n"]
        if long_term:
            for m in long_term:
                parts.append(f"- [LT] {m['subject']}: {m['content']}")
        if short_term:
            for m in short_term:
                parts.append(f"- [ST] {m['content']}")
        parts.append("")  # trailing newline
        return "\n".join(parts) + "\n"

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
        """Store a short-term memory with a LLM-determined TTL.

        Skips insertion if an active (non-expired) short-term memory
        already exists with overlapping content.
        """
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
            # Check for duplicate active short-term memories
            cursor = await db.execute(
                "SELECT id, content FROM short_term WHERE expires_at > datetime('now')",
            )
            existing = await cursor.fetchall()
            content_lower = content.lower()
            for row in existing:
                if content_lower in row[1].lower() or row[1].lower() in content_lower:
                    log.debug("Skipping duplicate short-term memory: %s", content[:80])
                    return 0

            await db.execute(
                "INSERT INTO short_term (content, context, expires_at) VALUES (?, ?, ?)",
                (content, context, expires_str),
            )
            await db.commit()
            log.debug("Stored short-term memory (TTL %dh): %s", ttl_hours, content[:80])
            return 1

    # -- Consolidation & cleanup --

    async def consolidate_and_cleanup(self, llm: LLMClient, model: str) -> dict:
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
        self, llm: LLMClient, model: str, short_term_rows: list[dict]
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
            raw = await llm.generate_text(model=model, prompt=prompt, max_tokens=1024)
        except Exception:
            log.exception("Consolidation LLM call failed")
            return 0

        promotions = _extract_json_array(raw)
        if promotions is None:
            log.warning("Consolidation LLM returned non-JSON: %s", raw[:200])
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
