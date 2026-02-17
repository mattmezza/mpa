"""Tests for scheduler helpers and job registration."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.scheduler import (
    _parse_cron,
    run_agent_task,
    run_memory_consolidation,
    run_system_command,
    set_agent_context,
)


def test_parse_cron_valid_expression() -> None:
    parsed = _parse_cron("0 7 * * *")
    assert parsed == {"minute": "0", "hour": "7"}


def test_parse_cron_invalid_expression() -> None:
    with pytest.raises(ValueError):
        _parse_cron("* * *")


@pytest.mark.asyncio
async def test_run_system_command_logs_nonzero_exit(monkeypatch) -> None:
    agent = SimpleNamespace(executor=SimpleNamespace(run_command_trusted=AsyncMock()))
    agent.executor.run_command_trusted.return_value = {
        "stdout": "",
        "stderr": "boom",
        "exit_code": 1,
    }
    set_agent_context(agent)

    await run_system_command("echo fail")

    agent.executor.run_command_trusted.assert_awaited_once_with("echo fail")


@pytest.mark.asyncio
async def test_run_memory_consolidation_calls_store(monkeypatch) -> None:
    memory = SimpleNamespace(consolidate_and_cleanup=AsyncMock(return_value={}))
    agent = SimpleNamespace(
        memory=memory,
        llm=object(),
        config=SimpleNamespace(memory=SimpleNamespace(consolidation_model="model")),
    )
    set_agent_context(agent)

    await run_memory_consolidation()

    memory.consolidate_and_cleanup.assert_awaited_once_with(llm=agent.llm, model="model")


@pytest.mark.asyncio
async def test_run_agent_task_sends_to_owner() -> None:
    channel = AsyncMock()
    agent = SimpleNamespace(
        channels={"telegram": channel},
        process=AsyncMock(return_value=SimpleNamespace(text="done")),
        config=SimpleNamespace(
            channels=SimpleNamespace(telegram=SimpleNamespace(allowed_user_ids=[123]))
        ),
    )
    set_agent_context(agent)

    await run_agent_task("do thing", channel="telegram")

    agent.process.assert_awaited_once()
    channel.send.assert_awaited_once_with(123, "done")
