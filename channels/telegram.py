"""Telegram channel — wires python-telegram-bot to the AgentCore."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import Application, MessageHandler, filters

if TYPE_CHECKING:
    from core.agent import AgentCore
    from core.config import TelegramConfig

log = logging.getLogger(__name__)


class TelegramChannel:
    def __init__(self, config: TelegramConfig, agent: AgentCore):
        self.config = config
        self.agent = agent
        self.app = Application.builder().token(config.bot_token).build()
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text))

    async def _on_text(self, update: Update, context) -> None:
        user_id = update.effective_user.id
        if self.config.allowed_user_ids and user_id not in self.config.allowed_user_ids:
            log.warning("Ignoring message from unauthorized user %s", user_id)
            return

        response = await self.agent.process(
            message=update.message.text,
            channel="telegram",
            user_id=str(user_id),
        )
        await update.message.reply_text(response.text)

    async def send(self, chat_id: int | str, text: str) -> None:
        """Send a message to a specific chat (used by scheduler, etc.)."""
        await self.app.bot.send_message(chat_id=chat_id, text=text)
