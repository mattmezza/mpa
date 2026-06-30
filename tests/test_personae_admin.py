"""Admin route tests for personae: CRUD, activate, and the tab partial."""

from __future__ import annotations

from typing import Any, cast

from fastapi.testclient import TestClient

from api.admin import AgentState, create_admin_app
from core.config import Config
from core.config_store import ConfigStore

AUTH = {"Authorization": "Bearer secret"}


class _Store:
    """Config-store stub backing get/set with a dict; personae live in tmp."""

    def __init__(self, tmp_path):
        self._data = {
            "agent.personae_db_path": str(tmp_path / "personae.db"),
            "agent.personae_dir": str(tmp_path / "seed"),  # missing = no gallery
            "history.db_path": str(tmp_path / "history.db"),
            "memory.db_path": str(tmp_path / "memory.db"),
        }

    async def is_setup_complete(self) -> bool:
        return True

    async def get(self, key: str):
        if key == "admin.password_hash":
            return "hash"
        if key == "admin.password_salt":
            return "salt"
        return self._data.get(key)

    async def set(self, key: str, value: str) -> None:
        self._data[key] = value

    async def verify_admin_password(self, password: str) -> bool:
        return password == "secret"

    async def get_all_redacted(self) -> dict:
        return {}


class _AgentStub:
    def __init__(self):
        self.config = Config()
        self.channels = {}
        self.job_store = None  # rename route reaches _get_job_store(); set per-test


def _client(tmp_path):
    agent = _AgentStub()
    app, _ = create_admin_app(
        AgentState(agent=cast(Any, agent)), cast(ConfigStore, _Store(tmp_path))
    )
    return TestClient(app), agent


def test_persona_crud_and_activation(tmp_path) -> None:
    client, agent = _client(tmp_path)

    # Create via the guided fields.
    r = client.post(
        "/personae",
        json={
            "name": "coach",
            "role": "Fitness coach",
            "emoji": "🏋️",
            "voice": "en-US-GuyNeural",
            "personalia": "You are Forge.",
            "character": "Direct.",
            "skills": ["memory"],
            "tools": ["run_command"],
            "secrets": ["persona:coach:*"],
        },
        headers=AUTH,
    )
    assert r.status_code == 200
    assert "Fitness coach" in r.text  # partial lists the new card

    # Read back via JSON API.
    got = client.get("/personae/coach", headers=AUTH).json()
    assert got["voice"] == "en-US-GuyNeural"
    assert got["skills"] == ["memory"] and got["tools"] == ["run_command"]

    # Activate → persisted and hot-applied to the running agent.
    r = client.post("/personae/activate", json={"name": "coach"}, headers=AUTH)
    assert r.status_code == 200
    assert "✓ Active" in r.text
    assert agent.config.agent.active_persona == "coach"

    # Deleting the active persona reverts to the default identity.
    r = client.post("/personae/delete", json={"name": "coach"}, headers=AUTH)
    assert r.status_code == 200
    assert agent.config.agent.active_persona == ""
    assert client.get("/personae/coach", headers=AUTH).status_code == 404


def test_persona_raw_markdown_upsert(tmp_path) -> None:
    client, _ = _client(tmp_path)
    raw = "---\nrole: Writer\nskills: [memory]\ntools: []\npersonalia: |\n  Editor.\n---\n"
    r = client.post("/personae", json={"name": "writer", "raw": raw}, headers=AUTH)
    assert r.status_code == 200
    got = client.get("/personae/writer", headers=AUTH).json()
    assert got["role"] == "Writer" and got["skills"] == ["memory"]
    assert "Editor." in got["personalia"]


def test_persona_bot_fields_persist(tmp_path) -> None:
    # Per-persona Telegram bot (#29): token + ACL survive the round-trip; the ACL
    # is parsed from a comma-separated string into ints; the token is REDACTED on
    # read (a secret, like the global Telegram token) but stored in full.
    client, _ = _client(tmp_path)
    r = client.post(
        "/personae",
        json={
            "name": "coach",
            "role": "Coach",
            "bot_token": "123456:ABC-DEF",
            "allowed_user_ids": "111, 222",
        },
        headers=AUTH,
    )
    assert r.status_code == 200
    got = client.get("/personae/coach", headers=AUTH).json()
    # Redacted on read, but head/tail prove the full value reached storage.
    assert "***" in got["bot_token"]
    assert got["bot_token"].startswith("1234") and got["bot_token"].endswith("-DEF")
    assert got["bot_token"] != "123456:ABC-DEF"
    assert "123456:ABC-DEF" not in got["markdown"]  # not leaked via the raw view either
    assert got["allowed_user_ids"] == [111, 222]


