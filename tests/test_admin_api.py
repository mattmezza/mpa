"""Admin API smoke tests for core endpoints."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from fastapi.testclient import TestClient

from api.admin import AgentState, create_admin_app
from core.config_store import ConfigStore


class _ConfigStoreStub:
    def __init__(self, setup_complete: bool = True):
        self._setup_complete = setup_complete

    async def is_setup_complete(self) -> bool:
        return self._setup_complete

    async def get(self, key: str):
        if key == "admin.password_hash":
            return "hash"
        if key == "admin.password_salt":
            return "salt"
        return None

    async def get_all_redacted(self) -> dict:
        return {"admin.password_hash": "***", "admin.password_salt": "***"}

    async def verify_admin_password(self, password: str) -> bool:
        return password == "secret"

    async def get_section_redacted(self, section: str) -> dict:
        return {f"{section}.value": "ok"}

    async def set_many(self, values: dict) -> None:
        self._last_set = values

    async def set_admin_password(self, password: str) -> None:
        self._last_set = {"admin.password_hash": "hash", "admin.password_salt": "salt"}

    async def delete(self, key: str) -> bool:
        return key == "test.key"

    async def get_setup_step(self) -> str:
        return "welcome"

    async def set_setup_step(self, step: str) -> None:
        self._step = step


class _AgentStub:
    def __init__(self):
        self.channels = {"telegram": object()}
        self.scheduler = SimpleNamespace(scheduler=SimpleNamespace(get_jobs=lambda: [1, 2]))
        self.permissions = SimpleNamespace(rules={"run_command:jq*": "ALWAYS"})


def _client(setup_complete: bool = True) -> TestClient:
    store = _ConfigStoreStub(setup_complete=setup_complete)
    agent_state = AgentState(agent=None)
    app, _auth = create_admin_app(agent_state, cast(ConfigStore, store))
    return TestClient(app)


def test_health_reports_setup_and_running() -> None:
    store = _ConfigStoreStub(setup_complete=True)
    agent_state = AgentState(agent=cast(Any, _AgentStub()))
    app, _auth = create_admin_app(agent_state, cast(ConfigStore, store))
    client = TestClient(app)
    resp = client.get("/health")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["setup_complete"] is True
    assert data["agent_running"] is True


def test_requires_auth_when_setup_complete() -> None:
    client = _client(setup_complete=True)
    resp = client.get("/config")
    assert resp.status_code == 401


def test_skips_auth_during_setup() -> None:
    client = _client(setup_complete=False)
    resp = client.get("/config")

    assert resp.status_code == 200
    assert resp.json()["admin.password_hash"] == "***"


def test_config_patch_updates_values() -> None:
    client = _client(setup_complete=True)
    headers = {"Authorization": "Bearer secret"}
    resp = client.patch("/config", json={"values": {"agent.name": "Ada"}}, headers=headers)

    assert resp.status_code == 200
    assert resp.json()["updated"] == ["agent.name"]


def test_agent_status_reports_channels_and_jobs() -> None:
    store = _ConfigStoreStub(setup_complete=True)
    agent_state = AgentState(agent=cast(Any, _AgentStub()))
    app, _auth = create_admin_app(agent_state, cast(ConfigStore, store))
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret"}
    resp = client.get("/agent/status", headers=headers)

    assert resp.status_code == 200
    assert resp.json() == {
        "running": True,
        "status": "STOPPED",
        "channels": ["telegram"],
        "scheduler_jobs": 2,
    }
