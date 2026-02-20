"""Materialize Himalaya config from the config store."""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

HIMALAYA_CONFIG_PATH = Path("/tmp/mpa-himalaya-config.toml")
HIMALAYA_XDG_DIR = Path("/tmp/mpa-himalaya-xdg")
HIMALAYA_XDG_CONFIG_PATH = HIMALAYA_XDG_DIR / "himalaya" / "config.toml"


def himalaya_env() -> dict[str, str]:
    return {
        "HIMALAYA_CONFIG": str(HIMALAYA_CONFIG_PATH),
        "XDG_CONFIG_HOME": str(HIMALAYA_XDG_DIR),
    }


async def materialize_himalaya_config(config_store) -> bool:
    """Write Himalaya TOML config from config DB.

    Returns True if a file was written or removed.
    """
    raw = await config_store.get("email.himalaya.toml")
    if not raw:
        removed = False
        if HIMALAYA_CONFIG_PATH.exists():
            HIMALAYA_CONFIG_PATH.unlink()
            removed = True
        if HIMALAYA_XDG_CONFIG_PATH.exists():
            HIMALAYA_XDG_CONFIG_PATH.unlink()
            removed = True
        return removed

    content = raw.strip()
    if content and not content.endswith("\n"):
        content += "\n"

    HIMALAYA_CONFIG_PATH.write_text(content)
    HIMALAYA_XDG_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    HIMALAYA_XDG_CONFIG_PATH.write_text(content)
    log.info("Materialized Himalaya config to %s", HIMALAYA_CONFIG_PATH)
    return True
