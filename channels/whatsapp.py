"""WhatsApp channel — bridges a WhatsApp sidecar to the AgentCore."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from core.agent import AgentCore
    from core.config import WhatsAppConfig

log = logging.getLogger(__name__)


_APPROVE_ACTIONS = {"approve", "approved", "yes"}
_DENY_ACTIONS = {"deny", "denied", "no"}
_ALWAYS_ACTIONS = {"always", "allow"}


def _normalize_number(value: str) -> str:
    if not value:
        return ""
    core = value.strip()
    if "@" in core:
        core = core.split("@", 1)[0]
    return "".join(ch for ch in core if ch.isdigit())


class WhatsAppChannel:
    def __init__(self, config: WhatsAppConfig, agent: AgentCore):
        self.config = config
        self.agent = agent
        self.allowed_numbers = {_normalize_number(n) for n in (config.allowed_numbers or []) if n}

    async def send(self, to: str, text: str) -> None:
        payload = {"to": to, "text": text}
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "http://localhost:8000/channels/whatsapp/send",
                json=payload,
            )
            resp.raise_for_status()

    async def send_approval_request(self, user_id: str, request_id: str, description: str) -> None:
        message = (
            f"Permission request:\n\n{description}\n\n"
            f"Reply with:\n"
            f"- approve {request_id}\n"
            f"- deny {request_id}\n"
            f"- always {request_id}"
        )
        await self.send(user_id, message)

    async def handle_webhook(self, payload: dict) -> dict:
        sender = str(payload.get("from", "")).strip()
        text = str(payload.get("body", "")).strip()
        if not sender or not text:
            return {"ok": False, "error": "Missing sender or message body"}

        if not self._is_allowed(sender):
            log.warning("Ignoring WhatsApp message from unauthorized sender %s", sender)
            return {"ok": False, "error": "Sender not allowed"}

        if await self._maybe_handle_approval(sender, text):
            return {"ok": True, "handled": "approval"}

        response = await self.agent.process(
            message=text,
            channel="whatsapp",
            user_id=sender,
        )
        await self.send(sender, response.text)
        return {"ok": True}

    def _is_allowed(self, sender: str) -> bool:
        if not self.allowed_numbers:
            return True
        normalized = _normalize_number(sender)
        return normalized in self.allowed_numbers

    async def _maybe_handle_approval(self, sender: str, text: str) -> bool:
        tokens = text.strip().lower().split()
        if not tokens:
            return False

        action = tokens[0]
        if action not in _APPROVE_ACTIONS | _DENY_ACTIONS | _ALWAYS_ACTIONS:
            return False

        request_id = tokens[1] if len(tokens) > 1 else ""
        if not request_id:
            match = re.search(r"\b[a-f0-9]{12}\b", text.lower())
            request_id = match.group(0) if match else ""
        if not request_id:
            await self.send(sender, "Missing approval ID. Reply with: approve <id>.")
            return True

        always_allow = action in _ALWAYS_ACTIONS
        approved = action in _APPROVE_ACTIONS or always_allow
        resolved = self.agent.permissions.resolve_approval(
            request_id, approved, always_allow=always_allow
        )
        if resolved:
            label = "Approved" if approved else "Denied"
            if always_allow:
                label = "Always allowed"
            await self.send(sender, f"{label}.")
        else:
            await self.send(sender, "No pending approval found for that ID.")
        return True
