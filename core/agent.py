"""Agent core — LLM call with agentic tool-use loop."""

from __future__ import annotations

import json
import logging
import shlex
from datetime import datetime
from pathlib import Path

from anthropic import AsyncAnthropic

from core.config import Config
from core.executor import ToolExecutor
from core.history import ConversationHistory
from core.memory import MemoryStore
from core.models import AgentResponse
from core.skills import SkillsEngine

log = logging.getLogger(__name__)


def _shell_quote(s: str) -> str:
    """Quote a string for safe shell interpolation."""
    return shlex.quote(s)


# -- Tool definitions the LLM can call --

TOOLS = [
    # Generic CLI executor — the LLM constructs commands using skill knowledge
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
    # Structured tools for write actions (will require permission once the permission engine lands)
    {
        "name": "send_email",
        "description": "Send an email on behalf of the user.",
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {
                    "type": "string",
                    "description": "Email account name (e.g. 'personal', 'work')",
                },
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["account", "to", "subject", "body"],
        },
    },
    {
        "name": "send_message",
        "description": "Send a message to a contact via Telegram or WhatsApp.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel": {
                    "type": "string",
                    "enum": ["telegram", "whatsapp"],
                    "description": "Which messaging channel to use",
                },
                "to": {"type": "string", "description": "Recipient identifier (chat ID or phone)"},
                "text": {"type": "string", "description": "Message text"},
            },
            "required": ["channel", "to", "text"],
        },
    },
    {
        "name": "create_calendar_event",
        "description": "Create a calendar event or send an invite.",
        "input_schema": {
            "type": "object",
            "properties": {
                "calendar": {
                    "type": "string",
                    "description": "Calendar name (e.g. 'google', 'icloud')",
                },
                "summary": {"type": "string", "description": "Event title"},
                "start": {"type": "string", "description": "ISO datetime with timezone"},
                "end": {"type": "string", "description": "ISO datetime with timezone"},
                "attendees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of attendee email addresses",
                },
            },
            "required": ["calendar", "summary", "start", "end"],
        },
    },
    # Read-only / utility tools
    {
        "name": "web_search",
        "description": "Search the web for information.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "schedule_task",
        "description": "Schedule a one-time future task (e.g. a reminder).",
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "What the agent should do when the time comes",
                },
                "run_at": {
                    "type": "string",
                    "description": "ISO datetime when the task should run",
                },
                "channel": {
                    "type": "string",
                    "description": "Channel to deliver the result on (default: telegram)",
                },
            },
            "required": ["task", "run_at"],
        },
    },
]


class AgentCore:
    def __init__(self, config: Config):
        self.config = config
        self.llm = AsyncAnthropic(api_key=config.agent.anthropic_api_key)
        self.skills = SkillsEngine(config.agent.skills_dir)
        self.executor = ToolExecutor()
        self.history = ConversationHistory(
            db_path=config.history.db_path,
            max_turns=config.history.max_turns,
        )
        self.memory = MemoryStore(
            db_path=config.memory.db_path,
            long_term_limit=config.memory.long_term_limit,
        )
        self.channels: dict = {}

    async def process(self, message: str, channel: str, user_id: str) -> AgentResponse:
        """Process an incoming message through the LLM with tool-use loop."""
        system = await self._build_system_prompt()

        # Load conversation history and append the new user message
        history = await self.history.get_messages(channel, user_id)
        messages = [*history, {"role": "user", "content": message}]

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
            messages.extend(
                [
                    {"role": "assistant", "content": response.content},
                    {"role": "user", "content": tool_results},
                ]
            )
            response = await self.llm.messages.create(
                model=self.config.agent.model,
                max_tokens=4096,
                system=system,
                messages=messages,
                tools=TOOLS,
            )

        final_text = self._extract_text(response)
        log.info("Response: %s", final_text[:200])

        # Persist the turn (user message + final assistant text only)
        await self.history.add_turn(channel, user_id, "user", message)
        await self.history.add_turn(channel, user_id, "assistant", final_text)

        return AgentResponse(text=final_text)

    async def _execute_tool(self, tool_call) -> dict:
        """Dispatch a tool call from the LLM."""
        name = tool_call.name
        params = tool_call.input

        if name == "run_command":
            log.info("Tool call: run_command — %s", params.get("purpose", ""))
            return await self.executor.run_command(params["command"])

        if name == "send_email":
            return await self._tool_send_email(params)

        if name == "send_message":
            return await self._tool_send_message(params)

        if name == "create_calendar_event":
            return await self._tool_create_calendar_event(params)

        if name == "web_search":
            log.info("Tool call: web_search — %s", params.get("query", ""))
            return {"error": "web_search is not configured yet."}

        if name == "schedule_task":
            log.info("Tool call: schedule_task — %s", params.get("task", ""))
            return {"error": "Scheduler is not configured yet."}

        return {"error": f"Unknown tool: {name}"}

    # -- Structured tool implementations --

    async def _tool_send_email(self, params: dict) -> dict:
        """Send an email via himalaya CLI."""
        account = params["account"]
        to = params["to"]
        subject = params["subject"]
        body = params["body"]
        log.info("Tool call: send_email — to=%s subject=%s", to, subject)

        # Build MML message and pipe to himalaya
        mml = f"To: {to}\nSubject: {subject}\n\n{body}"
        command = f"echo {_shell_quote(mml)} | himalaya -a {_shell_quote(account)} message send"
        return await self.executor.run_command(command)

    async def _tool_send_message(self, params: dict) -> dict:
        """Send a message via a registered channel."""
        channel_name = params["channel"]
        to = params["to"]
        text = params["text"]
        log.info("Tool call: send_message — channel=%s to=%s", channel_name, to)

        channel = self.channels.get(channel_name)
        if not channel:
            return {"error": f"Channel '{channel_name}' is not enabled."}

        try:
            await channel.send(to, text)
            return {"ok": True, "channel": channel_name, "to": to}
        except Exception as exc:
            return {"error": str(exc)}

    async def _tool_create_calendar_event(self, params: dict) -> dict:
        """Create a calendar event via the CalDAV helper script."""
        calendar = params["calendar"]
        summary = params["summary"]
        start = params["start"]
        end = params["end"]
        attendees = params.get("attendees", [])
        log.info("Tool call: create_calendar_event — %s on %s", summary, calendar)

        cmd_parts = [
            "python3 /app/tools/calendar_write.py",
            f"--calendar {_shell_quote(calendar)}",
            f"--summary {_shell_quote(summary)}",
            f"--start {_shell_quote(start)}",
            f"--end {_shell_quote(end)}",
        ]
        for addr in attendees:
            cmd_parts.append(f"--attendee {_shell_quote(addr)}")

        return await self.executor.run_command(" ".join(cmd_parts))

    async def _build_system_prompt(self) -> str:
        cfg = self.config.agent
        skills_block = self.skills.get_all_skills()
        character = self._load_file(cfg.character_file)
        personalia = self._load_file(cfg.personalia_file)
        memories = await self.memory.format_for_prompt()

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
Never guess at command syntax — always refer to the skill file.

You can store and recall memories using the sqlite3 CLI (see the memory skill).
Proactively remember important facts about the user and their contacts.
Before inserting a new long-term memory, check if it already exists to avoid duplicates."""

        if memories:
            prompt += f"""

<memories>
{memories}
</memories>"""

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
