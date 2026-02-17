"""Agent core — LLM call with agentic tool-use loop."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from anthropic import AsyncAnthropic

from core.config import Config
from core.executor import ToolExecutor
from core.models import AgentResponse
from core.skills import SkillsEngine

log = logging.getLogger(__name__)

# -- Tool definitions the LLM can call --

TOOLS = [
    {
        "name": "run_command",
        "description": (
            "Execute a CLI command. Use skill documentation to construct correct syntax. "
            "Returns stdout, stderr, and exit_code."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The full CLI command to run"},
                "purpose": {
                    "type": "string",
                    "description": "Brief explanation of what this command does",
                },
            },
            "required": ["command", "purpose"],
        },
    },
]


class AgentCore:
    def __init__(self, config: Config):
        self.config = config
        self.llm = AsyncAnthropic(api_key=config.agent.anthropic_api_key)
        self.skills = SkillsEngine(config.agent.skills_dir)
        self.executor = ToolExecutor()
        self.channels: dict = {}

    async def process(self, message: str, channel: str, user_id: str) -> AgentResponse:
        """Process an incoming message through the LLM with tool-use loop."""
        system = self._build_system_prompt()
        messages = [{"role": "user", "content": message}]

        log.info("Processing message from %s/%s: %s", channel, user_id, message[:100])

        # Initial LLM call
        response = await self.llm.messages.create(
            model=self.config.agent.model,
            max_tokens=4096,
            system=system,
            messages=messages,
            tools=TOOLS,
        )

        # Agentic loop — keep going while the LLM wants to call tools
        while response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = await self._execute_tool(block)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result),
                        }
                    )

            # Feed tool results back to the LLM
            messages = [
                {"role": "user", "content": message},
                {"role": "assistant", "content": response.content},
                {"role": "user", "content": tool_results},
            ]
            response = await self.llm.messages.create(
                model=self.config.agent.model,
                max_tokens=4096,
                system=system,
                messages=messages,
                tools=TOOLS,
            )

        final_text = self._extract_text(response)
        log.info("Response: %s", final_text[:200])
        return AgentResponse(text=final_text)

    async def _execute_tool(self, tool_call) -> dict:
        """Dispatch a tool call from the LLM."""
        name = tool_call.name
        params = tool_call.input

        if name == "run_command":
            log.info("Tool call: run_command — %s", params.get("purpose", ""))
            return await self.executor.run_command(params["command"])

        return {"error": f"Unknown tool: {name}"}

    def _build_system_prompt(self) -> str:
        cfg = self.config.agent
        skills_block = self.skills.get_all_skills()
        character = self._load_file(cfg.character_file)
        personalia = self._load_file(cfg.personalia_file)

        prompt = f"""You are {cfg.name}, a personal AI assistant for {cfg.owner_name}.

Today is {datetime.now().strftime("%A, %B %d, %Y")}. Timezone: {cfg.timezone}.

<personalia>
{personalia}
</personalia>

<character>
{character}
</character>

When you need to perform an action, use the `run_command` tool to execute CLI commands.
Always use the skill documentation to construct the correct command.
Parse JSON output when available (himalaya supports -o json, sqlite3 supports -json).
If a command fails, read the error and try to fix it.
Never guess at command syntax — always refer to the skill file."""

        if skills_block:
            prompt += f"""

<available_skills>
{skills_block}
</available_skills>"""

        return prompt

    def _load_file(self, filename: str) -> str:
        """Load a top-level markdown file (character.md or personalia.md)."""
        path = Path(filename)
        return path.read_text() if path.exists() else ""

    def _extract_text(self, response) -> str:
        """Pull the text content out of the LLM response."""
        parts = []
        for block in response.content:
            if block.type == "text":
                parts.append(block.text)
        return "\n".join(parts) if parts else ""
