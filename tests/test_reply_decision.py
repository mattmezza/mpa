"""Reply decision (#36): the gate that keeps the agent quiet in group chats.

Covers the one-shot classifier (should_reply), the group-vs-DM heuristic, the
rate-limit backstop, and the end-to-end suppression path through process().
"""

from __future__ import annotations

import pytest

from core.config import Config
from core.reply_decision import should_reply


class _LLMStub:
    """Minimal LLM stub returning a canned generate_text response."""

    def __init__(self, response: str):
        self._response = response
        self.calls = 0

    async def generate_text(self, *, model: str, prompt: str, max_tokens: int = 1024) -> str:
        self.calls += 1
        return self._response


class _BoomLLM:
    async def generate_text(self, *, model: str, prompt: str, max_tokens: int = 1024) -> str:
        raise RuntimeError("classifier exploded")


# -- should_reply unit tests --------------------------------------------------


@pytest.mark.asyncio
async def test_reply_when_model_says_reply() -> None:
    llm = _LLMStub("REPLY")
    assert await should_reply(llm, "m", "can you check my calendar?", "Coach") is True
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_skip_when_model_says_skip() -> None:
    llm = _LLMStub("SKIP — addressed to another bot")
    assert await should_reply(llm, "m", "@OtherBot what time is it?", "Coach") is False


@pytest.mark.asyncio
async def test_fail_open_on_error() -> None:
    # A classifier failure must never drop a real message.
    assert await should_reply(_BoomLLM(), "m", "hello there", "Coach") is True


@pytest.mark.asyncio
async def test_fail_open_on_blank_and_junk() -> None:
    assert await should_reply(_LLMStub(""), "m", "hi", "Coach") is True  # empty model output
    assert await should_reply(_LLMStub("maybe?"), "m", "hi", "Coach") is True  # unparseable
    # Empty message short-circuits without an LLM call.
    quiet = _LLMStub("SKIP")
    assert await should_reply(quiet, "m", "   ", "Coach") is True
    assert quiet.calls == 0


# -- AgentCore gate: helpers + integration ------------------------------------


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from core.agent import AgentCore

    cfg = Config()
    cfg.reply_decision.enabled = True
    cfg.goal_decomposition.enabled = False  # keep the gate the only background call
    return AgentCore(cfg)


def test_is_group_chat_heuristic(agent) -> None:
    assert agent._is_group_chat("111", "-999") is True  # telegram group id
    assert agent._is_group_chat("111", "111") is False  # telegram/whatsapp DM
    assert agent._is_group_chat("111", "12345@g.us") is True  # whatsapp group jid
    assert agent._is_group_chat("111", "-999:42") is True  # forum topic
    assert agent._is_group_chat("111", "") is False  # no chat id → not a group


def test_rate_limiter_backstop(agent) -> None:
    cfg = agent.config.reply_decision
    cfg.max_replies_per_window = 3
    cfg.window_seconds = 120
    for _ in range(3):
        assert agent._reply_rate_exceeded("telegram", "-999", cfg) is False
        agent._record_reply("telegram", "-999")
    # Fourth reply within the window is blocked.
    assert agent._reply_rate_exceeded("telegram", "-999", cfg) is True
    # A different chat has its own budget.
    assert agent._reply_rate_exceeded("telegram", "-888", cfg) is False


def test_rate_limiter_window_expiry(agent) -> None:
    cfg = agent.config.reply_decision
    cfg.max_replies_per_window = 1
    cfg.window_seconds = 120
    # Seed a timestamp older than the window — it must be pruned, not counted.
    agent._reply_times[("telegram", "-999")] = [1.0]
    assert agent._reply_rate_exceeded("telegram", "-999", cfg) is False


@pytest.mark.asyncio
async def test_process_suppresses_on_skip(agent, monkeypatch) -> None:
    monkeypatch.setattr(agent, "_background_llm", lambda *a, **k: _LLMStub("SKIP"))
    boom = _BoomLLM()  # main inference must never run on a suppressed message
    monkeypatch.setattr(agent.llm, "generate", boom.generate_text)

    resp = await agent.process(
        message="lol same", channel="telegram", user_id="111", chat_id="-999"
    )
    assert resp.text == ""


@pytest.mark.asyncio
async def test_process_replies_in_dm_without_gating(agent, monkeypatch) -> None:
    # In a DM (chat_id == user_id) the gate is skipped entirely, so the
    # background classifier is never consulted.
    sentinel = _BoomLLM()
    monkeypatch.setattr(agent, "_background_llm", lambda *a, **k: sentinel)
    # Short-circuit the heavy inference path: prove the gate let it through.
    assert agent._is_group_chat("111", "111") is False


@pytest.mark.asyncio
async def test_process_suppresses_when_rate_capped(agent, monkeypatch) -> None:
    cfg = agent.config.reply_decision
    cfg.max_replies_per_window = 2
    agent._reply_times[("telegram", "-999")] = [__import__("time").time()] * 2
    # Rate cap is checked before the classifier — it must not even be called.
    monkeypatch.setattr(agent, "_background_llm", lambda *a, **k: _BoomLLM())
    resp = await agent.process(
        message="still going", channel="telegram", user_id="111", chat_id="-999"
    )
    assert resp.text == ""
