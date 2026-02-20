"""Tool executor — runs CLI commands via subprocess with a prefix whitelist."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from core.email_config import himalaya_env


class ToolExecutor:
    """Executes CLI commands on behalf of the LLM."""

    ALLOWED_PREFIXES = [
        "curl",
        "himalaya",
        "jq",
        "wacli",
        "python3 /app/tools/contacts.py",
        "sqlite3",
        "python3 /app/tools/",
        "gh",
        "git",
        "w3m",
        "pandoc",
        "pdftotext",
        "rg",
        "yt-dlp",
        "cal",
    ]

    def _resolve_command(self, command: str) -> str:
        """Rewrite /app/tools paths for local dev when needed."""
        if "/app/tools/" not in command:
            return command
        if Path("/app/tools").exists():
            return command
        local_tools_dir = Path(__file__).resolve().parents[1] / "tools"
        if not local_tools_dir.exists():
            return command
        return command.replace("/app/tools/", f"{local_tools_dir}/")

    async def run_command(self, command: str, timeout: int = 30) -> dict:
        """Execute a shell command and return its output."""
        # Security: validate against whitelist
        if not any(command.startswith(p) for p in self.ALLOWED_PREFIXES):
            return {
                "error": f"Command not allowed. Must start with one of: {self.ALLOWED_PREFIXES}"
            }
        return await self._exec(self._resolve_command(command), timeout)

    async def run_command_trusted(self, command: str, timeout: int = 30) -> dict:
        """Execute a shell command without prefix validation.

        Only use this for commands constructed internally by the agent code,
        never for commands originating from LLM tool calls.
        """
        return await self._exec(self._resolve_command(command), timeout)

    async def _exec(self, command: str, timeout: int) -> dict:
        """Run a shell command and capture output."""
        env = None
        if "himalaya" in command:
            env = os.environ.copy()
            env.update(himalaya_env())
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return {
                "stdout": stdout.decode(),
                "stderr": stderr.decode(),
                "exit_code": proc.returncode,
            }
        except TimeoutError:
            proc.kill()
            return {"error": f"Command timed out after {timeout}s"}

    def parse_json_output(self, output: str) -> list | dict:
        """Parse JSON output from CLI tools."""
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            return {"raw": output}
