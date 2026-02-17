"""Admin API — FastAPI app for health checks, config management, permissions,
memory inspection, log streaming, and agent lifecycle control.

All endpoints (except /health and /setup/*) require Bearer token auth
matching the admin.api_key config value.
"""

from __future__ import annotations

import collections
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

if TYPE_CHECKING:
    from core.agent import AgentCore
    from core.config_store import ConfigStore

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared mutable agent state — used by both admin endpoints and lifecycle
# routes in main.py so everyone sees the same agent reference.
# ---------------------------------------------------------------------------


class AgentState:
    """Mutable container for the currently running agent.

    Both ``create_admin_app`` closures and lifecycle routes in
    ``core.main`` reference this object so that starting/stopping the
    agent is immediately visible to all endpoints.
    """

    def __init__(self, agent: AgentCore | None = None):
        self.agent: AgentCore | None = agent


# ---------------------------------------------------------------------------
# In-memory ring buffer for recent log lines (read by /logs endpoint)
# ---------------------------------------------------------------------------

_LOG_BUFFER: collections.deque[str] = collections.deque(maxlen=500)


class _BufferHandler(logging.Handler):
    """Logging handler that appends formatted records to an in-memory deque."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            _LOG_BUFFER.append(self.format(record))
        except Exception:
            pass


def install_log_buffer() -> None:
    """Attach the ring-buffer handler to the root logger."""
    handler = _BufferHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s — %(message)s"))
    logging.getLogger().addHandler(handler)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class PermissionRuleIn(BaseModel):
    pattern: str
    level: str  # ALWAYS | ASK | NEVER


class ConfigPatchIn(BaseModel):
    values: dict[str, str]


class SetupStepIn(BaseModel):
    step: str
    values: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

_bearer = HTTPBearer(auto_error=False)


def _make_auth_dependency(config_store: ConfigStore):
    """Return a FastAPI dependency that validates the admin API key.

    During setup (before setup is complete), auth is bypassed so the
    wizard can be used without a pre-existing API key.
    """

    async def _check_auth(
        credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    ) -> None:
        setup_complete = await config_store.is_setup_complete()
        if not setup_complete:
            # During first-time setup, allow unauthenticated access
            return

        api_key = await config_store.get("admin.api_key")
        if not api_key:
            # No API key configured — skip auth (but warn)
            return

        if not credentials or credentials.credentials != api_key:
            raise HTTPException(status_code=401, detail="Invalid or missing API key")

    return _check_auth


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_admin_app(
    agent_state: AgentState,
    config_store: ConfigStore,
) -> FastAPI:
    app = FastAPI(title="Personal Agent Admin", version="0.1.0")

    auth = _make_auth_dependency(config_store)

    # ── Health ──────────────────────────────────────────────────────────

    @app.get("/health")
    async def health() -> dict:
        agent = agent_state.agent
        setup_complete = await config_store.is_setup_complete()
        return {
            "status": "ok",
            "setup_complete": setup_complete,
            "agent_running": agent is not None and bool(agent.channels),
        }

    # ── Config ──────────────────────────────────────────────────────────

    @app.get("/config", dependencies=[Depends(auth)])
    async def get_config() -> dict:
        """Return all config values with secrets redacted."""
        return await config_store.get_all_redacted()

    @app.get("/config/{section}", dependencies=[Depends(auth)])
    async def get_config_section(section: str) -> dict:
        """Return a config section with secrets redacted."""
        return await config_store.get_section_redacted(section)

    @app.patch("/config", dependencies=[Depends(auth)])
    async def patch_config(body: ConfigPatchIn) -> dict:
        """Update one or more config values."""
        await config_store.set_many(body.values)
        return {"updated": list(body.values.keys())}

    @app.delete("/config/{key:path}", dependencies=[Depends(auth)])
    async def delete_config(key: str) -> dict:
        """Delete a config value by key."""
        deleted = await config_store.delete(key)
        if not deleted:
            raise HTTPException(404, f"Config key not found: {key}")
        return {"deleted": key}

    # ── Permissions ─────────────────────────────────────────────────────

    @app.get("/permissions", dependencies=[Depends(auth)])
    async def list_permissions() -> dict:
        agent = agent_state.agent
        if not agent:
            raise HTTPException(503, "Agent not running")
        return {"rules": agent.permissions.rules}

    @app.put("/permissions", dependencies=[Depends(auth)])
    async def upsert_permission(body: PermissionRuleIn) -> dict:
        agent = agent_state.agent
        if not agent:
            raise HTTPException(503, "Agent not running")
        agent.permissions.add_rule(body.pattern, body.level)
        return {"pattern": body.pattern, "level": body.level}

    @app.delete("/permissions/{pattern:path}", dependencies=[Depends(auth)])
    async def delete_permission(pattern: str) -> dict:
        agent = agent_state.agent
        if not agent:
            raise HTTPException(503, "Agent not running")
        if pattern in agent.permissions.rules:
            del agent.permissions.rules[pattern]
            return {"deleted": pattern}
        raise HTTPException(404, f"Rule not found: {pattern}")

    # ── Memory ──────────────────────────────────────────────────────────

    @app.get("/memory/long-term", dependencies=[Depends(auth)])
    async def list_long_term(
        subject: str | None = None,
        category: str | None = None,
    ) -> dict:
        agent = agent_state.agent
        if not agent:
            raise HTTPException(503, "Agent not running")
        import aiosqlite

        await agent.memory._ensure_schema()
        cols = "id, category, subject, content, source, confidence, created_at, updated_at"
        query = f"SELECT {cols} FROM long_term"
        conditions = []
        params: list[str] = []
        if subject:
            conditions.append("subject = ?")
            params.append(subject)
        if category:
            conditions.append("category = ?")
            params.append(category)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY updated_at DESC"

        async with aiosqlite.connect(agent.memory.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, params)
            rows = [dict(row) for row in await cursor.fetchall()]
        return {"count": len(rows), "memories": rows}

    @app.get("/memory/short-term", dependencies=[Depends(auth)])
    async def list_short_term() -> dict:
        agent = agent_state.agent
        if not agent:
            raise HTTPException(503, "Agent not running")
        import aiosqlite

        await agent.memory._ensure_schema()
        async with aiosqlite.connect(agent.memory.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, content, context, expires_at, created_at "
                "FROM short_term WHERE expires_at > datetime('now') "
                "ORDER BY created_at DESC"
            )
            rows = [dict(row) for row in await cursor.fetchall()]
        return {"count": len(rows), "memories": rows}

    @app.delete("/memory/{tier}/{memory_id}", dependencies=[Depends(auth)])
    async def delete_memory(tier: str, memory_id: int) -> dict:
        agent = agent_state.agent
        if not agent:
            raise HTTPException(503, "Agent not running")
        if tier not in ("long-term", "short-term"):
            raise HTTPException(400, "Tier must be 'long-term' or 'short-term'")

        import aiosqlite

        table = "long_term" if tier == "long-term" else "short_term"
        await agent.memory._ensure_schema()
        async with aiosqlite.connect(agent.memory.db_path) as db:
            cursor = await db.execute(f"DELETE FROM {table} WHERE id = ?", (memory_id,))
            await db.commit()
            if cursor.rowcount == 0:
                raise HTTPException(404, f"Memory {memory_id} not found in {tier}")
        return {"deleted": memory_id, "tier": tier}

    @app.post("/memory/consolidate", dependencies=[Depends(auth)])
    async def trigger_consolidation() -> dict:
        agent = agent_state.agent
        if not agent:
            raise HTTPException(503, "Agent not running")
        result = await agent.memory.consolidate_and_cleanup(
            llm=agent.llm,
            model=agent.config.memory.consolidation_model,
        )
        return result

    # ── Logs ────────────────────────────────────────────────────────────

    @app.get("/logs", dependencies=[Depends(auth)])
    async def get_logs(lines: int = 100) -> dict:
        """Return recent log lines from the in-memory ring buffer."""
        recent = list(_LOG_BUFFER)
        if lines < len(recent):
            recent = recent[-lines:]
        return {"count": len(recent), "lines": recent}

    # ── Agent lifecycle ─────────────────────────────────────────────────

    @app.get("/agent/status", dependencies=[Depends(auth)])
    async def agent_status() -> dict:
        agent = agent_state.agent
        if not agent:
            return {"running": False, "channels": [], "scheduler_jobs": 0}
        return {
            "running": True,
            "channels": list(agent.channels.keys()),
            "scheduler_jobs": len(agent.scheduler.scheduler.get_jobs()),
        }

    # ── Setup wizard ────────────────────────────────────────────────────

    @app.get("/setup/status")
    async def setup_status() -> dict:
        from core.config_store import SETUP_STEPS

        complete = await config_store.is_setup_complete()
        step = await config_store.get_setup_step()
        return {
            "complete": complete,
            "current_step": step,
            "steps": SETUP_STEPS,
        }

    @app.post("/setup/step")
    async def setup_save_step(body: SetupStepIn) -> dict:
        """Save config values for a setup step and advance to the next."""
        from core.config_store import SETUP_STEPS

        if body.values:
            await config_store.set_many(body.values)
            log.info("Setup step %r: saved %d values", body.step, len(body.values))

        # Advance to the requested step
        if body.step not in SETUP_STEPS:
            raise HTTPException(400, f"Unknown step: {body.step}")
        await config_store.set_setup_step(body.step)
        return {"step": body.step, "saved": list(body.values.keys())}

    @app.post("/setup/test-connection")
    async def test_connection(request: Request) -> dict:
        """Test a service connection (Anthropic, Telegram, email, etc.)."""
        payload = await request.json()
        service = payload.get("service", "")

        if service == "anthropic":
            return await _test_anthropic(payload.get("api_key", ""))
        if service == "telegram":
            return await _test_telegram(payload.get("bot_token", ""))
        if service == "tavily":
            return await _test_tavily(payload.get("api_key", ""))
        return {"ok": False, "error": f"Unknown service: {service}"}

    @app.get("/setup", response_class=HTMLResponse)
    async def setup_page() -> str:
        """Serve the setup wizard / admin UI as a single HTML page."""
        ui_path = Path(__file__).parent / "ui.html"
        if ui_path.exists():
            return ui_path.read_text()
        return "<html><body><h1>UI not found</h1><p>api/ui.html is missing.</p></body></html>"

    return app


# ---------------------------------------------------------------------------
# Connection test helpers
# ---------------------------------------------------------------------------


async def _test_anthropic(api_key: str) -> dict:
    if not api_key:
        return {"ok": False, "error": "API key is empty"}
    try:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=16,
            messages=[{"role": "user", "content": "Say 'ok'"}],
        )
        text = ""
        for block in response.content:
            if hasattr(block, "text"):
                text = block.text
                break
        return {"ok": True, "response": text}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def _test_telegram(bot_token: str) -> dict:
    if not bot_token:
        return {"ok": False, "error": "Bot token is empty"}
    try:
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.get(f"https://api.telegram.org/bot{bot_token}/getMe")
            data = resp.json()
            if data.get("ok"):
                bot_info = data["result"]
                return {
                    "ok": True,
                    "bot_name": bot_info.get("first_name", ""),
                    "bot_username": bot_info.get("username", ""),
                }
            return {"ok": False, "error": data.get("description", "Unknown error")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def _test_tavily(api_key: str) -> dict:
    if not api_key:
        return {"ok": False, "error": "API key is empty"}
    try:
        import asyncio

        from tavily import TavilyClient

        client = TavilyClient(api_key=api_key)
        result = await asyncio.to_thread(client.search, query="test", max_results=1)
        return {"ok": True, "results": len(result.get("results", []))}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
