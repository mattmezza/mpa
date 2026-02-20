"""OAuth helper for Google Contacts (People API)."""

from __future__ import annotations

import json
import sqlite3
import sys
import time

import requests

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
CONFIG_DB_PATH = "data/config.db"
TOKEN_DB_KEY = "contacts.google_oauth_token"

_token_cache: tuple[str, float] | None = None


def _load_token_from_db(db_path: str = CONFIG_DB_PATH) -> dict | None:
    try:
        db = sqlite3.connect(db_path)
        row = db.execute("SELECT value FROM config WHERE key = ?", (TOKEN_DB_KEY,)).fetchone()
        db.close()
        if row:
            return json.loads(row[0])
    except Exception:
        pass
    return None


def _refresh_access_token(token_data: dict) -> tuple[str, int]:
    resp = requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "client_id": token_data["client_id"],
            "client_secret": token_data["client_secret"],
            "refresh_token": token_data["refresh_token"],
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["access_token"], data.get("expires_in", 3600)


def get_google_access_token(db_path: str = CONFIG_DB_PATH) -> str:
    global _token_cache
    if _token_cache:
        token, expiry = _token_cache
        if time.time() < expiry - 60:
            return token

    token_data = _load_token_from_db(db_path)
    if not token_data:
        print(
            f"Error: no Google Contacts OAuth token found in DB (key: {TOKEN_DB_KEY}).",
            file=sys.stderr,
        )
        sys.exit(1)

    access_token, expires_in = _refresh_access_token(token_data)
    _token_cache = (access_token, time.time() + expires_in)
    return access_token
