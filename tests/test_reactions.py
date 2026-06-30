"""Telegram emoji reactions (#70): channel react() + the set_reaction tool."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.error import BadRequest

from channels.telegram import REACTION_EMOJI, TelegramChannel
from core.agent import TOOLS
from core.config import Config
from core.llm import LLMToolCall


def _channel_with_mock_bot() -> TelegramChannel:
    # Skip __init__ (it builds a real Application): react() only touches
    # self.app.bot.set_message_reaction and the static _route helper.
    ch = object.__new__(TelegramChannel)
    ch.app = SimpleNamespace(bot=AsyncMock())
    return ch


@pytest.mark.asyncio
async def test_react_maps_name_and_splits_folded_topic_id() -> None:
    ch = _channel_with_mock_bot()
    # A folded "<chat>:<thread>" id resolves to the base chat — a reaction targets
    # a message, not a thread.
    await ch.react("123:456", 99, "thumbsup")
    _, kwargs = ch.app.bot.set_message_reaction.call_args
    assert kwargs["chat_id"] == 123
    assert kwargs["message_id"] == 99
    assert [r.emoji for r in kwargs["reaction"]] == [REACTION_EMOJI["thumbsup"]]


@pytest.mark.asyncio
async def test_react_swallows_badrequest_on_old_message() -> None:
    # Telegram rejects reactions on >24h-old / deleted messages; a cosmetic ack
    # must never fail the turn (#70 edge case).
    ch = _channel_with_mock_bot()
    ch.app.bot.set_message_reaction.side_effect = BadRequest("MESSAGE_ID_INVALID")
    await ch.react(5, 1, "heart")  # must not raise


@pytest.mark.asyncio
async def test_react_unknown_emoji_raises() -> None:
    ch = _channel_with_mock_bot()
    with pytest.raises(ValueError):
        await ch.react(5, 1, "not-an-emoji")
    ch.app.bot.set_message_reaction.assert_not_called()


def test_every_enum_name_maps_to_an_emoji() -> None:
    # The tool's enum and the channel map must not drift apart: every advertised
    # name must resolve, or the model could pick a name that silently no-ops.
    tool = next(t for t in TOOLS if t["name"] == "set_reaction")
    for name in tool["input_schema"]["properties"]["emoji"]["enum"]:
        assert name in REACTION_EMOJI


# --- set_reaction tool ------------------------------------------------------


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from core.agent import AgentCore

    cfg = Config()
    cfg.agent.llm_provider = "deepseek"
    cfg.agent.model = "deepseek-v4-flash"
    cfg.memory.embedding.enabled = False
    return AgentCore(cfg)


def _state(agent, **origin):
    base = {"channel": "telegram", "user_id": "u", "chat_id": "55", "message_id": 77}
    base.update(origin)
    return agent._new_request_state(None, origin=base)


@pytest.mark.asyncio
async def test_set_reaction_defaults_to_triggering_message(agent) -> None:
    agent.channels["telegram"] = SimpleNamespace(react=AsyncMock())
    call = LLMToolCall(id="x", name="set_reaction", arguments={"emoji": "heart"})
    result = await agent._execute_tool(call, "telegram", "u", _state(agent))
    assert result == {"ok": True, "emoji": "heart"}
    agent.channels["telegram"].react.assert_awaited_once_with("55", 77, "heart")


@pytest.mark.asyncio
async def test_set_reaction_errors_when_no_message_in_context(agent) -> None:
    agent.channels["telegram"] = SimpleNamespace(react=AsyncMock())
    call = LLMToolCall(id="x", name="set_reaction", arguments={"emoji": "heart"})
    result = await agent._execute_tool(call, "telegram", "u", _state(agent, message_id=None))
    assert "error" in result
    agent.channels["telegram"].react.assert_not_called()


@pytest.mark.asyncio
async def test_set_reaction_errors_on_channel_without_support(agent) -> None:
    agent.channels["whatsapp"] = SimpleNamespace()  # no react()
    call = LLMToolCall(id="x", name="set_reaction", arguments={"emoji": "heart"})
    result = await agent._execute_tool(call, "whatsapp", "u", _state(agent, channel="whatsapp"))
    assert "does not support reactions" in result["error"]


def test_set_reaction_is_preapproved(tmp_path) -> None:
    from core.permissions import PermissionEngine, PermissionLevel

    eng = PermissionEngine(db_path=str(tmp_path / "perms.db"))
    assert eng.check("set_reaction", {"emoji": "heart"}) == PermissionLevel.ALWAYS
