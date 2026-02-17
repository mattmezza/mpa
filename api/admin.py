"""Admin API — lightweight FastAPI app for health checks and future management endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI

if TYPE_CHECKING:
    from core.agent import AgentCore


def create_admin_app(agent: AgentCore) -> FastAPI:
    app = FastAPI(title="Personal Agent Admin", version="0.1.0")

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    return app
