"""Tests for Himalaya config materialization."""

from __future__ import annotations

from pathlib import Path

import pytest

from core import email_config


class _Store:
    def __init__(self, value: str | None) -> None:
        self.value = value

    async def get(self, key: str) -> str | None:
        if key == "email.himalaya.toml":
            return self.value
        return None


@pytest.mark.asyncio
async def test_materialize_writes_tmp_files(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "himalaya.toml"
    xdg_dir = tmp_path / "xdg"
    xdg_config_path = xdg_dir / "himalaya" / "config.toml"

    monkeypatch.setattr(email_config, "HIMALAYA_CONFIG_PATH", config_path)
    monkeypatch.setattr(email_config, "HIMALAYA_XDG_DIR", xdg_dir)
    monkeypatch.setattr(email_config, "HIMALAYA_XDG_CONFIG_PATH", xdg_config_path)

    store = _Store('[accounts.personal]\nemail = "you@example.com"\n')
    changed = await email_config.materialize_himalaya_config(store)

    assert changed is True
    assert config_path.read_text() == store.value
    assert xdg_config_path.read_text() == store.value


@pytest.mark.asyncio
async def test_materialize_removes_tmp_files(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "himalaya.toml"
    xdg_dir = tmp_path / "xdg"
    xdg_config_path = xdg_dir / "himalaya" / "config.toml"
    config_path.write_text("[accounts.personal]\n")
    xdg_config_path.parent.mkdir(parents=True, exist_ok=True)
    xdg_config_path.write_text("[accounts.personal]\n")

    monkeypatch.setattr(email_config, "HIMALAYA_CONFIG_PATH", config_path)
    monkeypatch.setattr(email_config, "HIMALAYA_XDG_DIR", xdg_dir)
    monkeypatch.setattr(email_config, "HIMALAYA_XDG_CONFIG_PATH", xdg_config_path)

    store = _Store("")
    changed = await email_config.materialize_himalaya_config(store)

    assert changed is True
    assert not config_path.exists()
    assert not xdg_config_path.exists()
