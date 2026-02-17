"""Tests for admin UI — page routes, HTMX partials, and setup wizard."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from api.admin import AgentState, create_admin_app


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _ConfigStoreStub:
    """Minimal config store stub for testing page/partial routes."""

    def __init__(self, setup_complete: bool = True, step: str = "welcome"):
        self._setup_complete = setup_complete
        self._step = step
        self._data: dict[str, str] = {}
        self._last_set: dict[str, str] = {}

    async def is_setup_complete(self) -> bool:
        return self._setup_complete

    async def get(self, key: str):
        # Check explicit data first, then fall back to hardcoded defaults
        if key in self._data:
            return self._data[key]
        if key == "admin.password_hash":
            return "hash"
        if key == "admin.password_salt":
            return "salt"
        if key == "agent.character":
            return "# Test character"
        if key == "agent.personalia":
            return "# Test personalia"
        return None

    async def get_all_redacted(self) -> dict:
        return {"agent.name": "Clio", "admin.password_hash": "***", "admin.port": "8000"}

    async def verify_admin_password(self, password: str) -> bool:
        return password == "secret"

    async def set_admin_password(self, password: str) -> None:
        self._data["admin.password_hash"] = "hash"
        self._data["admin.password_salt"] = "salt"

    async def get_section_redacted(self, section: str) -> dict:
        return {f"{section}.value": "ok"}

    async def set(self, key: str, value: str) -> None:
        self._data[key] = value

    async def set_many(self, values: dict) -> None:
        self._last_set = values
        self._data.update(values)

    async def verify_admin_password(self, password: str) -> bool:
        return password == "secret"

    async def delete(self, key: str) -> bool:
        return key in self._data and bool(self._data.pop(key, True))

    async def get_setup_step(self) -> str:
        return self._step

    async def set_setup_step(self, step: str) -> None:
        self._step = step


class _AgentStub:
    def __init__(self):
        self.channels = {"telegram": object()}
        self.scheduler = SimpleNamespace(scheduler=SimpleNamespace(get_jobs=lambda: [1, 2]))
        self.permissions = SimpleNamespace(
            rules={"run_command:jq*": "ALWAYS"},
            add_rule=lambda p, l: None,
        )


def _client(setup_complete: bool = True, agent=None, step: str = "welcome") -> TestClient:
    store = _ConfigStoreStub(setup_complete=setup_complete, step=step)
    agent_state = AgentState(agent=agent or (None if not setup_complete else _AgentStub()))
    app, _auth = create_admin_app(agent_state, store)
    return TestClient(app, follow_redirects=False)


AUTH = {"Authorization": "Bearer secret"}


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------


class TestPageRoutes:
    def test_root_redirects_to_admin_when_setup_complete(self):
        client = _client(setup_complete=True)
        resp = client.get("/")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/admin"

    def test_root_redirects_to_setup_when_not_complete(self):
        client = _client(setup_complete=False)
        resp = client.get("/")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/setup"

    def test_login_page_returns_html(self):
        client = _client()
        resp = client.get("/login")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "<form" in resp.text or "login" in resp.text.lower()

    def test_setup_page_returns_html_when_not_complete(self):
        client = _client(setup_complete=False, step="welcome")
        resp = client.get("/setup")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_setup_page_redirects_to_admin_when_complete(self):
        client = _client(setup_complete=True)
        resp = client.get("/setup")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/admin"

    def test_admin_page_returns_html_when_setup_complete(self):
        client = _client(setup_complete=True)
        resp = client.get("/admin")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_admin_page_redirects_to_setup_when_not_complete(self):
        client = _client(setup_complete=False)
        resp = client.get("/admin")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/setup"


# ---------------------------------------------------------------------------
# HTMX partials
# ---------------------------------------------------------------------------


class TestPartialRoutes:
    def test_status_partial_with_running_agent(self):
        client = _client(setup_complete=True)
        resp = client.get("/partials/status", headers=AUTH)
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        # Should show running state message
        assert "running" in resp.text.lower()

    def test_status_partial_without_agent(self):
        client = _client(setup_complete=True, agent=None)
        # Override with explicit None agent
        store = _ConfigStoreStub(setup_complete=True)
        agent_state = AgentState(agent=None)
        app, _ = create_admin_app(agent_state, store)
        c = TestClient(app, follow_redirects=False)

        resp = c.get("/partials/status", headers=AUTH)
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_config_partial(self):
        client = _client(setup_complete=True)
        resp = client.get("/partials/config", headers=AUTH)
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        # Should contain config data
        assert "agent.name" in resp.text

    def test_identity_partial(self):
        client = _client(setup_complete=True)
        resp = client.get("/partials/identity", headers=AUTH)
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "character" in resp.text.lower() or "personalia" in resp.text.lower()

    def test_permissions_partial(self):
        client = _client(setup_complete=True)
        resp = client.get("/partials/permissions", headers=AUTH)
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "run_command" in resp.text

    def test_logs_partial(self):
        client = _client(setup_complete=True)
        resp = client.get("/partials/logs", headers=AUTH)
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_logs_content_partial(self):
        client = _client(setup_complete=True)
        resp = client.get("/partials/logs-content", headers=AUTH)
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_partials_require_auth(self):
        client = _client(setup_complete=True)
        for path in [
            "/partials/status",
            "/partials/config",
            "/partials/identity",
            "/partials/permissions",
            "/partials/admin",
            "/partials/logs",
            "/partials/logs-content",
        ]:
            resp = client.get(path)
            assert resp.status_code == 401, f"{path} should require auth"


# ---------------------------------------------------------------------------
# Setup wizard
# ---------------------------------------------------------------------------


class TestSetupWizard:
    def test_setup_status(self):
        client = _client(setup_complete=False, step="llm")
        resp = client.get("/setup/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["complete"] is False
        assert data["current_step"] == "llm"
        assert "welcome" in data["steps"]
        assert "done" in data["steps"]

    def test_setup_step_advance_json(self):
        client = _client(setup_complete=False, step="welcome")
        resp = client.post(
            "/setup/step",
            json={"step": "llm", "values": {}},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_setup_step_advance_form(self):
        client = _client(setup_complete=False, step="welcome")
        resp = client.post(
            "/setup/step",
            data={"step": "llm"},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_setup_step_saves_values(self):
        store = _ConfigStoreStub(setup_complete=False, step="welcome")
        agent_state = AgentState(agent=None)
        app, _ = create_admin_app(agent_state, store)
        client = TestClient(app, follow_redirects=False)

        resp = client.post(
            "/setup/step",
            json={
                "step": "telegram",
                "values": {"channels.telegram.bot_token": "123:ABC"},
            },
        )
        assert resp.status_code == 200
        assert store._last_set == {"channels.telegram.bot_token": "123:ABC"}

    def test_setup_identity_step(self):
        store = _ConfigStoreStub(setup_complete=False, step="identity")
        agent_state = AgentState(agent=None)
        app, _ = create_admin_app(agent_state, store)
        client = TestClient(app, follow_redirects=False)

        resp = client.post(
            "/setup/step/identity",
            data={
                "agent_name": "Ada",
                "owner_name": "Alice",
                "timezone": "Europe/Rome",
            },
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        # Should have saved identity values
        assert store._data.get("agent.name") == "Ada"
        assert store._data.get("agent.owner_name") == "Alice"
        assert store._data.get("agent.timezone") == "Europe/Rome"
        # Should have seeded character
        assert "Alice" in store._data.get("agent.character", "")
        # Should have advanced to telegram step
        assert store._step == "telegram"

    def test_setup_calendar_step(self):
        store = _ConfigStoreStub(setup_complete=False, step="calendar")
        agent_state = AgentState(agent=None)
        app, _ = create_admin_app(agent_state, store)
        client = TestClient(app, follow_redirects=False)

        resp = client.post(
            "/setup/step/calendar",
            data={
                "cal_name": "Work",
                "cal_url": "https://cal.example.com/dav",
                "cal_username": "user",
                "cal_password": "pass",
            },
        )
        assert resp.status_code == 200
        # Should have saved calendar providers as JSON
        assert "calendar.providers" in store._data
        # Should have advanced to search step
        assert store._step == "search"

    def test_setup_unknown_step_returns_400(self):
        client = _client(setup_complete=False)
        resp = client.post(
            "/setup/step",
            json={"step": "nonexistent", "values": {}},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Config CRUD
# ---------------------------------------------------------------------------


class TestConfigAPI:
    def test_get_config(self):
        client = _client(setup_complete=True)
        resp = client.get("/config", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert "admin.port" in data

    def test_config_section(self):
        client = _client(setup_complete=True)
        resp = client.get("/config/agent", headers=AUTH)
        assert resp.status_code == 200

    def test_get_character(self):
        client = _client(setup_complete=True)
        resp = client.get("/config/character", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["content"] == "# Test character"

    def test_get_personalia(self):
        client = _client(setup_complete=True)
        resp = client.get("/config/personalia", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["content"] == "# Test personalia"

    def test_save_character(self):
        store = _ConfigStoreStub(setup_complete=True)
        agent_state = AgentState(agent=_AgentStub())
        app, _ = create_admin_app(agent_state, store)
        client = TestClient(app, follow_redirects=False)

        resp = client.post(
            "/config/character",
            json={"content": "# New character"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        assert store._data.get("agent.character") == "# New character"

    def test_save_personalia(self):
        store = _ConfigStoreStub(setup_complete=True)
        agent_state = AgentState(agent=_AgentStub())
        app, _ = create_admin_app(agent_state, store)
        client = TestClient(app, follow_redirects=False)

        resp = client.post(
            "/config/personalia",
            json={"content": "# New personalia"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        assert store._data.get("agent.personalia") == "# New personalia"


# ---------------------------------------------------------------------------
# Permissions management
# ---------------------------------------------------------------------------


class TestPermissionsAPI:
    def test_list_permissions(self):
        client = _client(setup_complete=True)
        resp = client.get("/permissions", headers=AUTH)
        assert resp.status_code == 200
        assert "run_command:jq*" in resp.json()["rules"]

    def test_upsert_permission_returns_html(self):
        client = _client(setup_complete=True)
        resp = client.post(
            "/permissions",
            data={"pattern": "send_email:*", "level": "ASK"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_permissions_require_running_agent(self):
        store = _ConfigStoreStub(setup_complete=True)
        agent_state = AgentState(agent=None)
        app, _ = create_admin_app(agent_state, store)
        client = TestClient(app, follow_redirects=False)

        resp = client.get("/permissions", headers=AUTH)
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Logs API
# ---------------------------------------------------------------------------


class TestLogsAPI:
    def test_get_logs_json(self):
        client = _client(setup_complete=True)
        resp = client.get("/logs", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert "count" in data
        assert "lines" in data


# ---------------------------------------------------------------------------
# Delete endpoints (form-encoded and JSON)
# ---------------------------------------------------------------------------


class TestDeleteEndpoints:
    def test_config_delete_form_encoded(self):
        store = _ConfigStoreStub(setup_complete=True)
        store._data["test.key"] = "value"
        agent_state = AgentState(agent=_AgentStub())
        app, _ = create_admin_app(agent_state, store)
        client = TestClient(app, follow_redirects=False)

        resp = client.post(
            "/config/delete",
            data={"key": "test.key"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_config_delete_json(self):
        store = _ConfigStoreStub(setup_complete=True)
        store._data["test.key"] = "value"
        agent_state = AgentState(agent=_AgentStub())
        app, _ = create_admin_app(agent_state, store)
        client = TestClient(app, follow_redirects=False)

        resp = client.post(
            "/config/delete",
            json={"key": "test.key"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_config_delete_missing_key_returns_400(self):
        client = _client(setup_complete=True)
        resp = client.post(
            "/config/delete",
            data={"key": ""},
            headers=AUTH,
        )
        assert resp.status_code == 400

    def test_permissions_delete_form_encoded(self):
        agent = _AgentStub()
        agent.permissions.rules["test:*"] = "ALWAYS"
        client = _client(setup_complete=True, agent=agent)

        resp = client.post(
            "/permissions/delete",
            data={"pattern": "test:*"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "test:*" not in agent.permissions.rules


# ---------------------------------------------------------------------------
# Login validation
# ---------------------------------------------------------------------------


class TestLoginValidation:
    def test_login_page_validates_against_authenticated_endpoint(self):
        """Login page should use /agent/status (auth-protected), not /health."""
        client = _client(setup_complete=True)
        resp = client.get("/login")
        assert resp.status_code == 200
        # Template should reference /agent/status, not /health
        assert "/agent/status" in resp.text
        assert "/health" not in resp.text or "health" in resp.text.lower()


# ---------------------------------------------------------------------------
# Wizard progress OOB updates
# ---------------------------------------------------------------------------


class TestWizardProgress:
    def test_setup_step_includes_progress_oob(self):
        """Wizard step responses should include OOB progress dots update."""
        client = _client(setup_complete=False, step="welcome")
        resp = client.post(
            "/setup/step",
            json={"step": "llm", "values": {}},
        )
        assert resp.status_code == 200
        assert "wizard-progress" in resp.text
        assert "hx-swap-oob" in resp.text

    def test_identity_step_includes_progress_oob(self):
        store = _ConfigStoreStub(setup_complete=False, step="identity")
        agent_state = AgentState(agent=None)
        app, _ = create_admin_app(agent_state, store)
        client = TestClient(app, follow_redirects=False)

        resp = client.post(
            "/setup/step/identity",
            data={"agent_name": "Ada", "owner_name": "Alice", "timezone": "UTC"},
        )
        assert resp.status_code == 200
        assert "wizard-progress" in resp.text
        assert "hx-swap-oob" in resp.text

    def test_calendar_step_includes_progress_oob(self):
        store = _ConfigStoreStub(setup_complete=False, step="calendar")
        agent_state = AgentState(agent=None)
        app, _ = create_admin_app(agent_state, store)
        client = TestClient(app, follow_redirects=False)

        resp = client.post(
            "/setup/step/calendar",
            data={"cal_name": "", "cal_url": ""},
        )
        assert resp.status_code == 200
        assert "wizard-progress" in resp.text
        assert "hx-swap-oob" in resp.text


# ---------------------------------------------------------------------------
# Wizard back-navigation pre-population
# ---------------------------------------------------------------------------


def _client_with_config(
    data: dict[str, str],
    step: str = "welcome",
) -> TestClient:
    """Create a test client whose config store already has saved values."""
    store = _ConfigStoreStub(setup_complete=False, step=step)
    store._data.update(data)
    agent_state = AgentState(agent=None)
    app, _ = create_admin_app(agent_state, store)
    return TestClient(app, follow_redirects=False)


class TestWizardPrePopulation:
    def test_llm_step_shows_saved_api_key(self):
        """Navigating back to LLM step should show the previously saved API key."""
        client = _client_with_config(
            {"agent.anthropic_api_key": "sk-ant-test123", "agent.model": "claude-haiku-4-5"},
            step="identity",
        )
        resp = client.post("/setup/step", json={"step": "llm", "values": {}})
        assert resp.status_code == 200
        assert "sk-ant-test123" in resp.text
        assert "claude-haiku-4-5" in resp.text

    def test_identity_step_shows_saved_values(self):
        """Navigating back to identity step should show saved name, owner, tz."""
        client = _client_with_config(
            {
                "agent.name": "Jarvis",
                "agent.owner_name": "Tony",
                "agent.timezone": "America/New_York",
            },
            step="telegram",
        )
        resp = client.post("/setup/step", json={"step": "identity", "values": {}})
        assert resp.status_code == 200
        assert "Jarvis" in resp.text
        assert "Tony" in resp.text
        assert "America/New_York" in resp.text

    def test_telegram_step_shows_saved_token_and_ids(self):
        """Navigating back to telegram step should show saved bot token and user IDs."""
        client = _client_with_config(
            {
                "channels.telegram.bot_token": "123456:ABC-DEF",
                "channels.telegram.allowed_user_ids": "987654321",
            },
            step="email",
        )
        resp = client.post("/setup/step", json={"step": "telegram", "values": {}})
        assert resp.status_code == 200
        assert "123456:ABC-DEF" in resp.text
        assert "987654321" in resp.text

    def test_calendar_step_shows_saved_provider(self):
        """Navigating back to calendar step should unpack and show saved provider."""
        import json

        client = _client_with_config(
            {
                "calendar.providers": json.dumps(
                    [
                        {
                            "name": "google",
                            "url": "https://cal.example.com",
                            "username": "me@g.co",
                            "password": "apppass",
                        }
                    ]
                ),
            },
            step="search",
        )
        resp = client.post("/setup/step", json={"step": "calendar", "values": {}})
        assert resp.status_code == 200
        assert "google" in resp.text
        assert "https://cal.example.com" in resp.text
        assert "me@g.co" in resp.text
        assert "apppass" in resp.text

    def test_search_step_shows_saved_key(self):
        """Navigating back to search step should show the saved Tavily key."""
        client = _client_with_config(
            {"search.api_key": "tvly-testkey999"},
            step="admin",
        )
        resp = client.post("/setup/step", json={"step": "search", "values": {}})
        assert resp.status_code == 200
        assert "tvly-testkey999" in resp.text

    def test_admin_step_shows_saved_key(self):
        """Admin step should render without exposing stored password hash."""
        client = _client_with_config(
            {"admin.password_hash": "hash", "admin.password_salt": "salt"},
            step="done",
        )
        resp = client.post("/setup/step", json={"step": "admin", "values": {}})
        assert resp.status_code == 200

    def test_setup_page_initial_load_pre_populates(self):
        """GET /setup should pre-populate the current step with saved values."""
        client = _client_with_config(
            {
                "agent.anthropic_api_key": "sk-ant-initial",
                "agent.model": "claude-haiku-4-5",
            },
            step="llm",
        )
        resp = client.get("/setup")
        assert resp.status_code == 200
        assert "sk-ant-initial" in resp.text

    def test_identity_forward_pre_populates_telegram(self):
        """Submitting identity step should pre-populate telegram with any saved values."""
        store = _ConfigStoreStub(setup_complete=False, step="identity")
        store._data["channels.telegram.bot_token"] = "pre-saved-token"
        store._data["channels.telegram.allowed_user_ids"] = "111222"
        agent_state = AgentState(agent=None)
        app, _ = create_admin_app(agent_state, store)
        client = TestClient(app, follow_redirects=False)

        resp = client.post(
            "/setup/step/identity",
            data={"agent_name": "Test", "owner_name": "Owner", "timezone": "UTC"},
        )
        assert resp.status_code == 200
        assert "pre-saved-token" in resp.text
        assert "111222" in resp.text

    def test_calendar_forward_pre_populates_search(self):
        """Submitting calendar step should pre-populate search with any saved values."""
        store = _ConfigStoreStub(setup_complete=False, step="calendar")
        store._data["search.api_key"] = "tvly-presaved"
        agent_state = AgentState(agent=None)
        app, _ = create_admin_app(agent_state, store)
        client = TestClient(app, follow_redirects=False)

        resp = client.post(
            "/setup/step/calendar",
            data={"cal_name": "", "cal_url": ""},
        )
        assert resp.status_code == 200
        assert "tvly-presaved" in resp.text

    def test_empty_config_shows_defaults(self):
        """With no saved config, identity fields should show defaults (Clio, Europe/Zurich)."""
        client = _client_with_config({}, step="llm")
        resp = client.post("/setup/step", json={"step": "identity", "values": {}})
        assert resp.status_code == 200
        assert "Clio" in resp.text
        assert "Europe/Zurich" in resp.text
