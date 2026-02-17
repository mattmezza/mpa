"""Tests for main lifecycle routes on the admin app."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

import core.main as main


class _ConfigStoreStub:
    async def is_setup_complete(self) -> bool:
        return True

    async def get(self, key: str):
        return "secret" if key == "admin.api_key" else None


def _client(agent_state: main.AgentState) -> TestClient:
    app = main.create_admin_app(agent_state, _ConfigStoreStub())
    main._attach_lifecycle_routes(app, _ConfigStoreStub(), agent_state)
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
