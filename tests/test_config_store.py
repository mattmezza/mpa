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
async def test_set_get_delete(tmp_path) -> None:
    store = ConfigStore(db_path=str(tmp_path / "config.db"))

    await store.set("agent.name", "Clio")
    assert await store.get("agent.name") == "Clio"

    deleted = await store.delete("agent.name")
    assert deleted is True
    assert await store.get("agent.name") is None
