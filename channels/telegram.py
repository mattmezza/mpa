"""Telegram channel — wires python-telegram-bot to the AgentCore."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, MessageHandler, filters

if TYPE_CHECKING:
    from core.agent import AgentCore
    from core.config import TelegramConfig
    from voice.pipeline import VoicePipeline

log = logging.getLogger(__name__)

# Callback data prefix for approval buttons
_APPROVE_PREFIX = "perm_approve:"
_DENY_PREFIX = "perm_deny:"


class TelegramChannel:
    def __init__(
        self, config: TelegramConfig, agent: AgentCore, voice: VoicePipeline | None = None
    ):
        self.config = config
        self.agent = agent
        self.voice = voice
        self.app = Application.builder().token(config.bot_token).build()
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text))
        self.app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, self._on_voice))
        self.app.add_handler(CallbackQueryHandler(self._on_approval_callback))

    # -- Incoming handlers ---------------------------------------------------

    async def _on_text(self, update: Update, context) -> None:
        user_id = update.effective_user.id
        if not self._is_allowed(user_id):
            return

        response = await self.agent.process(
            message=update.message.text,
            channel="telegram",
            user_id=str(user_id),
        )
        await self._send_response(update, response)

    async def _on_voice(self, update: Update, context) -> None:
        """Handle incoming voice messages: download, transcribe, process, reply."""
        user_id = update.effective_user.id
        if not self._is_allowed(user_id):
            return

        if not self.voice:
            await update.message.reply_text(
                "Voice messages are not supported (voice pipeline not configured)."
            )
            return

        # Download the voice/audio file
        voice_msg = update.message.voice or update.message.audio
        if not voice_msg:
            return

        file = await voice_msg.get_file()
        audio_bytes = await file.download_as_bytearray()

        # Transcribe via Whisper
        log.info("Transcribing voice message from user %s (%d bytes)", user_id, len(audio_bytes))
        transcript = await self.voice.transcribe(bytes(audio_bytes))

        if not transcript.strip():
            await update.message.reply_text("(could not transcribe voice message)")
            return

        log.info("Transcript: %s", transcript[:200])

        # Pass to agent with [voice] prefix so the LLM knows the input medium
        response = await self.agent.process(
            message=f"[voice] {transcript}",
            channel="telegram",
            user_id=str(user_id),
        )
        await self._send_response(update, response)

    async def _on_approval_callback(self, update: Update, context) -> None:
        """Handle inline keyboard button presses for permission approvals."""
        query = update.callback_query
        await query.answer()  # Acknowledge the button press

        user_id = update.effective_user.id
        if not self._is_allowed(user_id):
            return

        data = query.data or ""

        if data.startswith(_APPROVE_PREFIX):
            request_id = data[len(_APPROVE_PREFIX) :]
            resolved = self.agent.permissions.resolve_approval(request_id, True)
            if resolved:
                await query.edit_message_text(query.message.text + "\n\n--- Approved")
            else:
                await query.edit_message_text("(approval request expired or already handled)")

        elif data.startswith(_DENY_PREFIX):
            request_id = data[len(_DENY_PREFIX) :]
            resolved = self.agent.permissions.resolve_approval(request_id, False)
            if resolved:
                await query.edit_message_text(query.message.text + "\n\n--- Denied")
            else:
                await query.edit_message_text("(approval request expired or already handled)")

    # -- Outgoing ------------------------------------------------------------

    async def send(self, chat_id: int | str, text: str) -> None:
        """Send a message to a specific chat (used by scheduler, send_message tool, etc.)."""
        await self.app.bot.send_message(chat_id=chat_id, text=text)

    async def send_approval_request(self, user_id: str, request_id: str, description: str) -> None:
        """Send a permission approval prompt with Approve/Deny inline buttons."""
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Approve", callback_data=f"{_APPROVE_PREFIX}{request_id}"),
                    InlineKeyboardButton("Deny", callback_data=f"{_DENY_PREFIX}{request_id}"),
                ]
            ]
        )
        await self.app.bot.send_message(
            chat_id=int(user_id),
            text=f"Permission request:\n\n{description}",
            reply_markup=keyboard,
        )

    # -- Helpers -------------------------------------------------------------

    def _is_allowed(self, user_id: int) -> bool:
        if self.config.allowed_user_ids and user_id not in self.config.allowed_user_ids:
            log.warning("Ignoring message from unauthorized user %s", user_id)
            return False
        return True

    async def _send_response(self, update: Update, response) -> None:
        """Send an AgentResponse back — voice if present, otherwise text."""
        if response.voice:
            await update.message.reply_voice(voice=response.voice)
        else:
            await update.message.reply_text(response.text)
