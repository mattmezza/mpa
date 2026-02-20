"""Admin API — FastAPI app for health checks, config management, permissions,
memory inspection, log streaming, and agent lifecycle control.

Uses Jinja2 templates with HTMX for the UI. All endpoints (except /health,
/setup/*, /login, and /static/*) require Bearer token auth matching the
stored admin password hash.
"""

from __future__ import annotations

import collections
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader
from pydantic import BaseModel

from core.config_store import ConfigStore
from core.wacli import WacliManager

if TYPE_CHECKING:
    from core.agent import AgentCore
    from core.skills import SkillsStore

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
            ("agent.llm_provider", "provider"),
            ("agent.anthropic_api_key", "anthropic_api_key"),
            ("agent.openai_api_key", "openai_api_key"),
            ("agent.openai_base_url", "openai_base_url"),
            ("agent.google_api_key", "google_api_key"),
            ("agent.google_base_url", "google_base_url"),
            ("agent.grok_api_key", "grok_api_key"),
            ("agent.grok_base_url", "grok_base_url"),
            ("agent.deepseek_api_key", "deepseek_api_key"),
            ("agent.deepseek_base_url", "deepseek_base_url"),
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
    elif step == "email":
        val = await config_store.get("email.himalaya.toml")
        if val:
            ctx["himalaya_toml"] = val
    elif step == "telegram":
        for key, var in (
            ("channels.telegram.bot_token", "bot_token"),
            ("channels.telegram.allowed_user_ids", "user_ids"),
        ):
            val = await config_store.get(key)
            if val:
                ctx[var] = val
    elif step == "whatsapp":
        for key, var in (("channels.whatsapp.allowed_numbers", "allowed_numbers"),):
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
        val = await config_store.get("admin.password_hash")
        if val:
            ctx["admin_key"] = ""
    return ctx


async def _channel_list_context(
    config_store: ConfigStore,
    wacli: WacliManager | None = None,
) -> dict[str, list[dict[str, object]]]:
    channels: list[dict[str, object]] = []

    tg_enabled_raw = await config_store.get("channels.telegram.enabled")
    tg_enabled = str(tg_enabled_raw).lower() == "true"
    tg_token = await config_store.get("channels.telegram.bot_token")
    tg_users = await config_store.get("channels.telegram.allowed_user_ids")
    tg_configured = bool(tg_token or tg_users or tg_enabled)
    if tg_configured:
        tg_detail = "Not configured"
        if tg_token:
            tg_detail = "Bot token set"
            if tg_users:
                tg_detail = f"Bot token set · Users: {tg_users}"
        channels.append(
            {
                "key": "telegram",
                "label": "Telegram",
                "enabled": tg_enabled,
                "detail": tg_detail,
            }
        )

    wa_enabled_raw = await config_store.get("channels.whatsapp.enabled")
    wa_enabled = str(wa_enabled_raw).lower() == "true"
    wa_bridge = await config_store.get("channels.whatsapp.bridge_url")
    wa_numbers = await config_store.get("channels.whatsapp.allowed_numbers")
    wa_configured = bool(wa_bridge or wa_numbers or wa_enabled)
    if wa_configured:
        wa_detail = "Not configured"
        wa_auth_status = ""
        wa_auth_class = "badge-off"
        if wa_bridge:
            wa_detail = "Local wacli"
            if wa_numbers:
                wa_detail = f"Local wacli · Numbers: {wa_numbers}"
            try:
                status = await (wacli or WacliManager()).auth_status()
                if status.get("authenticated") is True:
                    wa_auth_status = "Auth ok"
                    wa_auth_class = "badge-ok"
                elif status.get("running") is False:
                    wa_auth_status = "Auth stopped"
                    wa_auth_class = "badge-off"
                else:
                    wa_auth_status = "Auth required"
                    wa_auth_class = "badge-warn"
            except Exception:
                wa_auth_status = "Auth unknown"
                wa_auth_class = "badge-off"
        channels.append(
            {
                "key": "whatsapp",
                "label": "WhatsApp",
                "enabled": wa_enabled,
                "detail": wa_detail,
                "auth_status": wa_auth_status,
                "auth_class": wa_auth_class,
            }
        )

    return {"channels": channels}


async def _channel_wizard_context(
    config_store: ConfigStore,
    channel: str,
) -> dict[str, str]:
    ctx: dict[str, str] = {}
    if channel == "telegram":
        bot_token = await config_store.get("channels.telegram.bot_token")
        user_ids = await config_store.get("channels.telegram.allowed_user_ids")
        if bot_token:
            ctx["bot_token"] = bot_token
        if user_ids:
            ctx["user_ids"] = user_ids
    if channel == "whatsapp":
        enabled_raw = await config_store.get("channels.whatsapp.enabled")
        enabled = str(enabled_raw).lower() != "false"
        ctx["enabled"] = "true" if enabled else "false"
        bridge_url = await config_store.get("channels.whatsapp.bridge_url")
        allowed_numbers = await config_store.get("channels.whatsapp.allowed_numbers")
        if bridge_url:
            ctx["bridge_url"] = bridge_url
        if allowed_numbers:
            ctx["allowed_numbers"] = allowed_numbers
    return ctx


