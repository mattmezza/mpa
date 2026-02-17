"""Tests for MemoryStore.extract_memories."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from core.memory import MemoryStore


@pytest.fixture
async def store(tmp_path):
    db_path = str(tmp_path / "memory.db")
    memory = MemoryStore(db_path=db_path, long_term_limit=50)
    await memory._ensure_schema()
    return memory


def _make_mock_llm(response_json):
    llm = AsyncMock()
    text_block = SimpleNamespace(type="text", text=json.dumps(response_json))
    llm.messages.create.return_value = SimpleNamespace(content=[text_block])
    return llm


async def _count_rows(db_path: str, table: str) -> int:
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(f"SELECT COUNT(*) FROM {table}")  # noqa: S608
        row = await cursor.fetchone()
        return row[0]


@pytest.mark.asyncio
async def test_extract_memories_stores_long_and_short_term(store) -> None:
    llm = _make_mock_llm(
        [
            {
                "tier": "LONG_TERM",
                "category": "preference",
                "subject": "matteo",
                "content": "Prefers oat milk",
            },
            {
                "tier": "SHORT_TERM",
                "content": "Working from home today",
                "context": "daily update",
                "ttl_hours": 8,
            },
        ]
    )

    stored = await store.extract_memories(
        llm,
        model="claude-haiku-4-5",
        user_msg="I like oat milk",
        agent_msg="Noted",
    )

    assert stored == 2
    assert await _count_rows(store.db_path, "long_term") == 1
    assert await _count_rows(store.db_path, "short_term") == 1


@pytest.mark.asyncio
async def test_extract_memories_skips_invalid_short_term(store) -> None:
    llm = _make_mock_llm(
        [
            {
                "tier": "SHORT_TERM",
                "content": "No ttl provided",
                "context": "oops",
            }
        ]
    )

    stored = await store.extract_memories(
        llm,
        model="claude-haiku-4-5",
        user_msg="",
        agent_msg="",
    )

    assert stored == 0
    assert await _count_rows(store.db_path, "short_term") == 0


@pytest.mark.asyncio
async def test_extract_memories_strips_markdown_code_fences(store) -> None:
    """LLMs sometimes wrap JSON in ```json ... ``` fences."""
    llm = AsyncMock()
    fenced = '```json\n[{"tier": "LONG_TERM", "category": "fact", "subject": "marco", "content": "Email is marco@example.com"}]\n```'
    text_block = SimpleNamespace(type="text", text=fenced)
    llm.messages.create.return_value = SimpleNamespace(content=[text_block])

    stored = await store.extract_memories(
        llm,
        model="claude-haiku-4-5",
        user_msg="My email is marco@example.com",
        agent_msg="Got it",
    )

    assert stored == 1
    assert await _count_rows(store.db_path, "long_term") == 1
