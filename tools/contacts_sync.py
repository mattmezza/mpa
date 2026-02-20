#!/usr/bin/env python3
"""Contacts sync helper — runs vdirsyncer using admin-stored config."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path


def _setup_sys_path() -> None:
    root = Path(__file__).resolve().parent.parent
    os.sys.path.insert(0, str(root))


async def main() -> None:
    parser = argparse.ArgumentParser(description="Sync contacts via vdirsyncer")
    parser.add_argument(
        "--config-db",
        default="data/config.db",
        help="Path to the config DB (default: data/config.db)",
    )
    parser.add_argument("--discover", action="store_true", help="Run vdirsyncer discover")
    args = parser.parse_args()

    _setup_sys_path()
    from core.config_store import ConfigStore
    from core.contacts_config import (
        list_contact_pairs,
        materialize_vdirsyncer_config,
        vdirsyncer_env,
    )

    store = ConfigStore(db_path=args.config_db)
    await store._ensure_schema()
    await materialize_vdirsyncer_config(store)

    cmd = ["vdirsyncer", "sync"]
    if args.discover:
        pairs = await list_contact_pairs(store)
        cmd = ["vdirsyncer", "discover", *pairs] if pairs else ["vdirsyncer", "discover"]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        env={**os.environ, **vdirsyncer_env()},
    )
    raise SystemExit(await proc.wait())


if __name__ == "__main__":
    asyncio.run(main())
