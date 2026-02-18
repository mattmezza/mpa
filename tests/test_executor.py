"""Tests for the ToolExecutor."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.executor import ToolExecutor


@pytest.mark.asyncio
async def test_run_command_rejects_non_whitelisted_prefix(monkeypatch) -> None:
    executor = ToolExecutor()
    mock_exec = AsyncMock(return_value={"stdout": "", "stderr": "", "exit_code": 0})
    monkeypatch.setattr(executor, "_exec", mock_exec)

    result = await executor.run_command("rm -rf /tmp")

    assert "error" in result
    mock_exec.assert_not_called()


@pytest.mark.asyncio
async def test_run_command_allows_whitelisted_prefix(monkeypatch) -> None:
    executor = ToolExecutor()
    mock_exec = AsyncMock(return_value={"stdout": "ok", "stderr": "", "exit_code": 0})
    monkeypatch.setattr(executor, "_exec", mock_exec)

    result = await executor.run_command("jq --version")

    assert result["stdout"] == "ok"
    mock_exec.assert_awaited_once()


def test_parse_json_output_handles_invalid_json() -> None:
    executor = ToolExecutor()
    output = executor.parse_json_output("not json")

    assert output == {"raw": "not json"}


@pytest.mark.asyncio
async def test_himalaya_command_sets_env(monkeypatch) -> None:
    executor = ToolExecutor()
    created = {}

    async def _fake_subprocess_shell(command, stdout, stderr, env):
        created["env"] = env

        class _Proc:
            returncode = 0

            async def communicate(self):
                return b"", b""

        return _Proc()

    monkeypatch.setattr("core.executor.asyncio.create_subprocess_shell", _fake_subprocess_shell)
    monkeypatch.setattr("core.executor.himalaya_env", lambda: {"HIMALAYA_CONFIG": "/tmp/x"})

    await executor.run_command("himalaya envelope list")

    assert created["env"]["HIMALAYA_CONFIG"] == "/tmp/x"
