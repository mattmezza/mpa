"""Tool executor — runs CLI commands via subprocess with a prefix whitelist."""

from __future__ import annotations

import asyncio
import json


class ToolExecutor:
    """Executes CLI commands on behalf of the LLM."""

    ALLOWED_PREFIXES = [
        "curl",
        "himalaya",
        "jq",
        "khard",
        "vdirsyncer",
        "sqlite3",
        "python3 /app/tools/",
    ]

    async def run_command(self, command: str, timeout: int = 30) -> dict:
        """Execute a shell command and return its output."""
        # Security: validate against whitelist
        if not any(command.startswith(p) for p in self.ALLOWED_PREFIXES):
            return {
                "error": f"Command not allowed. Must start with one of: {self.ALLOWED_PREFIXES}"
            }
        return await self._exec(command, timeout)

    async def run_command_trusted(self, command: str, timeout: int = 30) -> dict:
        """Execute a shell command without prefix validation.

        Only use this for commands constructed internally by the agent code,
        never for commands originating from LLM tool calls.
        """
        return await self._exec(command, timeout)

    async def _exec(self, command: str, timeout: int) -> dict:
        """Run a shell command and capture output."""
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
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
