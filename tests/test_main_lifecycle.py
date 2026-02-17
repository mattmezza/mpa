"""Tests for main lifecycle routes on the admin app."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

import core.main as main


class _ConfigStoreStub:
    async def is_setup_complete(self) -> bool:
        return True

    async def get(self, key: str):
        if key == "admin.password_hash":
            return "hash"
        if key == "admin.password_salt":
            return "salt"
        return None

    async def verify_admin_password(self, password: str) -> bool:
        return password == "secret"


def _client(agent_state: main.AgentState) -> TestClient:
    app, auth = main.create_admin_app(agent_state, _ConfigStoreStub())
    main._attach_lifecycle_routes(app, _ConfigStoreStub(), agent_state, auth)
    return TestClient(app)


def test_agent_start_and_stop(monkeypatch) -> None:
    started_agent = SimpleNamespace(channels={"telegram": object()})
    agent_state = main.AgentState()

    async def _start_agent(_store):
        return started_agent

    async def _stop_agent(_agent):
        return None

    monkeypatch.setattr(main, "_start_agent", _start_agent)
    monkeypatch.setattr(main, "_stop_agent", _stop_agent)

    client = _client(agent_state)
    headers = {"Authorization": "Bearer secret"}

    start_resp = client.post("/agent/start", headers=headers)
    assert start_resp.status_code == 200
    assert start_resp.json() == {"status": "started", "channels": ["telegram"]}

    stop_resp = client.post("/agent/stop", headers=headers)
    assert stop_resp.status_code == 200
    assert stop_resp.json() == {"status": "stopped"}


def test_lifecycle_htmx_returns_html(monkeypatch) -> None:
    """HTMX requests should get HTML snippets instead of JSON."""
    started_agent = SimpleNamespace(channels={"telegram": object()})
    agent_state = main.AgentState()

    async def _start_agent(_store):
        return started_agent

    async def _stop_agent(_agent):
        return None

    monkeypatch.setattr(main, "_start_agent", _start_agent)
    monkeypatch.setattr(main, "_stop_agent", _stop_agent)

    client = _client(agent_state)
    headers = {"Authorization": "Bearer secret", "HX-Request": "true"}

    start_resp = client.post("/agent/start", headers=headers)
    assert start_resp.status_code == 200
    assert "text/html" in start_resp.headers["content-type"]
    assert "Started" in start_resp.text
    assert "alert-success" in start_resp.text
    assert start_resp.headers.get("HX-Trigger") == "refresh-status"

    stop_resp = client.post("/agent/stop", headers=headers)
    assert stop_resp.status_code == 200
    assert "Stopped" in stop_resp.text
    assert "alert-success" in stop_resp.text


def test_restart_agent(monkeypatch) -> None:
    """Test restart returns correct JSON and HTMX responses."""
    started_agent = SimpleNamespace(channels={"telegram": object()})
    agent_state = main.AgentState()

    async def _start_agent(_store):
        return started_agent

    async def _stop_agent(_agent):
        return None

    monkeypatch.setattr(main, "_start_agent", _start_agent)
    monkeypatch.setattr(main, "_stop_agent", _stop_agent)

    client = _client(agent_state)
    headers = {"Authorization": "Bearer secret"}

    # JSON response
    resp = client.post("/agent/restart", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "restarted"
    assert resp.json()["channels"] == ["telegram"]

    # HTMX response
    htmx_headers = {**headers, "HX-Request": "true"}
    resp = client.post("/agent/restart", headers=htmx_headers)
    assert resp.status_code == 200
    assert "Restarted" in resp.text
    assert "alert-success" in resp.text


def test_lifecycle_requires_auth() -> None:
    """Lifecycle endpoints require auth when setup is complete."""
    agent_state = main.AgentState()
    client = _client(agent_state)

    for path in ["/agent/start", "/agent/stop", "/agent/restart"]:
        resp = client.post(path)
        assert resp.status_code == 401, f"{path} should require auth"