async def _calendar_providers_context(config_store: ConfigStore) -> list[dict[str, str]]:
    raw = await config_store.get("calendar.providers")
    if not raw:
        return []
    try:
        providers = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(providers, list):
        return []
    cleaned: list[dict[str, str]] = []
    for p in providers:
        if not isinstance(p, dict):
            continue
        cleaned.append(
            {
                "name": str(p.get("name", "")),
                "url": str(p.get("url", "")),
                "username": str(p.get("username", "")),
                "password": str(p.get("password", "")),
            }
        )
    return cleaned


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

    def __init__(self, agent: AgentCore | None = None, status: str = "STOPPED"):
        self.agent: AgentCore | None = agent
        self.status: str = status


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


class SkillUpsertIn(BaseModel):
    name: str
    content: str


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str


class CalendarProvidersIn(BaseModel):
    providers: list[dict[str, str]]


class WhatsAppTestIn(BaseModel):
    pass


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

        password_hash = await config_store.get("admin.password_hash")
        password_salt = await config_store.get("admin.password_salt")
        if not password_hash or not password_salt:
            return

        if not credentials:
            raise HTTPException(status_code=401, detail="Invalid or missing API key")

        if not await config_store.verify_admin_password(credentials.credentials):
            raise HTTPException(status_code=401, detail="Invalid or missing API key")

    return _check_auth


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_admin_app(
    agent_state: AgentState,
    config_store: ConfigStore,
) -> tuple[FastAPI, object]:
    wacli = WacliManager()
    app = FastAPI(
        title="Personal Agent Admin",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    # Mount static files
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    auth = _make_auth_dependency(config_store)

    # Keys managed by dedicated tabs — excluded from the generic Config tab.
    _IDENTITY_KEYS = {
        "agent.character",
        "agent.personalia",
        "agent.name",
        "agent.owner_name",
        "agent.timezone",
    }
    _LLM_KEYS = {
        "agent.llm_provider",
        "agent.anthropic_api_key",
        "agent.openai_api_key",
        "agent.openai_base_url",
        "agent.google_api_key",
        "agent.google_base_url",
        "agent.grok_api_key",
        "agent.grok_base_url",
        "agent.deepseek_api_key",
        "agent.deepseek_base_url",
        "agent.model",
    }
    _SEARCH_PREFIX = "search."
    _MEMORY_PREFIX = "memory."
    _CHANNEL_PREFIX = "channels."
    _SCHEDULER_PREFIX = "scheduler."
    _CALENDAR_PREFIX = "calendar."
    _YOU_PREFIX = "you."
    _VOICE_PREFIX = "voice."
    _HISTORY_PREFIX = "history."
    _EMAIL_PREFIX = "email."

    def _is_managed_key(key: str) -> bool:
        """Return True if this key is managed by a dedicated tab (not Config)."""
        if key in _IDENTITY_KEYS or key in _LLM_KEYS:
            return True
        for prefix in (
            _SEARCH_PREFIX,
            _MEMORY_PREFIX,
            _CHANNEL_PREFIX,
            _SCHEDULER_PREFIX,
            _CALENDAR_PREFIX,
            _YOU_PREFIX,
            _VOICE_PREFIX,
            _HISTORY_PREFIX,
            _EMAIL_PREFIX,
        ):
            if key.startswith(prefix):
                return True
        return False

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

    @app.get("/setup", response_model=None)
    async def setup_page() -> Response:
        """Setup wizard page."""
        from core.config_store import SETUP_STEPS

        complete = await config_store.is_setup_complete()
        if complete:
            return RedirectResponse("/admin", status_code=302)

        step = await config_store.get_setup_step()
        step_ctx = await _wizard_step_context(step, config_store)
        return _render("setup.html", steps=SETUP_STEPS, current_step=step, step_ctx=step_ctx)

    @app.get("/admin", response_model=None)
    async def admin_page() -> Response:
        """Admin dashboard page."""
        setup_complete = await config_store.is_setup_complete()
        if not setup_complete:
            return RedirectResponse("/setup", status_code=302)
        owner_name = await config_store.get("agent.owner_name") or ""
        agent_name = await config_store.get("agent.name") or ""
        return _render(
            "dashboard.html",
            owner_name=owner_name,
            agent_name=agent_name,
        )

    @app.get("/admin/skills/new", response_model=None)
    async def admin_skill_new() -> Response:
        """New skill editor page."""
        setup_complete = await config_store.is_setup_complete()
        if not setup_complete:
            return RedirectResponse("/setup", status_code=302)
        return _render(
            "skill_editor.html",
            skill_name="",
            skill_content="",
            is_new=True,
        )

    @app.get("/admin/skills/{name}", response_model=None)
    async def admin_skill_editor(name: str) -> Response:
        """Skill editor page."""
        setup_complete = await config_store.is_setup_complete()
        if not setup_complete:
            return RedirectResponse("/setup", status_code=302)
        store = await _skills_store_from_config(config_store)
        skill = await store.get_skill(name)
        if not skill:
            raise HTTPException(404, f"Skill not found: {name}")
        return _render(
            "skill_editor.html",
            skill_name=skill.get("name", ""),
            skill_content=skill.get("content", ""),
            is_new=False,
        )

    @app.get("/", response_model=None)
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
        status = agent_state.status
        if running and status not in ("STARTING", "RESTARTING", "STOPPING"):
            status = "RUNNING"
        return _render_partial(
            "partials/status.html",
            running=running,
            status=status,
            channels=channels,
            scheduler_jobs=scheduler_jobs,
        )

    @app.get("/partials/identity", dependencies=[Depends(auth)])
    async def partial_identity() -> HTMLResponse:
        """Agent identity tab partial."""
        character = await config_store.get("agent.character") or ""
        personalia = await config_store.get("agent.personalia") or ""
        agent_name = await config_store.get("agent.name") or ""
        stt_model = await config_store.get("voice.stt_model") or "base"
        tts_voice = await config_store.get("voice.tts_voice") or "en-US-AvaNeural"
        tts_enabled = await config_store.get("voice.tts_enabled") or "true"
        return _render_partial(
            "partials/identity.html",
            character=character,
            personalia=personalia,
            agent_name=agent_name,
            stt_model=stt_model,
            tts_voice=tts_voice,
            tts_enabled=tts_enabled,
        )

    @app.get("/partials/you", dependencies=[Depends(auth)])
    async def partial_you() -> HTMLResponse:
        """You tab partial — info about the user the assistant serves."""
        owner_name = await config_store.get("agent.owner_name") or ""
        timezone = await config_store.get("agent.timezone") or ""
        you_personalia = await config_store.get("you.personalia") or ""
        return _render_partial(
            "partials/you.html",
            owner_name=owner_name,
            timezone=timezone,
            you_personalia=you_personalia,
        )

    @app.get("/partials/permissions", dependencies=[Depends(auth)])
    async def partial_permissions() -> HTMLResponse:
        """Permissions tab partial."""
        agent = agent_state.agent
        rules = agent.permissions.rules if agent else {}
        return _render_partial("partials/permissions.html", rules=rules)

    @app.get("/partials/skills", dependencies=[Depends(auth)])
    async def partial_skills() -> HTMLResponse:
        """Skills tab partial."""
        store = await _skills_store_from_config(config_store)
        skills = await store.list_skills()
        return _render_partial("partials/skills.html", skills=skills)

    @app.get("/partials/channels", dependencies=[Depends(auth)])
    async def partial_channels() -> HTMLResponse:
        """Channels tab partial."""
        channel_data = await _channel_list_context(config_store, wacli)
        return _render_partial("partials/channels.html", **channel_data)

    @app.get("/partials/calendars", dependencies=[Depends(auth)])
    async def partial_calendars() -> HTMLResponse:
        """Calendars tab partial."""
        providers = await _calendar_providers_context(config_store)
        return _render_partial("partials/calendars.html", providers=providers)

    @app.get("/partials/email", dependencies=[Depends(auth)])
    async def partial_email() -> HTMLResponse:
        """Email tab partial."""
        himalaya_toml = await config_store.get("email.himalaya.toml") or ""
        return _render_partial("partials/email.html", himalaya_toml=himalaya_toml)

    @app.get("/partials/admin", dependencies=[Depends(auth)])
    async def partial_admin() -> HTMLResponse:
        """Admin tab partial."""
        return _render_partial("partials/admin.html")

    @app.get("/partials/llm", dependencies=[Depends(auth)])
    async def partial_llm() -> HTMLResponse:
        """LLM tab partial."""
        provider = await config_store.get("agent.llm_provider") or "anthropic"
        anthropic_api_key = await config_store.get("agent.anthropic_api_key") or ""
        openai_api_key = await config_store.get("agent.openai_api_key") or ""
        openai_base_url = await config_store.get("agent.openai_base_url") or ""
        google_api_key = await config_store.get("agent.google_api_key") or ""
        google_base_url = await config_store.get("agent.google_base_url") or ""
        grok_api_key = await config_store.get("agent.grok_api_key") or ""
        grok_base_url = await config_store.get("agent.grok_base_url") or ""
        deepseek_api_key = await config_store.get("agent.deepseek_api_key") or ""
        deepseek_base_url = await config_store.get("agent.deepseek_base_url") or ""
        model = await config_store.get("agent.model") or "claude-4-6-sonnet"
        extraction_provider = await config_store.get("memory.extraction_provider") or "anthropic"
        extraction_model = await config_store.get("memory.extraction_model") or "claude-4-5-haiku"
        consolidation_provider = (
            await config_store.get("memory.consolidation_provider") or "anthropic"
        )
        consolidation_model = (
            await config_store.get("memory.consolidation_model") or "claude-4-5-haiku"
        )
        return _render_partial(
            "partials/llm.html",
            provider=provider,
            anthropic_api_key=anthropic_api_key,
            openai_api_key=openai_api_key,
            openai_base_url=openai_base_url,
            google_api_key=google_api_key,
            google_base_url=google_base_url,
            grok_api_key=grok_api_key,
            grok_base_url=grok_base_url,
            deepseek_api_key=deepseek_api_key,
            deepseek_base_url=deepseek_base_url,
            model=model,
            extraction_provider=extraction_provider,
            extraction_model=extraction_model,
            consolidation_provider=consolidation_provider,
            consolidation_model=consolidation_model,
        )

    @app.get("/partials/search", dependencies=[Depends(auth)])
    async def partial_search() -> HTMLResponse:
        """Search tab partial."""
        enabled = await config_store.get("search.enabled") or "false"
        provider = await config_store.get("search.provider") or "tavily"
        api_key = await config_store.get("search.api_key") or ""
        max_results = await config_store.get("search.max_results") or "5"
        return _render_partial(
            "partials/search.html",
            enabled=enabled,
            provider=provider,
            api_key=api_key,
            max_results=max_results,
        )

    @app.get("/partials/memory", dependencies=[Depends(auth)])
    async def partial_memory() -> HTMLResponse:
        """Memory tab partial."""
        # Memory config
        memory_long_term_limit = await config_store.get("memory.long_term_limit") or "50"

        # Memory data
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
            memory_long_term_limit=memory_long_term_limit,
        )

    @app.get("/partials/history", dependencies=[Depends(auth)])
    async def partial_history() -> HTMLResponse:
        """History tab partial."""
        mode = await config_store.get("history.mode") or "injection"
        max_turns = await config_store.get("history.max_turns") or "10"
        return _render_partial(
            "partials/history.html",
            mode=mode,
            max_turns=max_turns,
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

    # ── Jobs partial + API ─────────────────────────────────────────────

    async def _persist_jobs_to_config(agent: AgentCore, cs: ConfigStore) -> None:
        """Persist the current scheduler jobs to config store so they survive restarts."""
        jobs_data = []
        for ap_job in agent.scheduler.scheduler.get_jobs():
            func_name = getattr(ap_job, "func_ref", "") or str(getattr(ap_job, "func", ""))
            kwargs = ap_job.kwargs or {}
            trigger = ap_job.trigger

            # Only persist cron jobs (skip one-shot date triggers)
            if not hasattr(trigger, "fields"):
                continue

            # Reconstruct cron string
            parts = []
            field_order = ["minute", "hour", "day", "month", "day_of_week"]
            field_map = {f.name: f for f in trigger.fields}
            for name in field_order:
                f = field_map.get(name)
                parts.append(str(f) if f else "*")
            cron = " ".join(parts)

            if "run_agent_task" in func_name:
                job_type = "agent"
                task = kwargs.get("task", "")
            elif "run_system_command" in func_name:
                job_type = "system"
                task = kwargs.get("command", "")
            elif "run_memory_consolidation" in func_name:
                job_type = "memory_consolidation"
                task = ""
            else:
                continue

            jobs_data.append(
                {
                    "id": ap_job.id,
                    "cron": cron,
                    "type": job_type,
                    "task": task,
                    "channel": kwargs.get("channel", "telegram"),
                }
            )

        await cs.set("scheduler.jobs", json.dumps(jobs_data))

    def _get_jobs_list() -> list[dict]:
        """Build a list of job dicts from the running scheduler + config store."""
        agent = agent_state.agent
        if not agent:
            return []
        jobs = []
        for ap_job in agent.scheduler.scheduler.get_jobs():
            trigger = ap_job.trigger
            # Reconstruct cron expression from APScheduler trigger fields
            cron = ""
            if hasattr(trigger, "fields"):
                parts = []
                field_order = ["minute", "hour", "day", "month", "day_of_week"]
                field_map = {f.name: f for f in trigger.fields}
                for name in field_order:
                    f = field_map.get(name)
                    parts.append(str(f) if f else "*")
                cron = " ".join(parts)
            elif hasattr(trigger, "run_date"):
                cron = f"once @ {trigger.run_date}"

            # Determine job type and task from the stored function + kwargs
            func_name = getattr(ap_job, "func_ref", "") or str(getattr(ap_job, "func", ""))
            kwargs = ap_job.kwargs or {}
            if "run_agent_task" in func_name:
                job_type = "agent"
                task = kwargs.get("task", "")
            elif "run_system_command" in func_name:
                job_type = "system"
                task = kwargs.get("command", "")
            elif "run_memory_consolidation" in func_name:
                job_type = "memory_consolidation"
                task = ""
            else:
                job_type = "unknown"
                task = str(kwargs)

            next_run = ""
            if ap_job.next_run_time:
                next_run = ap_job.next_run_time.strftime("%Y-%m-%d %H:%M")

            silent = bool(kwargs.get("silent", False))
            if job_type == "agent" and silent:
                job_type = "agent_silent"

            jobs.append(
                {
                    "id": ap_job.id,
                    "cron": cron,
                    "type": job_type,
                    "task": task,
                    "channel": kwargs.get("channel", "telegram"),
                    "next_run": next_run,
                }
            )
        return jobs

    @app.get("/partials/jobs", dependencies=[Depends(auth)])
    async def partial_jobs() -> HTMLResponse:
        """Jobs tab partial."""
        jobs = _get_jobs_list()
        agent_running = agent_state.agent is not None
        return _render_partial("partials/jobs.html", jobs=jobs, agent_running=agent_running)

    @app.post("/jobs", dependencies=[Depends(auth)])
    async def upsert_job(request: Request) -> HTMLResponse:
        """Add or update a scheduled job. Returns refreshed jobs partial."""
        agent = agent_state.agent
        if not agent:
            raise HTTPException(503, "Agent not running")
        content_type = request.headers.get("content-type", "")
        if "application/x-www-form-urlencoded" in content_type:
            body = await request.form()
        else:
            body = await request.json()

        job_id = str(body.get("job_id", "")).strip()
        cron = str(body.get("cron", "")).strip()
        job_type = str(body.get("type", "agent")).strip()
        task = str(body.get("task", "")).strip()
        channel = str(body.get("channel", "telegram")).strip()

        if not job_id:
            raise HTTPException(400, "Job ID is required")
        if not cron:
            raise HTTPException(400, "Cron schedule is required")
        if job_type not in ("agent", "agent_silent", "system", "memory_consolidation"):
            raise HTTPException(400, f"Invalid job type: {job_type}")
        if job_type != "memory_consolidation" and not task:
            raise HTTPException(400, "Task is required for agent/system jobs")

        from core.scheduler import (
            _parse_cron,
            run_agent_task,
            run_memory_consolidation,
            run_system_command,
        )

        try:
            cron_kwargs = _parse_cron(cron)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

        # Register the job in APScheduler
        if job_type == "system":
            agent.scheduler.scheduler.add_job(
                run_system_command,
                "cron",
                id=job_id,
                kwargs={"command": task},
                replace_existing=True,
                **cron_kwargs,
            )
        elif job_type == "memory_consolidation":
            agent.scheduler.scheduler.add_job(
                run_memory_consolidation,
                "cron",
                id=job_id,
                replace_existing=True,
                **cron_kwargs,
            )
        else:
            silent = job_type == "agent_silent"
            agent.scheduler.scheduler.add_job(
                run_agent_task,
                "cron",
                id=job_id,
                kwargs={
                    "task": task,
                    "channel": channel,
                    "job_id": job_id,
                    "silent": silent,
                },
                replace_existing=True,
                **cron_kwargs,
            )

        log.info("Job %r upserted via admin: %s (%s)", job_id, cron, job_type)

        # Also persist to config store so jobs survive restarts
        await _persist_jobs_to_config(agent, config_store)

        jobs = _get_jobs_list()
        return _render_partial("partials/jobs.html", jobs=jobs, agent_running=True)

    @app.post("/jobs/delete", dependencies=[Depends(auth)])
    async def delete_job(request: Request) -> HTMLResponse:
        """Delete a scheduled job. Returns refreshed jobs partial."""
        agent = agent_state.agent
        if not agent:
            raise HTTPException(503, "Agent not running")
        content_type = request.headers.get("content-type", "")
        if "application/x-www-form-urlencoded" in content_type:
            body = await request.form()
        else:
            body = await request.json()

        job_id = str(body.get("job_id", "")).strip()
        if not job_id:
            raise HTTPException(400, "Missing 'job_id' in request body")

        try:
            agent.scheduler.scheduler.remove_job(job_id)
        except Exception:
            raise HTTPException(404, f"Job not found: {job_id}")

        log.info("Job %r deleted via admin", job_id)
        await _persist_jobs_to_config(agent, config_store)

        jobs = _get_jobs_list()
        return _render_partial("partials/jobs.html", jobs=jobs, agent_running=True)

    @app.post("/jobs/run", dependencies=[Depends(auth)])
    async def run_job_now(request: Request) -> HTMLResponse:
        """Trigger a job to run immediately. Returns a flash message."""
        agent = agent_state.agent
        if not agent:
            raise HTTPException(503, "Agent not running")
        content_type = request.headers.get("content-type", "")
        if "application/x-www-form-urlencoded" in content_type:
            body = await request.form()
        else:
            body = await request.json()

        job_id = str(body.get("job_id", "")).strip()
        if not job_id:
            raise HTTPException(400, "Missing 'job_id' in request body")

        # Find the job in APScheduler
        ap_job = agent.scheduler.scheduler.get_job(job_id)
        if not ap_job:
            raise HTTPException(404, f"Job not found: {job_id}")

        # Run it immediately in the background
        import asyncio

        func_name = getattr(ap_job, "func_ref", "") or str(getattr(ap_job, "func", ""))
        kwargs = ap_job.kwargs or {}

        if "run_agent_task" in func_name:
            from core.scheduler import run_agent_task

            asyncio.create_task(run_agent_task(**kwargs))
        elif "run_system_command" in func_name:
            from core.scheduler import run_system_command

            asyncio.create_task(run_system_command(**kwargs))
        elif "run_memory_consolidation" in func_name:
            from core.scheduler import run_memory_consolidation

            asyncio.create_task(run_memory_consolidation())
        else:
            return HTMLResponse('<span class="alert-error">Unknown job function</span>')

        log.info("Job %r triggered manually via admin", job_id)
        return HTMLResponse(
            f'<span class="alert-success">Job &quot;{job_id}&quot; triggered — check logs '
            "for output</span>"
        )

    # ── Config API ─────────────────────────────────────────────────────

    @app.get("/config", dependencies=[Depends(auth)])
    async def get_config() -> dict:
        data = await config_store.get_all_redacted()
        return {k: v for k, v in data.items() if not _is_managed_key(k)}

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

    @app.get("/config/you-personalia", dependencies=[Depends(auth)])
    async def get_you_personalia() -> dict:
        value = await config_store.get("you.personalia") or ""
        return {"content": value}

    @app.post("/config/you-personalia", dependencies=[Depends(auth)])
    async def put_you_personalia(request: Request) -> dict:
        body = await request.json()
        content = body.get("content", "")
        await config_store.set("you.personalia", content)
        return {"updated": "you.personalia"}

    @app.get("/config/{section}", dependencies=[Depends(auth)])
    async def get_config_section(section: str) -> dict:
        return await config_store.get_section_redacted(section)

    @app.patch("/config", dependencies=[Depends(auth)])
    async def patch_config(body: ConfigPatchIn) -> dict:
        await config_store.set_many(body.values)
        return {"updated": list(body.values.keys())}

    @app.post("/admin/password", dependencies=[Depends(auth)])
    async def change_admin_password(body: PasswordChangeIn) -> dict:
        current = body.current_password.strip()
        new_password = body.new_password.strip()
        if not current or not new_password:
            return {"ok": False, "error": "Both current and new passwords are required."}
        if not await config_store.verify_admin_password(current):
            return {"ok": False, "error": "Current password is incorrect."}
        await config_store.set_admin_password(new_password)
        return {"ok": True, "token": new_password}

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
        filtered = {k: v for k, v in data.items() if not _is_managed_key(k)}
        config_items = sorted(filtered.items())
        return _render_partial("partials/config.html", config_items=config_items)

    @app.post("/calendar/providers", dependencies=[Depends(auth)])
    async def save_calendar_providers(body: CalendarProvidersIn) -> dict:
        providers = []
        for p in body.providers:
            name = str(p.get("name", "")).strip()
            url = str(p.get("url", "")).strip()
            username = str(p.get("username", "")).strip()
            password = str(p.get("password", "")).strip()
            if not any([name, url, username, password]):
                continue
            providers.append(
                {
                    "name": name,
                    "url": url,
                    "username": username,
                    "password": password,
                }
            )
        await config_store.set("calendar.providers", json.dumps(providers))
        return {"ok": True}

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

    # ── Skills API ────────────────────────────────────────────────────────

    @app.get("/skills", dependencies=[Depends(auth)])
    async def list_skills() -> dict:
        store = await _skills_store_from_config(config_store)
        skills = await store.list_skills()
        return {"count": len(skills), "skills": skills}

    @app.get("/skills/{name}", dependencies=[Depends(auth)])
    async def get_skill(name: str) -> dict:
        store = await _skills_store_from_config(config_store)
        skill = await store.get_skill(name)
        if not skill:
            raise HTTPException(404, f"Skill not found: {name}")
        return skill

    # ── Channels API ──────────────────────────────────────────────────────

    @app.get("/channels/wizard", dependencies=[Depends(auth)])
    async def channel_wizard(channel: str = "telegram") -> HTMLResponse:
        key = channel.strip().lower()
        if key == "telegram":
            ctx = await _channel_wizard_context(config_store, "telegram")
            return _render_partial("partials/channel_wizard_telegram.html", **ctx)
        if key == "whatsapp":
            ctx = await _channel_wizard_context(config_store, "whatsapp")
            return _render_partial("partials/channel_wizard_whatsapp.html", **ctx)
        raise HTTPException(400, f"Unknown channel: {channel}")

    @app.post("/channels/telegram", dependencies=[Depends(auth)])
    async def save_channel_telegram(request: Request) -> HTMLResponse:
        body = await request.json()
        bot_token = str(body.get("bot_token", "")).strip()
        user_ids = str(body.get("user_ids", "")).strip()
        enabled = str(body.get("enabled", "true")).lower() == "true"
        if not bot_token:
            raise HTTPException(400, "Bot token is required")
        values = {
            "channels.telegram.enabled": str(enabled).lower(),
            "channels.telegram.bot_token": bot_token,
            "channels.telegram.allowed_user_ids": user_ids,
        }
        await config_store.set_many(values)
        channel_data = await _channel_list_context(config_store, wacli)
        return _render_partial("partials/channels.html", **channel_data)

    @app.post("/channels/whatsapp", dependencies=[Depends(auth)])
    async def save_channel_whatsapp(request: Request) -> HTMLResponse:
        body = await request.json()
        bridge_url = str(body.get("bridge_url", "")).strip() or "local-wacli"
        allowed_numbers = str(body.get("allowed_numbers", "")).strip()
        enabled = str(body.get("enabled", "true")).lower() == "true"
        values = {
            "channels.whatsapp.enabled": str(enabled).lower(),
            "channels.whatsapp.bridge_url": bridge_url,
            "channels.whatsapp.allowed_numbers": allowed_numbers,
        }
        await config_store.set_many(values)
        if not enabled:
            try:
                await wacli.stop_auth()
                await wacli.stop_sync()
            except Exception as exc:
                log.warning("Failed to stop WhatsApp auth: %s", exc)
        channel_data = await _channel_list_context(config_store, wacli)
        return _render_partial("partials/channels.html", **channel_data)

    @app.post("/channels/whatsapp/test", dependencies=[Depends(auth)])
    async def test_channel_whatsapp(body: WhatsAppTestIn) -> dict:
        status = await wacli.auth_status()
        available = status.get("available") is True
        result: dict = {"ok": available, "response": status}
        if not available:
            result["error"] = (
                "wacli binary not found. "
                "Run 'make dev-wa' or 'cd tools/wacli && pnpm build' to compile it."
            )
        return result

    @app.get("/channels/whatsapp/auth/status", dependencies=[Depends(auth)])
    async def whatsapp_auth_status() -> dict:
        status = await wacli.auth_status()
        return {"ok": True, **status}

    @app.post("/channels/whatsapp/auth/start", dependencies=[Depends(auth)])
    async def whatsapp_auth_start() -> dict:
        await wacli.start_auth()
        return {"ok": True}

    @app.post("/channels/whatsapp/auth/stop", dependencies=[Depends(auth)])
    async def whatsapp_auth_stop() -> dict:
        await wacli.stop_auth()
        await wacli.stop_sync()
        return {"ok": True}

    @app.get("/channels/whatsapp/auth/qr", dependencies=[Depends(auth)])
    async def whatsapp_auth_qr() -> dict:
        if not wacli.latest_qr:
            await wacli.fetch_latest_qr()
        if not wacli.latest_qr:
            raise HTTPException(404, "No QR available")
        return {"ok": True, "qr": wacli.latest_qr, "latest_qr_at": wacli.latest_qr_at}

    @app.post("/channels/whatsapp/auth/logout", dependencies=[Depends(auth)])
    async def whatsapp_auth_logout() -> dict:
        await wacli.logout()
        return {"ok": True}

    @app.post("/channels/whatsapp/sync/start", dependencies=[Depends(auth)])
    async def whatsapp_sync_start() -> dict:
        await wacli.start_sync()
        return {"ok": True}

    @app.post("/channels/whatsapp/sync/stop", dependencies=[Depends(auth)])
    async def whatsapp_sync_stop() -> dict:
        await wacli.stop_sync()
        return {"ok": True}

    @app.post("/channels/whatsapp/send", dependencies=[Depends(auth)])
    async def whatsapp_send(request: Request) -> dict:
        body = await request.json()
        to = str(body.get("to", "")).strip()
        text = str(body.get("text", "")).strip()
        if not to or not text:
            raise HTTPException(400, "Missing 'to' or 'text'")
        res = await wacli.send_text(to, text)
        if res.get("success") is not True:
            return {"ok": False, "error": res.get("error")}
        return {"ok": True}

    @app.post("/webhook/whatsapp")
    async def whatsapp_webhook(request: Request) -> dict:
        """Webhook for WhatsApp inbound messages."""
        if agent_state.agent is None:
            raise HTTPException(503, "Agent not running")

        body = await request.json()
        channel = agent_state.agent.channels.get("whatsapp")
        if not channel:
            raise HTTPException(404, "WhatsApp channel not enabled")

        try:
            return await channel.handle_webhook(body)
        except Exception as exc:
            log.exception("WhatsApp webhook failed")
            raise HTTPException(500, f"Webhook error: {exc}")

    @app.delete("/channels/{channel}", dependencies=[Depends(auth)])
    async def delete_channel(channel: str) -> HTMLResponse:
        key = channel.strip().lower()
        values: dict[str, str] = {}
        if key == "telegram":
            values = {
                "channels.telegram.enabled": "false",
                "channels.telegram.bot_token": "",
                "channels.telegram.allowed_user_ids": "",
            }
        elif key == "whatsapp":
            try:
                await wacli.logout()
            except Exception as exc:
                log.warning("Failed to logout WhatsApp auth: %s", exc)
            values = {
                "channels.whatsapp.enabled": "false",
                "channels.whatsapp.bridge_url": "",
                "channels.whatsapp.allowed_numbers": "",
            }
        else:
            raise HTTPException(400, f"Unknown channel: {channel}")

        await config_store.set_many(values)
        channel_data = await _channel_list_context(config_store, wacli)
        return _render_partial("partials/channels.html", **channel_data)

    @app.post("/skills", dependencies=[Depends(auth)])
    async def upsert_skill(body: SkillUpsertIn) -> HTMLResponse:
        store = await _skills_store_from_config(config_store)
        name = body.name.strip()
        content = body.content.strip()
        if not name:
            raise HTTPException(400, "Skill name is required")
        if not content:
            raise HTTPException(400, "Skill content is required")
        await store.upsert_skill(name, content)
        skills = await store.list_skills()
        return _render_partial("partials/skills.html", skills=skills)

    @app.post("/skills/delete", dependencies=[Depends(auth)])
    async def delete_skill(request: Request) -> HTMLResponse:
        content_type = request.headers.get("content-type", "")
        if "application/x-www-form-urlencoded" in content_type:
            body = await request.form()
        else:
            body = await request.json()
        name = str(body.get("name", "")).strip()
        if not name:
            raise HTTPException(400, "Missing 'name' in request body")
        store = await _skills_store_from_config(config_store)
        deleted = await store.delete_skill(name)
        if not deleted:
            raise HTTPException(404, f"Skill not found: {name}")
        skills = await store.list_skills()
        return _render_partial("partials/skills.html", skills=skills)

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
        memory_id_raw = body.get("memory_id")
        if tier not in ("long-term", "short-term"):
            raise HTTPException(400, "Tier must be 'long-term' or 'short-term'")
        if memory_id_raw is None:
            raise HTTPException(400, "Missing 'memory_id' in request body")
        # Coerce to int (form-encoded values arrive as strings)
        try:
            memory_id = int(cast(str, memory_id_raw))
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
        memory_long_term_limit = await config_store.get("memory.long_term_limit") or "50"
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
            memory_long_term_limit=memory_long_term_limit,
        )

    @app.post("/memory/consolidate", dependencies=[Depends(auth)])
    async def trigger_consolidation() -> HTMLResponse:
        agent = agent_state.agent
        if not agent:
            raise HTTPException(503, "Agent not running")
        result = await agent.memory.consolidate_and_cleanup(
            llm=agent._memory_llm(agent.config.memory.consolidation_provider),
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
            return {
                "running": False,
                "status": agent_state.status,
                "channels": [],
                "scheduler_jobs": 0,
            }
        return {
            "running": True,
            "status": agent_state.status,
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
                (
                    f"- When acting on {owner_name}'s behalf (emails, messages), match their "
                    "communication style."
                ),
                (
                    f"- When messaging {owner_name}'s contacts, always identify yourself unless "
                    "told otherwise."
                ),
                "",
                "## Decision-making",
                "",
                "- When unsure about an action, ask. When confident and pre-approved, just do it.",
                "- If multiple contacts match a name, present the options — never guess.",
                "- If a command fails, read the error and try to fix it before reporting back.",
                (
                    "- Prefer structured data (JSON output flags) over free-text parsing when "
                    "available."
                ),
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
                (
                    f"- Always needs permission before sending messages or emails on {owner_name}'s"
                    " behalf"
                ),
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
        if service == "openai":
            return await _test_openai(
                payload.get("api_key", ""),
                payload.get("base_url"),
            )
        if service == "google":
            return await _test_openai(
                payload.get("api_key", ""),
                payload.get("base_url"),
                model="gemini-flash-latest",
            )
        if service == "grok":
            return await _test_openai(
                payload.get("api_key", ""),
                payload.get("base_url"),
                model="grok-2-latest",
            )
        if service == "deepseek":
            return await _test_openai(
                payload.get("api_key", ""),
                payload.get("base_url"),
                model="deepseek-chat",
            )
        if service == "telegram":
            return await _test_telegram(payload.get("bot_token", ""))
        if service == "tavily":
            return await _test_tavily(payload.get("api_key", ""))
        return {"ok": False, "error": f"Unknown service: {service}"}

    return app, auth


async def _skills_store_from_config(config_store: ConfigStore) -> SkillsStore:
    from core.skills import SkillsStore

    skills_db_path = await config_store.get("agent.skills_db_path") or "data/skills.db"
    skills_dir = await config_store.get("agent.skills_dir") or "skills/"
    return SkillsStore(db_path=skills_db_path, seed_dir=skills_dir)


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
            text = getattr(block, "text", "")
            if text:
                break
        return {"ok": True, "response": text}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def _test_openai(api_key: str, base_url: str | None, model: str = "gpt-4o-mini") -> dict:
    if not api_key:
        return {"ok": False, "error": "API key is empty"}
    try:
        import importlib

        module = importlib.import_module("openai")
        client_class = cast(Any, getattr(module, "AsyncOpenAI"))
        client = cast(Any, client_class)(api_key=api_key, base_url=base_url or None)
        response = await client.chat.completions.create(
            model=model,
            max_tokens=16,
            messages=[{"role": "user", "content": "Say 'ok'"}],
        )
        text = response.choices[0].message.content or ""
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
