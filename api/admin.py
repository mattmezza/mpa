"""Admin API — FastAPI app for health checks, config management, permissions,
memory inspection, log streaming, and agent lifecycle control.

Uses Jinja2 templates with HTMX for the UI. All endpoints (except /health,
/setup/*, /login, and /static/*) require Bearer token auth matching the
admin.api_key config value.
"""

from __future__ import annotations

import collections
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader
from pydantic import BaseModel

if TYPE_CHECKING:
    from core.agent import AgentCore
    from core.config_store import ConfigStore

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Jinja2 template environment
# ---------------------------------------------------------------------------

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=True,
    auto_reload=True,
)
_jinja_env.globals["step_ctx"] = {}  # default empty dict for wizard templates


def _render(template_name: str, **ctx: object) -> HTMLResponse:
    """Render a Jinja2 template and return an HTMLResponse."""
    tmpl = _jinja_env.get_template(template_name)
    return HTMLResponse(tmpl.render(**ctx))


def _render_partial(template_name: str, **ctx: object) -> HTMLResponse:
    """Render a partial template (no base layout) for HTMX swaps."""
    tmpl = _jinja_env.get_template(template_name)
    return HTMLResponse(tmpl.render(**ctx))


async def _wizard_step_context(step: str, config_store: ConfigStore) -> dict[str, str]:
    """Fetch previously-saved config values relevant to a wizard step.

    Returns a flat dict of template variable names to values so that
    navigating *back* in the wizard pre-populates form fields.
    """
    ctx: dict[str, str] = {}
    if step == "llm":
        for key, var in (
            ("agent.anthropic_api_key", "api_key"),
            ("agent.model", "model"),
        ):
            val = await config_store.get(key)
            if val:
                ctx[var] = val
    elif step == "identity":
        for key, var in (
            ("agent.name", "agent_name"),
            ("agent.owner_name", "owner_name"),
            ("agent.timezone", "timezone"),
        ):
            val = await config_store.get(key)
            if val:
                ctx[var] = val
    elif step == "telegram":
        for key, var in (
            ("channels.telegram.bot_token", "bot_token"),
            ("channels.telegram.allowed_user_ids", "user_ids"),
        ):
            val = await config_store.get(key)
            if val:
                ctx[var] = val
    elif step == "calendar":
        raw = await config_store.get("calendar.providers")
        if raw:
            try:
                providers = json.loads(raw)
                if providers and isinstance(providers, list):
                    p = providers[0]
                    ctx["cal_name"] = p.get("name", "")
                    ctx["cal_url"] = p.get("url", "")
                    ctx["cal_username"] = p.get("username", "")
                    ctx["cal_password"] = p.get("password", "")
            except json.JSONDecodeError, IndexError:
                pass
    elif step == "search":
        val = await config_store.get("search.api_key")
        if val:
            ctx["tavily_key"] = val
    elif step == "admin":
        val = await config_store.get("admin.api_key")
        if val:
            ctx["admin_key"] = val
    return ctx


def _render_wizard_step(
    step: str,
    steps: list[str],
    ctx: dict[str, str] | None = None,
) -> HTMLResponse:
    """Render a wizard step partial with OOB progress dots update."""
    step_html = _jinja_env.get_template(f"wizard/{step}.html").render(
        step_ctx=ctx or {},
    )
    progress_html = _jinja_env.get_template("wizard/progress.html").render(
        steps=steps, current_step=step
    )
    return HTMLResponse(step_html + progress_html)


# ---------------------------------------------------------------------------
# Shared mutable agent state
# ---------------------------------------------------------------------------


class AgentState:
    """Mutable container for the currently running agent."""

    def __init__(self, agent: AgentCore | None = None):
        self.agent: AgentCore | None = agent


