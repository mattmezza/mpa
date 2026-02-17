"""Shared data models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AgentResponse:
    text: str
    voice: bytes | None = None
