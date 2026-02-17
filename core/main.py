"""Entrypoint — boots the agent and starts the Telegram channel."""

from __future__ import annotations

import logging

from channels.telegram import TelegramChannel
from core.agent import AgentCore
from core.config import load_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)
log = logging.getLogger(__name__)


def main() -> None:
    config = load_config()
    agent = AgentCore(config)

    if config.channels.telegram.enabled:
        tg = TelegramChannel(config.channels.telegram, agent)
        agent.channels["telegram"] = tg
        log.info("Starting Telegram bot…")
        tg.app.run_polling()
    else:
        log.error("No channels enabled. Enable Telegram in config.yml and try again.")


if __name__ == "__main__":
    main()