# ---------------------------------------------------------------------------
# In-memory ring buffer for recent log lines
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
    """Return a FastAPI dependency that validates the admin API key."""

    async def _check_auth(
        credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    ) -> None:
        setup_complete = await config_store.is_setup_complete()
        if not setup_complete:
            return

        api_key = await config_store.get("admin.api_key")
        if not api_key:
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
) -> tuple[FastAPI, object]:
    app = FastAPI(title="Personal Agent Admin", version="0.1.0")

    # Mount static files
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    auth = _make_auth_dependency(config_store)

    # Keys that hold large text content
    _LARGE_TEXT_KEYS = {"agent.character", "agent.personalia"}

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

    # ── Page routes (full HTML pages) ──────────────────────────────────

    @app.get("/login", response_class=HTMLResponse)
    async def login_page() -> HTMLResponse:
        """Login page for the admin dashboard."""
        return _render("login.html")

    @app.get("/setup", response_class=HTMLResponse)
    async def setup_page() -> HTMLResponse:
        """Setup wizard page."""
        from core.config_store import SETUP_STEPS

        complete = await config_store.is_setup_complete()
        if complete:
            return RedirectResponse("/admin", status_code=302)

        step = await config_store.get_setup_step()
        step_ctx = await _wizard_step_context(step, config_store)
        return _render("setup.html", steps=SETUP_STEPS, current_step=step, step_ctx=step_ctx)

    @app.get("/admin", response_class=HTMLResponse)
    async def admin_page() -> HTMLResponse:
        """Admin dashboard page."""
        setup_complete = await config_store.is_setup_complete()
        if not setup_complete:
            return RedirectResponse("/setup", status_code=302)
        return _render("dashboard.html")

    @app.get("/", response_class=HTMLResponse)
    async def root_redirect() -> RedirectResponse:
        """Redirect root to setup or admin based on state."""
        setup_complete = await config_store.is_setup_complete()
        if setup_complete:
            return RedirectResponse("/admin", status_code=302)
        return RedirectResponse("/setup", status_code=302)

    # ── HTMX partial routes ────────────────────────────────────────────

    @app.get("/partials/status", dependencies=[Depends(auth)])
    async def partial_status() -> HTMLResponse:
        """Status bar partial for the dashboard."""
        agent = agent_state.agent
        if agent:
            running = True
            channels = list(agent.channels.keys())
            scheduler_jobs = len(agent.scheduler.scheduler.get_jobs())
        else:
            running = False
            channels = []
            scheduler_jobs = 0
        return _render_partial(
            "partials/status.html",
            running=running,
            channels=channels,
            scheduler_jobs=scheduler_jobs,
        )

    @app.get("/partials/config", dependencies=[Depends(auth)])
    async def partial_config() -> HTMLResponse:
        """Config tab partial."""
        data = await config_store.get_all_redacted()
        filtered = {k: v for k, v in data.items() if k not in _LARGE_TEXT_KEYS}
        config_items = sorted(filtered.items())
        return _render_partial("partials/config.html", config_items=config_items)

    @app.get("/partials/identity", dependencies=[Depends(auth)])
    async def partial_identity() -> HTMLResponse:
        """Agent identity tab partial."""
        character = await config_store.get("agent.character") or ""
        personalia = await config_store.get("agent.personalia") or ""
        return _render_partial(
            "partials/identity.html",
            character=character,
            personalia=personalia,
        )

    @app.get("/partials/permissions", dependencies=[Depends(auth)])
    async def partial_permissions() -> HTMLResponse:
        """Permissions tab partial."""
        agent = agent_state.agent
        rules = agent.permissions.rules if agent else {}
        return _render_partial("partials/permissions.html", rules=rules)

    @app.get("/partials/memory", dependencies=[Depends(auth)])
    async def partial_memory() -> HTMLResponse:
        """Memory tab partial."""
        agent = agent_state.agent
        long_term = []
        short_term = []
        if agent:
            import aiosqlite

            await agent.memory._ensure_schema()
            cols = "id, category, subject, content, source, confidence, created_at, updated_at"
            async with aiosqlite.connect(agent.memory.db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(f"SELECT {cols} FROM long_term ORDER BY updated_at DESC")
                long_term = [dict(row) for row in await cursor.fetchall()]

                cursor = await db.execute(
                    "SELECT id, content, context, expires_at, created_at "
                    "FROM short_term WHERE expires_at > datetime('now') "
                    "ORDER BY created_at DESC"
                )
                short_term = [dict(row) for row in await cursor.fetchall()]

        return _render_partial(
            "partials/memory.html",
            long_term=long_term,
            short_term=short_term,
        )

    @app.get("/partials/logs", dependencies=[Depends(auth)])
    async def partial_logs() -> HTMLResponse:
        """Logs tab partial (container with auto-refresh)."""
        return _render_partial("partials/logs.html")

    @app.get("/partials/logs-content", dependencies=[Depends(auth)])
    async def partial_logs_content() -> HTMLResponse:
        """Log lines partial for HTMX swap."""
        lines = list(_LOG_BUFFER)[-200:]
        return _render_partial("partials/logs_content.html", lines=lines)

    # ── Config API ─────────────────────────────────────────────────────

    @app.get("/config", dependencies=[Depends(auth)])
    async def get_config() -> dict:
        data = await config_store.get_all_redacted()
        return {k: v for k, v in data.items() if k not in _LARGE_TEXT_KEYS}

    @app.get("/config/character", dependencies=[Depends(auth)])
    async def get_character() -> dict:
        value = await config_store.get("agent.character") or ""
        return {"content": value}

    @app.post("/config/character", dependencies=[Depends(auth)])
    async def put_character(request: Request) -> dict:
        body = await request.json()
        content = body.get("content", "")
        await config_store.set("agent.character", content)
        return {"updated": "agent.character"}

    @app.get("/config/personalia", dependencies=[Depends(auth)])
    async def get_personalia() -> dict:
        value = await config_store.get("agent.personalia") or ""
        return {"content": value}

    @app.post("/config/personalia", dependencies=[Depends(auth)])
    async def put_personalia(request: Request) -> dict:
        body = await request.json()
        content = body.get("content", "")
        await config_store.set("agent.personalia", content)
        return {"updated": "agent.personalia"}

    @app.get("/config/{section}", dependencies=[Depends(auth)])
    async def get_config_section(section: str) -> dict:
        return await config_store.get_section_redacted(section)

    @app.patch("/config", dependencies=[Depends(auth)])
    async def patch_config(body: ConfigPatchIn) -> dict:
        await config_store.set_many(body.values)
        return {"updated": list(body.values.keys())}

    @app.post("/config/delete", dependencies=[Depends(auth)])
    async def delete_config(request: Request) -> HTMLResponse:
        """Delete a config key. Returns refreshed config partial for HTMX."""
        content_type = request.headers.get("content-type", "")
        if "application/x-www-form-urlencoded" in content_type:
            body = await request.form()
        else:
            body = await request.json()
        key = str(body.get("key", ""))
        if not key:
            raise HTTPException(400, "Missing 'key' in request body")
        deleted = await config_store.delete(key)
        if not deleted:
            raise HTTPException(404, f"Config key not found: {key}")
        # Return refreshed config partial
        data = await config_store.get_all_redacted()
        filtered = {k: v for k, v in data.items() if k not in _LARGE_TEXT_KEYS}
        config_items = sorted(filtered.items())
        return _render_partial("partials/config.html", config_items=config_items)

    # ── Permissions API ────────────────────────────────────────────────

    @app.get("/permissions", dependencies=[Depends(auth)])
    async def list_permissions() -> dict:
        agent = agent_state.agent
        if not agent:
            raise HTTPException(503, "Agent not running")
        return {"rules": agent.permissions.rules}

    @app.post("/permissions", dependencies=[Depends(auth)])
    async def upsert_permission(request: Request) -> HTMLResponse:
        """Add/update a permission rule. Returns refreshed partial for HTMX."""
        agent = agent_state.agent
        if not agent:
            raise HTTPException(503, "Agent not running")
        body = await request.form()
        pattern = body.get("pattern", "")
        level = body.get("level", "ASK")
        if pattern:
            agent.permissions.add_rule(str(pattern), str(level))
        rules = agent.permissions.rules
        return _render_partial("partials/permissions.html", rules=rules)

    @app.post("/permissions/delete", dependencies=[Depends(auth)])
    async def delete_permission(request: Request) -> HTMLResponse:
        """Delete a permission rule. Returns refreshed partial for HTMX."""
        agent = agent_state.agent
        if not agent:
            raise HTTPException(503, "Agent not running")
        content_type = request.headers.get("content-type", "")
        if "application/x-www-form-urlencoded" in content_type:
            body = await request.form()
        else:
            body = await request.json()
        pattern = str(body.get("pattern", ""))
        if pattern and pattern in agent.permissions.rules:
            del agent.permissions.rules[pattern]
        rules = agent.permissions.rules
        return _render_partial("partials/permissions.html", rules=rules)

    # ── Memory API ─────────────────────────────────────────────────────

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

    @app.post("/memory/delete", dependencies=[Depends(auth)])
    async def delete_memory(request: Request) -> HTMLResponse:
        """Delete a memory entry. Returns refreshed memory partial for HTMX."""
        agent = agent_state.agent
        if not agent:
            raise HTTPException(503, "Agent not running")
        content_type = request.headers.get("content-type", "")
        if "application/x-www-form-urlencoded" in content_type:
            body = await request.form()
        else:
            body = await request.json()
        tier = str(body.get("tier", ""))
        memory_id = body.get("memory_id")
        if tier not in ("long-term", "short-term"):
            raise HTTPException(400, "Tier must be 'long-term' or 'short-term'")
        if memory_id is None:
            raise HTTPException(400, "Missing 'memory_id' in request body")
        # Coerce to int (form-encoded values arrive as strings)
        try:
            memory_id = int(memory_id)
        except TypeError, ValueError:
            raise HTTPException(400, "memory_id must be an integer")

        import aiosqlite

        table = "long_term" if tier == "long-term" else "short_term"
        await agent.memory._ensure_schema()
        async with aiosqlite.connect(agent.memory.db_path) as db:
            cursor = await db.execute(f"DELETE FROM {table} WHERE id = ?", (memory_id,))
            await db.commit()
            if cursor.rowcount == 0:
                raise HTTPException(404, f"Memory {memory_id} not found in {tier}")

        # Return refreshed memory partial
        long_term = []
        short_term = []
        cols = "id, category, subject, content, source, confidence, created_at, updated_at"
        async with aiosqlite.connect(agent.memory.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(f"SELECT {cols} FROM long_term ORDER BY updated_at DESC")
            long_term = [dict(row) for row in await cursor.fetchall()]
            cursor = await db.execute(
                "SELECT id, content, context, expires_at, created_at "
                "FROM short_term WHERE expires_at > datetime('now') "
                "ORDER BY created_at DESC"
            )
            short_term = [dict(row) for row in await cursor.fetchall()]
        return _render_partial(
            "partials/memory.html",
            long_term=long_term,
            short_term=short_term,
        )

    @app.post("/memory/consolidate", dependencies=[Depends(auth)])
    async def trigger_consolidation() -> HTMLResponse:
        agent = agent_state.agent
        if not agent:
            raise HTTPException(503, "Agent not running")
        result = await agent.memory.consolidate_and_cleanup(
            llm=agent.llm,
            model=agent.config.memory.consolidation_model,
        )
        promoted = result.get("promoted_to_long_term", 0)
        expired = result.get("expired_deleted", 0)
        return HTMLResponse(
            f'<span class="alert-success">{promoted} promoted, {expired} expired deleted</span>'
        )

    # ── Logs API ───────────────────────────────────────────────────────

    @app.get("/logs", dependencies=[Depends(auth)])
    async def get_logs(lines: int = 100) -> dict:
        recent = list(_LOG_BUFFER)
        if lines < len(recent):
            recent = recent[-lines:]
        return {"count": len(recent), "lines": recent}

    # ── Agent status (JSON API) ──────────────────────────────────────

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

    # NOTE: lifecycle POST endpoints (/agent/start, /agent/stop,
    # /agent/restart) are attached in core/main.py via
    # _attach_lifecycle_routes() so they can access _start_agent /
    # _stop_agent without circular imports.

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
    async def setup_save_step(request: Request) -> HTMLResponse:
        """Save config values for a setup step and advance. Returns the next step partial."""
        from core.config_store import SETUP_STEPS

        content_type = request.headers.get("content-type", "")

        if "application/x-www-form-urlencoded" in content_type:
            form_data = await request.form()
            step = str(form_data.get("step", ""))
            values = {}
            for key, val in form_data.items():
                if key.startswith("values[") and key.endswith("]"):
                    config_key = key[7:-1]
                    values[config_key] = str(val)
        else:
            body = await request.json()
            step = body.get("step", "")
            values = body.get("values", {})

        if values:
            await config_store.set_many(values)
            log.info("Setup step %r: saved %d values", step, len(values))

        if step not in SETUP_STEPS:
            raise HTTPException(400, f"Unknown step: {step}")
        await config_store.set_setup_step(step)

        ctx = await _wizard_step_context(step, config_store)
        return _render_wizard_step(step, SETUP_STEPS, ctx)

    @app.post("/setup/step/identity")
    async def setup_save_identity(request: Request) -> HTMLResponse:
        """Handle identity step form submission with character/personalia seeding."""
        from core.config_store import SETUP_STEPS

        form_data = await request.form()
        agent_name = str(form_data.get("agent_name", "")).strip() or "Clio"
        owner_name = str(form_data.get("owner_name", "")).strip() or "User"
        timezone = str(form_data.get("timezone", "")).strip() or "UTC"

        values = {
            "agent.name": agent_name,
            "agent.owner_name": owner_name,
            "agent.timezone": timezone,
        }

        # Seed default character
        values["agent.character"] = "\n".join(
            [
                "# Character",
                "",
                "## Tone",
                "",
                "- Be concise. Messages should be short and direct — no filler.",
                '- Be warm but not sycophantic. No "Great question!" or "Of course!". Just answer.',
                f"- When acting on {owner_name}'s behalf (emails, messages), match their communication style.",
                f"- When messaging {owner_name}'s contacts, always identify yourself unless told otherwise.",
                "",
                "## Decision-making",
                "",
                "- When unsure about an action, ask. When confident and pre-approved, just do it.",
                "- If multiple contacts match a name, present the options — never guess.",
                "- If a command fails, read the error and try to fix it before reporting back.",
                "- Prefer structured data (JSON output flags) over free-text parsing when available.",
                "",
                "## Language",
                "",
                "- Default to English.",
                "",
                "## Proactive behaviors",
                "",
                "When running scheduled tasks (morning briefing, email checks):",
                "- Be brief and scannable.",
                "- Only flag truly important items.",
                "- Group related information together.",
            ]
        )

        # Seed default personalia
        today = datetime.now().strftime("%Y-%m-%d")
        values["agent.personalia"] = "\n".join(
            [
                "# Personalia",
                "",
                "## Identity",
                "",
                f"- Name: {agent_name}",
                f"- Owner: {owner_name}",
                "- Role: Personal AI assistant",
                "",
                "## Capabilities",
                "",
                "- Conversational interaction via Telegram (text)",
                "- Can execute CLI commands via a whitelisted tool executor",
                "",
                "## Limitations",
                "",
                "- Cannot make phone calls",
                "- Cannot access websites or browse the internet",
                f"- Cannot access files on {owner_name}'s personal devices",
                f"- Always needs permission before sending messages or emails on {owner_name}'s behalf",
                "",
                "## History",
                "",
                f"- {today}: Initial setup",
            ]
        )

        await config_store.set_many(values)
        log.info("Setup identity: saved %d values", len(values))

        next_step = "telegram"
        await config_store.set_setup_step(next_step)
        ctx = await _wizard_step_context(next_step, config_store)
        return _render_wizard_step(next_step, SETUP_STEPS, ctx)

    @app.post("/setup/step/calendar")
    async def setup_save_calendar(request: Request) -> HTMLResponse:
        """Handle calendar step form submission."""
        from core.config_store import SETUP_STEPS

        form_data = await request.form()
        cal_name = str(form_data.get("cal_name", "")).strip()
        cal_url = str(form_data.get("cal_url", "")).strip()
        cal_user = str(form_data.get("cal_username", "")).strip()
        cal_pass = str(form_data.get("cal_password", "")).strip()

        values = {}
        if cal_name and cal_url:
            values["calendar.providers"] = json.dumps(
                [{"name": cal_name, "url": cal_url, "username": cal_user, "password": cal_pass}]
            )

        if values:
            await config_store.set_many(values)
            log.info("Setup calendar: saved %d values", len(values))

        next_step = "search"
        await config_store.set_setup_step(next_step)
        ctx = await _wizard_step_context(next_step, config_store)
        return _render_wizard_step(next_step, SETUP_STEPS, ctx)

    @app.post("/setup/test-connection")
    async def test_connection(request: Request) -> dict:
        payload = await request.json()
        service = payload.get("service", "")

        if service == "anthropic":
            return await _test_anthropic(payload.get("api_key", ""))
        if service == "telegram":
            return await _test_telegram(payload.get("bot_token", ""))
        if service == "tavily":
            return await _test_tavily(payload.get("api_key", ""))
        return {"ok": False, "error": f"Unknown service: {service}"}

    return app, auth


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
