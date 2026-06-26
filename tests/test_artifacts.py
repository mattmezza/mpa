"""Tests for agent-crafted web artifacts (core/artifacts.py + /artifacts route)."""

from __future__ import annotations

import os
import time
from types import SimpleNamespace
from typing import cast

from fastapi.testclient import TestClient

from api.admin import AgentState, create_admin_app
from core.artifacts import ArtifactStore
from core.config_store import ConfigStore

# -- ArtifactStore unit tests -------------------------------------------------


def test_write_and_resolve_round_trip(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    art_id = store.write("<p>hello</p>", title="greeting")
    path = store.path_for(art_id)
    assert path is not None
    assert path.read_text(encoding="utf-8") == "<p>hello</p>"


def test_path_for_rejects_traversal_and_malformed(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    for bad in ["../etc/passwd", "a/b", "a.b", "", "x" * 65, "foo bar", "..", "a%2fb"]:
        assert store.path_for(bad) is None, bad
    # A well-formed id resolves (file need not exist yet).
    assert store.path_for("AbC-123_xy") is not None


def test_cleanup_removes_expired_keeps_fresh(tmp_path) -> None:
    store = ArtifactStore(tmp_path, ttl_hours=1)
    old_id = store.write("<p>old</p>")
    old_path = store.path_for(old_id)
    assert old_path is not None
    backdated = time.time() - 2 * 3600  # 2h ago, past the 1h TTL
    os.utime(old_path, (backdated, backdated))
    fresh_id = store.write("<p>new</p>")

    assert store.cleanup() == 1
    assert not old_path.exists()
    fresh_path = store.path_for(fresh_id)
    assert fresh_path is not None and fresh_path.exists()


def test_cleanup_ttl_zero_is_noop(tmp_path) -> None:
    store = ArtifactStore(tmp_path, ttl_hours=0)
    art_id = store.write("<p>x</p>")
    path = store.path_for(art_id)
    assert path is not None
    os.utime(path, (0, 0))  # ancient mtime
    assert store.cleanup() == 0
    assert path.exists()


# -- /artifacts route tests ---------------------------------------------------


class _Store:
    """Minimal ConfigStore stub exposing only what the artifacts route reads."""

    def __init__(self, directory, *, enabled: bool = True, ttl_hours: int = 168):
        self._vals = {
            "artifacts.directory": str(directory),
            "artifacts.enabled": "true" if enabled else "false",
            "artifacts.ttl_hours": str(ttl_hours),
        }

    async def get_many(self, prefix: str = "") -> dict:
        return {k: v for k, v in self._vals.items() if k.startswith(prefix)}

    async def is_setup_complete(self) -> bool:
        return True


def _client(store: _Store) -> TestClient:
    app, _auth = create_admin_app(AgentState(agent=None), cast(ConfigStore, store))
    return TestClient(app)


def test_serve_artifact_round_trip(tmp_path) -> None:
    art_id = ArtifactStore(tmp_path).write("<h1>dashboard</h1>")
    resp = _client(_Store(tmp_path)).get(f"/artifacts/{art_id}")
    assert resp.status_code == 200
    assert "<h1>dashboard</h1>" in resp.text
    # CSP sandbox keeps artifact JS off the admin origin's localStorage.
    assert "sandbox" in resp.headers["content-security-policy"]


def test_serve_missing_artifact_404(tmp_path) -> None:
    resp = _client(_Store(tmp_path)).get("/artifacts/doesnotexist123")
    assert resp.status_code == 404


def test_serve_when_disabled_404(tmp_path) -> None:
    art_id = ArtifactStore(tmp_path).write("<h1>x</h1>")
    resp = _client(_Store(tmp_path, enabled=False)).get(f"/artifacts/{art_id}")
    assert resp.status_code == 404


def test_serve_route_is_public(tmp_path) -> None:
    # No Authorization header — artifacts are gated by the unguessable id, not auth.
    art_id = ArtifactStore(tmp_path).write("<h1>x</h1>")
    resp = _client(_Store(tmp_path)).get(f"/artifacts/{art_id}")
    assert resp.status_code == 200


# -- write_artifact tool handler ----------------------------------------------


def test_tool_write_artifact(tmp_path) -> None:
    from core.agent import AgentCore
    from core.config import ArtifactsConfig

    fake = SimpleNamespace(
        config=SimpleNamespace(artifacts=ArtifactsConfig(directory=str(tmp_path))),
        _base_url=lambda: "http://host:8000",
    )

    res = AgentCore._tool_write_artifact(fake, {"html": "<h1>hi</h1>", "title": "t"})
    assert res["ok"] is True
    assert res["url"].startswith("http://host:8000/artifacts/")
    art_id = res["url"].rsplit("/", 1)[1]
    assert (tmp_path / f"{art_id}.html").read_text(encoding="utf-8") == "<h1>hi</h1>"

    # Empty content is rejected.
    assert "error" in AgentCore._tool_write_artifact(fake, {"html": "   "})

    # Disabled feature is rejected.
    fake.config.artifacts.enabled = False
    assert "error" in AgentCore._tool_write_artifact(fake, {"html": "<p>x</p>"})
