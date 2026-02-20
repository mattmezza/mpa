from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from channels.whatsapp import WhatsAppChannel, _normalize_number
from core.config import WhatsAppConfig


class FakePermissions:
    def __init__(self, resolved: bool = True):
        self.calls: list[tuple[str, bool, bool, bool]] = []
        self.resolved = resolved

    def resolve_approval(
        self,
        request_id: str,
        approved: bool,
        always_allow: bool = False,
        *,
        skipped: bool = False,
    ) -> bool:
        self.calls.append((request_id, approved, always_allow, skipped))
        return self.resolved


class FakeAgent:
    def __init__(self, permissions: FakePermissions):
        self.permissions = permissions


def _fake_wacli() -> Any:
    wacli = AsyncMock()
    wacli.send_text.return_value = {"success": True}
    return wacli


def test_normalize_number() -> None:
    assert _normalize_number("+39 333 1234567@c.us") == "393331234567"
    assert _normalize_number(" +1 (415) 555-1234 ") == "14155551234"


def test_allowed_numbers_match() -> None:
    config = WhatsAppConfig(allowed_numbers=["+14155551234"])
    channel = WhatsAppChannel(config, cast(Any, FakeAgent(FakePermissions())), wacli=_fake_wacli())
    assert channel._is_allowed("+1 415-555-1234") is True
    assert channel._is_allowed("14155551234@c.us") is True
    assert channel._is_allowed("+442071838750") is False


@pytest.mark.asyncio
async def test_approval_commands_send_responses() -> None:
    permissions = FakePermissions(resolved=True)
    channel = WhatsAppChannel(
        WhatsAppConfig(),
        cast(Any, FakeAgent(permissions)),
        wacli=_fake_wacli(),
    )
    sent: list[tuple[str, str]] = []

    async def fake_send(to: str, text: str) -> None:
        sent.append((to, text))

    channel.send = fake_send

    handled = await channel._maybe_handle_approval("sender", "approve abcdef123456")
    assert handled is True
    assert permissions.calls == [("abcdef123456", True, False, False)]
    assert sent[-1][1] == "Approved."

    handled = await channel._maybe_handle_approval("sender", "always abcdef123456")
    assert handled is True
    assert permissions.calls[-1] == ("abcdef123456", True, True, False)
    assert sent[-1][1] == "Always allowed."


@pytest.mark.asyncio
async def test_approval_missing_id() -> None:
    permissions = FakePermissions(resolved=True)
    channel = WhatsAppChannel(
        WhatsAppConfig(),
        cast(Any, FakeAgent(permissions)),
        wacli=_fake_wacli(),
    )
    sent: list[tuple[str, str]] = []

    async def fake_send(to: str, text: str) -> None:
        sent.append((to, text))

    channel.send = fake_send

    handled = await channel._maybe_handle_approval("sender", "approve")
    assert handled is True
    assert permissions.calls == []
    assert sent[-1][1] == "Missing approval ID. Reply with: approve <id>."


@pytest.mark.asyncio
async def test_skip_approval_command() -> None:
    permissions = FakePermissions(resolved=True)
    channel = WhatsAppChannel(
        WhatsAppConfig(),
        cast(Any, FakeAgent(permissions)),
        wacli=_fake_wacli(),
    )
    sent: list[tuple[str, str]] = []

    async def fake_send(to: str, text: str) -> None:
        sent.append((to, text))

    channel.send = fake_send

    handled = await channel._maybe_handle_approval("sender", "skip abcdef123456")
    assert handled is True
    assert permissions.calls == [("abcdef123456", False, False, True)]
    assert sent[-1][1] == "Skipped."
