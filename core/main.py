"""Entrypoint — boots the agent and runs Telegram + admin API concurrently.

Supports two boot modes:
  1. **Setup mode** — config store is empty or setup not complete.
     Only the admin API runs (serves the setup wizard).  No agent, no
     Telegram, no scheduler.
  2. **Normal mode** — setup complete.  Full agent with all channels,
     scheduler, and admin API.
"""

from __future__ import annotations

import asyncio
import logging

import uvicorn

from api.admin import AgentState, create_admin_app, install_log_buffer
from core.config_store import ConfigStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)
log = logging.getLogger(__name__)

# Install the in-memory log buffer handler before anything else
install_log_buffer()


async def _start_agent(config_store: ConfigStore):
    """Build and start the full agent (channels, scheduler, voice)."""
    from channels.telegram import TelegramChannel
    from core.agent import AgentCore
    from voice.pipeline import VoicePipeline

    config = await config_store.export_to_config()

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

    # -- Telegram --
    if config.channels.telegram.enabled and config.channels.telegram.bot_token:
        tg = TelegramChannel(config.channels.telegram, agent, voice=voice)
        agent.channels["telegram"] = tg
        log.info("Starting Telegram bot…")

        await tg.app.initialize()
        await tg.app.start()
        await tg.app.updater.start_polling()

    # -- Scheduler --
    if config.scheduler.jobs:
        agent.scheduler.load_jobs(config.scheduler)

    agent.scheduler.start()
    log.info("Scheduler started with %d jobs", len(agent.scheduler.scheduler.get_jobs()))

    return agent


async def _stop_agent(agent) -> None:
    """Gracefully shut down the agent."""
    agent.scheduler.shutdown()

    if "telegram" in agent.channels:
        tg = agent.channels["telegram"]
        await tg.app.updater.stop()
        await tg.app.stop()
        await tg.app.shutdown()


async def main() -> None:
    config_store = ConfigStore()

    # Seed from YAML/.env on first boot (or if config.db is empty)
    await config_store.seed_if_empty()

    setup_complete = await config_store.is_setup_complete()

    agent_state = AgentState()
    if setup_complete:
        log.info("Setup complete — starting agent")
        try:
            agent_state.agent = await _start_agent(config_store)
        except Exception:
            log.exception("Failed to start agent — falling back to setup mode")
    else:
        log.info("Setup not complete — running in setup-only mode (admin API + wizard)")

    # -- Admin API (always runs) --
    admin_app = create_admin_app(agent_state, config_store)

    # Add lifecycle endpoints that can start/stop the agent
    _attach_lifecycle_routes(admin_app, config_store, agent_state)

    port = 8000
    if setup_complete:
        port_val = await config_store.get("admin.port")
        if port_val:
            try:
                port = int(port_val)
            except ValueError:
                pass

    uvi_config = uvicorn.Config(
        admin_app,
        host="0.0.0.0",
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(uvi_config)
    log.info("Starting admin API on port %s…", port)

    # Graceful shutdown
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

    # Run server until shutdown signal
    server_task = asyncio.create_task(server.serve())

    await stop.wait()

    log.info("Shutting down…")

    if agent_state.agent:
        await _stop_agent(agent_state.agent)

    server_task.cancel()
    try:
        await server_task
    except asyncio.CancelledError:
        pass


def _attach_lifecycle_routes(app, config_store: ConfigStore, agent_state: AgentState) -> None:
    """Add /agent/start and /agent/stop endpoints for runtime lifecycle control.

    These share the same ``AgentState`` object used by ``create_admin_app``
    so all endpoints see agent changes immediately.
    """

    @app.post("/agent/start")
    async def start_agent() -> dict:
        if agent_state.agent is not None:
            return {"status": "already_running"}
        try:
            agent_state.agent = await _start_agent(config_store)
            log.info("Agent started via API")
            return {
                "status": "started",
                "channels": list(agent_state.agent.channels.keys()),
            }
        except Exception as exc:
            log.exception("Failed to start agent via API")
            return {"status": "error", "error": str(exc)}

    @app.post("/agent/stop")
    async def stop_agent() -> dict:
        if agent_state.agent is None:
            return {"status": "not_running"}
        try:
            await _stop_agent(agent_state.agent)
            agent_state.agent = None
            log.info("Agent stopped via API")
            return {"status": "stopped"}
        except Exception as exc:
            log.exception("Failed to stop agent via API")
            return {"status": "error", "error": str(exc)}

    @app.post("/agent/restart")
    async def restart_agent() -> dict:
        # Stop
        if agent_state.agent is not None:
            try:
                await _stop_agent(agent_state.agent)
            except Exception:
                log.exception("Error during agent stop (restart)")
            agent_state.agent = None

        # Start
        try:
            agent_state.agent = await _start_agent(config_store)
            log.info("Agent restarted via API")
            return {
                "status": "restarted",
                "channels": list(agent_state.agent.channels.keys()),
            }
        except Exception as exc:
            log.exception("Failed to restart agent via API")
            return {"status": "error", "error": str(exc)}


if __name__ == "__main__":
    asyncio.run(main())
