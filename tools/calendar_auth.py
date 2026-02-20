"""Shared OAuth 2.0 helper for CalDAV calendar tools.

Loads a Google OAuth token file, refreshes the access token if needed,
and provides a `caldav.DAVClient` that uses Bearer auth.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import caldav
import requests

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

# Cache: (access_token, expiry_timestamp)
_token_cache: dict[str, tuple[str, float]] = {}


def _refresh_access_token(token_data: dict) -> tuple[str, int]:
    """Use the refresh token to get a fresh access token."""
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


def get_access_token(token_file: str) -> str:
    """Return a valid access token, refreshing if expired."""
    cached = _token_cache.get(token_file)
    if cached:
        token, expiry = cached
        if time.time() < expiry - 60:  # 60s safety margin
            return token

    path = Path(token_file)
    if not path.exists():
        print(f"Error: token file not found: {token_file}", file=sys.stderr)
        print("Run tools/google_oauth.py to authorize.", file=sys.stderr)
        sys.exit(1)

    token_data = json.loads(path.read_text())
    access_token, expires_in = _refresh_access_token(token_data)
    _token_cache[token_file] = (access_token, time.time() + expires_in)
    return access_token


def connect_google(provider: dict) -> caldav.Calendar:
    """Connect to Google CalDAV using OAuth 2.0 Bearer token."""
    token_file = provider.get("token_file", "data/google_calendar_token.json")
    access_token = get_access_token(token_file)

    client = caldav.DAVClient(
        url=provider["url"],
        headers={"Authorization": f"Bearer {access_token}"},
    )
    principal = client.principal()
    calendars = principal.calendars()
    if not calendars:
        print("Error: no calendars found for this account", file=sys.stderr)
        sys.exit(1)
    return calendars[0]


def connect_basic(provider: dict) -> caldav.Calendar:
    """Connect to a CalDAV server using Basic Auth (for non-Google providers)."""
    client = caldav.DAVClient(
        url=provider["url"],
        username=provider.get("username", ""),
        password=provider.get("password", ""),
    )
    principal = client.principal()
    calendars = principal.calendars()
    if not calendars:
        print("Error: no calendars found for this account", file=sys.stderr)
        sys.exit(1)
    return calendars[0]


def connect(provider: dict) -> caldav.Calendar:
    """Connect to a CalDAV server, auto-detecting auth method.

    Uses OAuth 2.0 if `auth_type` is "oauth" or if the URL contains
    "google", otherwise falls back to Basic Auth.
    """
    auth_type = provider.get("auth_type", "")
    url = provider.get("url", "")

    if auth_type == "oauth" or (not auth_type and "google" in url.lower()):
        return connect_google(provider)
    return connect_basic(provider)