def test_activate_unknown_persona_404(tmp_path) -> None:
    client, _ = _client(tmp_path)
    assert (
        client.post("/personae/activate", json={"name": "ghost"}, headers=AUTH).status_code == 404
    )


def test_partial_personae_renders(tmp_path) -> None:
    client, _ = _client(tmp_path)
    r = client.get("/partials/personae", headers=AUTH)
    assert r.status_code == 200
    assert "Active persona" in r.text


def test_persona_rename_cascades(tmp_path) -> None:
    """Renaming a slug repoints the persona row, the active selection, per-chat
    bindings, the per-persona bot channel, private memory scope and jobs (#69)."""
    import asyncio

    import aiosqlite

    from core.history import ConversationHistory
    from core.job_store import JobStore
    from core.memory import MemoryStore

    client, agent = _client(tmp_path)
    history_db = str(tmp_path / "history.db")
    memory_db = str(tmp_path / "memory.db")
    agent.job_store = JobStore(db_path=str(tmp_path / "jobs.db"))

    assert (
        client.post("/personae", json={"name": "coach", "role": "Coach"}, headers=AUTH).status_code
        == 200
    )
    assert (
        client.post("/personae/activate", json={"name": "coach"}, headers=AUTH).status_code == 200
    )
    assert agent.config.agent.active_persona == "coach"

    async def seed() -> None:
        h = ConversationHistory(db_path=history_db)
        await h.set_chat_persona("telegram", "u1", "coach", "")  # default-bot binding
        await h.add_turn("telegram:coach", "u1", "user", "hi")  # the bot's own channel
        m = MemoryStore(db_path=memory_db)
        await m._ensure_schema()
        async with aiosqlite.connect(memory_db) as db:
            await db.execute(
                "INSERT INTO long_term (category, subject, content, scope) "
                "VALUES ('pref','s','c','coach')"
            )
            await db.commit()
        await agent.job_store.upsert_job("j1", task="t", channel="telegram:coach", persona="coach")

    asyncio.run(seed())

    r = client.post("/personae/rename", json={"old": "coach", "new": "trainer"}, headers=AUTH)
    assert r.status_code == 200
    assert client.get("/personae/coach", headers=AUTH).status_code == 404
    assert client.get("/personae/trainer", headers=AUTH).status_code == 200
    assert agent.config.agent.active_persona == "trainer"

    async def check() -> None:
        h = ConversationHistory(db_path=history_db)
        assert await h.get_chat_persona("telegram", "u1", "") == "trainer"
        async with aiosqlite.connect(history_db) as db:
            cur = await db.execute("SELECT channel FROM conversation_turns WHERE user_id='u1'")
            assert [row[0] for row in await cur.fetchall()] == ["telegram:trainer"]
        async with aiosqlite.connect(memory_db) as db:
            cur = await db.execute("SELECT scope FROM long_term")
            assert [row[0] for row in await cur.fetchall()] == ["trainer"]
        job = await agent.job_store.get_job("j1")
        assert job["persona"] == "trainer" and job["channel"] == "telegram:trainer"

    asyncio.run(check())


def test_persona_rename_validation(tmp_path) -> None:
    client, _ = _client(tmp_path)
    client.post("/personae", json={"name": "coach", "role": "C"}, headers=AUTH)
    client.post("/personae", json={"name": "writer", "role": "W"}, headers=AUTH)

    # Collision with an existing slug → 409.
    assert (
        client.post(
            "/personae/rename", json={"old": "coach", "new": "writer"}, headers=AUTH
        ).status_code
        == 409
    )
    # Illegal characters (the ':' would break channel routing) → 400.
    assert (
        client.post(
            "/personae/rename", json={"old": "coach", "new": "te:am"}, headers=AUTH
        ).status_code
        == 400
    )
    # Unknown source slug → 404.
    assert (
        client.post("/personae/rename", json={"old": "ghost", "new": "x"}, headers=AUTH).status_code
        == 404
    )
    # Renaming to the same slug is a harmless no-op.
    assert (
        client.post(
            "/personae/rename", json={"old": "coach", "new": "coach"}, headers=AUTH
        ).status_code
        == 200
    )
