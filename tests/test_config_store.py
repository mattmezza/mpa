"""Tests for ConfigStore helpers and CRUD."""

from __future__ import annotations

import pytest

from core.config_store import ConfigStore, _flatten, _parse_value, _unflatten


def test_flatten_and_unflatten_round_trip() -> None:
    data = {
        "agent": {"name": "Ada", "enabled": True},
        "channels": {"telegram": {"allowed_user_ids": [1, 2]}},
    }
    flat = _flatten(data)
    assert flat["agent.name"] == "Ada"
    assert flat["agent.enabled"] == "True"
    assert flat["channels.telegram.allowed_user_ids"] == "[1, 2]"

    nested = _unflatten(flat)
    assert nested["agent"]["name"] == "Ada"
    assert nested["agent"]["enabled"] is True
    assert nested["channels"]["telegram"]["allowed_user_ids"] == [1, 2]


def test_parse_value_handles_int_bool_json() -> None:
    assert _parse_value("42") == 42
    assert _parse_value("true") is True
    assert _parse_value("false") is False
    assert _parse_value("[1, 2]") == [1, 2]


@pytest.mark.asyncio
async def test_embedding_config_roundtrips_to_nested_model(tmp_path) -> None:
    """UI-saved flat memory.embedding.* keys reconstruct EmbeddingConfig."""
    store = ConfigStore(db_path=str(tmp_path / "config.db"))
    await store.set_many(
        {
            "memory.embedding.enabled": "false",
            "memory.embedding.provider": "openai",
            "memory.embedding.model": "text-embedding-3-small",
            "memory.embedding.injection_top_k": "20",
            "memory.hygiene_enabled": "false",
            "memory.default_importance": "7.5",
        }
    )
    config = await store.export_to_config()
    emb = config.memory.embedding
    assert emb.enabled is False
    assert emb.provider == "openai"
    assert emb.model == "text-embedding-3-small"
    assert emb.injection_top_k == 20
    assert config.memory.hygiene_enabled is False
    assert config.memory.default_importance == 7.5


@pytest.mark.asyncio
async def test_group_chat_config_roundtrips_to_nested_model(tmp_path) -> None:
    """UI-saved flat channels.telegram.group_chat.* keys reconstruct GroupChatConfig (#30)."""
    store = ConfigStore(db_path=str(tmp_path / "config.db"))
    await store.set_many(
        {
            "channels.telegram.group_chat.enabled": "true",
            "channels.telegram.group_chat.reply_when_addressed_only": "false",
            "channels.telegram.group_chat.ignore_bots": "true",
        }
    )
    config = await store.export_to_config()
    gc = config.channels.telegram.group_chat
    assert gc.enabled is True
    assert gc.reply_when_addressed_only is False
    assert gc.ignore_bots is True


@pytest.mark.asyncio
async def test_set_get_delete(tmp_path) -> None:
    store = ConfigStore(db_path=str(tmp_path / "config.db"))

    await store.set("agent.name", "Clio")
    assert await store.get("agent.name") == "Clio"

    deleted = await store.delete("agent.name")
    assert deleted is True
    assert await store.get("agent.name") is None


@pytest.mark.asyncio
async def test_email_config_materializes_on_set(tmp_path, monkeypatch) -> None:
    store = ConfigStore(db_path=str(tmp_path / "config.db"))
    called = []

    async def _fake_materialize(_store):
        called.append(True)
        return True

    monkeypatch.setattr("core.config_store.materialize_himalaya_config", _fake_materialize)

    await store.set("email.himalaya.toml", "[accounts.personal]\n")

    assert called
