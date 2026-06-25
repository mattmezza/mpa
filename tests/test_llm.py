"""Tests for thinking-level plumbing in LLMClient."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.config import AgentConfig
from core.llm import LLMClient


def test_thinking_level_normalized_and_defaulted() -> None:
    assert LLMClient("anthropic", "x", thinking_level=" HIGH ").thinking_level == "high"
    assert LLMClient("anthropic", "x").thinking_level == ""


def test_from_agent_config_carries_thinking_level() -> None:
    cfg = AgentConfig(llm_provider="openai", openai_api_key="x", thinking_level="low")
    assert LLMClient.from_agent_config(cfg).thinking_level == "low"


@pytest.mark.asyncio
async def test_anthropic_generate_sends_effort_when_set() -> None:
    client = LLMClient("anthropic", "x", thinking_level="medium")
    create = AsyncMock(return_value=type("R", (), {"content": [], "usage": None})())
    client._client = type("C", (), {"messages": type("M", (), {"create": create})()})()

    await client.generate(model="claude-4-6-opus", system="s", messages=[], tools=[])

    kwargs = create.await_args.kwargs
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert kwargs["output_config"] == {"effort": "medium"}


@pytest.mark.asyncio
async def test_anthropic_generate_omits_effort_when_off() -> None:
    client = LLMClient("anthropic", "x")
    create = AsyncMock(return_value=type("R", (), {"content": [], "usage": None})())
    client._client = type("C", (), {"messages": type("M", (), {"create": create})()})()

    await client.generate(model="claude-4-6-opus", system="s", messages=[], tools=[])

    kwargs = create.await_args.kwargs
    assert "thinking" not in kwargs
    assert "output_config" not in kwargs
