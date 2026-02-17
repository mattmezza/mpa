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
        if key == "admin.api_key":
            return "secret"
        if key == "agent.character":
            return "# Test character"
        if key == "agent.personalia":
            return "# Test personalia"
        return self._data.get(key)

    async def get_all_redacted(self) -> dict:
        return {"agent.name": "Clio", "admin.api_key": "se***ret"}

    async def get_section_redacted(self, section: str) -> dict:
        return {f"{section}.value": "ok"}

    async def set(self, key: str, value: str) -> None:
        self._data[key] = value

    async def set_many(self, values: dict) -> None:
        self._last_set = values
        self._data.update(values)

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
        # Should show running state with channel info
        assert "telegram" in resp.text.lower()

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
        assert "agent.name" in data

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
