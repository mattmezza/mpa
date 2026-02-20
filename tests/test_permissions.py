"""Tests for the PermissionEngine."""

from __future__ import annotations

import asyncio

import pytest

from core.permissions import PermissionEngine, PermissionLevel


def test_permission_specificity_prefers_longer_pattern() -> None:
    engine = PermissionEngine()

    allow_delete = engine.check(
        "run_command",
        {"command": 'sqlite3 /app/data/memory.db "DELETE FROM long_term"'},
    )
    deny_drop = engine.check(
        "run_command",
        {"command": 'sqlite3 /app/data/memory.db "DROP TABLE long_term"'},
    )

    assert allow_delete == PermissionLevel.ALWAYS
    assert deny_drop == PermissionLevel.NEVER


def test_unknown_action_defaults_to_ask() -> None:
    engine = PermissionEngine()
    assert engine.check("send_fax", {}) == PermissionLevel.ASK


@pytest.mark.asyncio
async def test_approval_request_lifecycle() -> None:
    engine = PermissionEngine()
    request_id, future = engine.create_approval_request()

    assert request_id
    assert isinstance(future, asyncio.Future)

    resolved = engine.resolve_approval(request_id, True)
    assert resolved is True
    assert await future == "approved"


@pytest.mark.asyncio
async def test_approval_denied() -> None:
    engine = PermissionEngine()
    request_id, future = engine.create_approval_request()

    resolved = engine.resolve_approval(request_id, False)
    assert resolved is True
    assert await future == "denied"


@pytest.mark.asyncio
async def test_approval_skipped() -> None:
    engine = PermissionEngine()
    request_id, future = engine.create_approval_request()

    resolved = engine.resolve_approval(request_id, False, skipped=True)
    assert resolved is True
    assert await future == "skipped"


def test_format_approval_message_run_command_includes_purpose() -> None:
    engine = PermissionEngine()
    text = engine.format_approval_message(
        "run_command",
        {"command": "jq --version", "purpose": "check jq install"},
    )
    assert "jq --version" in text
    assert "check jq install" in text
