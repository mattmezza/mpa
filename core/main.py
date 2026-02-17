"""Entrypoint — boots the agent and runs Telegram + admin API concurrently."""

from __future__ import annotations

import asyncio
import logging

import uvicorn

from api.admin import create_admin_app
from channels.telegram import TelegramChannel
from core.agent import AgentCore
from core.config import load_config
from voice.pipeline import VoicePipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)
log = logging.getLogger(__name__)


async def main() -> None:
    config = load_config()
    agent = AgentCore(config)

    # -- Voice pipeline --
    voice: VoicePipeline | None = None
    if config.voice.tts_enabled:
        log.info(
            "Initializing voice pipeline (model=%s, voice=%s)…",
            config.voice.stt_model,
            config.voice.tts_voice,
        )
        voice = VoicePipeline(
            stt_model=config.voice.stt_model,
            tts_voice=config.voice.tts_voice,
            tts_enabled=config.voice.tts_enabled,
        )
        agent.voice = voice

    tasks: list[asyncio.Task] = []

    # -- Telegram --
    if config.channels.telegram.enabled:
        tg = TelegramChannel(config.channels.telegram, agent, voice=voice)
        agent.channels["telegram"] = tg
        log.info("Starting Telegram bot…")

        await tg.app.initialize()
        await tg.app.start()
        await tg.app.updater.start_polling()
        # Telegram is now running in the background; no task needed —
        # it hooks into the event loop via its own internal tasks.

    # -- Scheduler --
    if config.scheduler.jobs:
        agent.scheduler.load_jobs(config.scheduler)
        agent.scheduler.start()
        log.info("Scheduler started with %d jobs", len(config.scheduler.jobs))

    # -- Admin API --
    if config.admin.enabled:
        admin_app = create_admin_app(agent)
        uvi_config = uvicorn.Config(
            admin_app,
            host="0.0.0.0",
            port=config.admin.port,
            log_level="info",
        )
        server = uvicorn.Server(uvi_config)
        tasks.append(asyncio.create_task(server.serve()))
        log.info("Starting admin API on port %s…", config.admin.port)

    if not tasks and not agent.channels:
        log.error("Nothing to run. Enable Telegram or the admin API in config.yml.")
        return

    # Block until all long-running tasks finish (or are cancelled via signal).
    stop = asyncio.Event()

    def _signal_handler() -> None:
        log.info("Shutdown signal received")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig_name in ("SIGINT", "SIGTERM"):
        try:
            loop.add_signal_handler(getattr(__import__("signal"), sig_name), _signal_handler)
        except NotImplementedError:
            pass  # Windows

    # Wait for shutdown signal
    await stop.wait()

    # Graceful cleanup
    log.info("Shutting down…")

    # Stop scheduler first (prevents new jobs from firing during shutdown)
    agent.scheduler.shutdown()

    if "telegram" in agent.channels:
        tg = agent.channels["telegram"]
        await tg.app.updater.stop()
        await tg.app.stop()
        await tg.app.shutdown()

    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
