"""Agent core — LLM call with agentic tool-use loop."""

from __future__ import annotations

import asyncio
import json
import logging
import shlex
import uuid
from datetime import datetime
from typing import Any, cast

from tavily import TavilyClient

from core.config import Config
from core.executor import ToolExecutor
from core.history import ConversationHistory
from core.llm import LLMClient, LLMToolCall
from core.memory import MemoryStore
from core.models import AgentResponse, Attachment
from core.permissions import PermissionEngine, PermissionLevel, format_approval_message
from core.scheduler import AgentScheduler
from core.skills import SkillsEngine
from voice.pipeline import VoicePipeline

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
    # Structured tools for write actions (permission-gated via PermissionEngine)
    {
        "name": "send_email",
        "description": "Send a new email on behalf of the user.",
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {
                    "type": "string",
                    "description": "Email account name (e.g. 'personal', 'work')",
                },
                "from": {
                    "type": "string",
                    "description": "Sender email address (must match the account)",
                },
                "to": {
                    "type": "string",
                    "description": "Recipient email address(es), comma-separated",
                },
                "cc": {"type": "string", "description": "CC recipient(s), comma-separated"},
                "bcc": {"type": "string", "description": "BCC recipient(s), comma-separated"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["account", "to", "subject", "body"],
        },
    },
    {
        "name": "reply_email",
        "description": "Reply to an existing email by message ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {
                    "type": "string",
                    "description": "Email account name (e.g. 'personal', 'work')",
                },
                "message_id": {
                    "type": "string",
                    "description": "The ID of the message to reply to",
                },
                "body": {"type": "string", "description": "The reply body text"},
                "reply_all": {
                    "type": "boolean",
                    "description": "Reply to all recipients (default: false)",
                },
                "folder": {
                    "type": "string",
                    "description": "Folder the message is in (default: INBOX)",
                },
            },
            "required": ["account", "message_id", "body"],
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
        "name": "load_skill",
        "description": "Load a named skill document by name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Skill name to load"},
            },
            "required": ["name"],
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
        self.llm: LLMClient = LLMClient.from_agent_config(config.agent)
        self.skills = SkillsEngine(
            db_path=config.agent.skills_db_path,
            seed_dir=config.agent.skills_dir,
        )
        self.executor = ToolExecutor()
        self.history = ConversationHistory(
            db_path=config.history.db_path,
            max_turns=config.history.max_turns,
        )
        self.history_mode = config.history.mode  # "injection" or "session"
        self.memory = MemoryStore(
            db_path=config.memory.db_path,
            long_term_limit=config.memory.long_term_limit,
        )
        self.channels: dict = {}
        self.voice: VoicePipeline | None = None
        config_db = "data/config.db"
        self.scheduler = AgentScheduler(config_db, self)
        self.permissions = PermissionEngine(db_path=config_db)

        # Web search (Tavily)
        if config.search.enabled and config.search.api_key:
            self.search_client: TavilyClient | None = TavilyClient(
                api_key=config.search.api_key,
            )
            log.info("Web search enabled (provider: %s)", config.search.provider)
        else:
            self.search_client = None
            log.info("Web search disabled (no API key or not enabled)")

    async def process(
        self,
        message: str,
        channel: str,
        user_id: str,
        attachments: list[Attachment] | None = None,
    ) -> AgentResponse:
        """Process an incoming message through the LLM with tool-use loop."""
        system = await self._build_system_prompt()

        if self.history_mode == "session":
            return await self._process_session(system, message, channel, user_id, attachments)
        return await self._process_injection(system, message, channel, user_id, attachments)

    def _build_user_message(
        self,
        message: str,
        attachments: list[Attachment] | None = None,
    ) -> dict:
        """Build the user message dict, handling multimodal content."""
        image_attachments = [a for a in (attachments or []) if a.is_image]
        if image_attachments:
            content_blocks: list[dict] = []
            if message:
                content_blocks.append({"type": "text", "text": message})
            for att in image_attachments:
                if self.llm.provider == "anthropic":
                    content_blocks.append(att.to_anthropic_block())
                else:
                    content_blocks.append(att.to_openai_block())
            return {"role": "user", "content": content_blocks}
        return {"role": "user", "content": message}

    async def _process_injection(
        self,
        system: str,
        message: str,
        channel: str,
        user_id: str,
        attachments: list[Attachment] | None = None,
    ) -> AgentResponse:
        """Injection mode: replay windowed history as native alternating messages."""
        history = await self.history.get_messages(channel, user_id)
        messages: list[dict] = []

        if history:
            # Replay history as proper alternating user/assistant messages
            for turn in history:
                messages.append({"role": turn["role"], "content": turn["content"]})

        # The actual current request — always the last user message.
        messages.append(self._build_user_message(message, attachments))

        log.info("Processing message (injection) from %s/%s: %s", channel, user_id, message[:100])

        # Initial LLM call
        response = await self.llm.generate(
            model=self.config.agent.model,
            max_tokens=4096,
            system=system,
            messages=messages,
            tools=cast(Any, TOOLS),
        )

        # Agentic loop — keep going while the LLM wants to call tools
        while response.tool_calls:
            tool_results = []
            for call in response.tool_calls:
                result = await self._execute_tool(call, channel, user_id)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": json.dumps(result),
                    }
                )

            # Feed tool results back to the LLM
            messages.append(self.llm.assistant_message(response))
            messages.extend(self.llm.tool_result_messages(tool_results))
            response = await self.llm.generate(
                model=self.config.agent.model,
                max_tokens=4096,
                system=system,
                messages=messages,
                tools=cast(Any, TOOLS),
            )

        final_text = response.text
        log.info("Response: %s", final_text[:200])

        # Check if the LLM wants to respond with voice
        voice_bytes = await self._maybe_synthesize_voice(final_text)
        if voice_bytes:
            final_text = final_text.replace("[respond_with_voice]", "").strip()

        # Persist the turn (user message + final assistant text only)
        history_message = self._history_message_text(message, attachments)
        await self.history.add_turn(channel, user_id, "user", history_message)
        await self.history.add_turn(channel, user_id, "assistant", final_text)

        # Automatic memory extraction
        if channel != "system":
            asyncio.create_task(
                self._extract_memories(message, final_text),
                name=f"memory-extract-{user_id}",
            )

        return AgentResponse(text=final_text, voice=voice_bytes)

    async def _process_session(
        self,
        system: str,
        message: str,
        channel: str,
        user_id: str,
        attachments: list[Attachment] | None = None,
    ) -> AgentResponse:
        """Session mode: sticky session per (channel, user_id).

        The full message array is kept in memory and persisted to SQLite.
        New messages are appended, giving the LLM full conversational
        continuity with a cache-friendly prefix.
        """
        # Load existing session (from memory cache or DB)
        session = await self.history.get_session(channel, user_id)

        # Append the new user message
        user_msg = self._build_user_message(message, attachments)
        await self.history.append_session_message(channel, user_id, user_msg)

        log.info("Processing message (session) from %s/%s: %s", channel, user_id, message[:100])

        # Initial LLM call with the full session
        response = await self.llm.generate(
            model=self.config.agent.model,
            max_tokens=4096,
            system=system,
            messages=session,
            tools=cast(Any, TOOLS),
        )

        # Agentic loop — keep going while the LLM wants to call tools
        new_messages: list[dict] = []
        while response.tool_calls:
            tool_results = []
            for call in response.tool_calls:
                result = await self._execute_tool(call, channel, user_id)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": json.dumps(result),
                    }
                )

            # Append tool exchange to session
            assistant_msg = self.llm.assistant_message(response)
            tool_result_msgs = self.llm.tool_result_messages(tool_results)

            new_messages.append(assistant_msg)
            new_messages.extend(tool_result_msgs)

            # The in-memory session list is mutated by append_session_messages
            # so the next generate() call sees the updated messages.
            await self.history.append_session_messages(
                channel, user_id, [assistant_msg, *tool_result_msgs]
            )

            response = await self.llm.generate(
                model=self.config.agent.model,
                max_tokens=4096,
                system=system,
                messages=session,
                tools=cast(Any, TOOLS),
            )

        # Append the final assistant response to the session
        final_assistant_msg = {"role": "assistant", "content": response.text}
        await self.history.append_session_message(channel, user_id, final_assistant_msg)

        final_text = response.text
        log.info("Response: %s", final_text[:200])

        # Check if the LLM wants to respond with voice
        voice_bytes = await self._maybe_synthesize_voice(final_text)
        if voice_bytes:
            final_text = final_text.replace("[respond_with_voice]", "").strip()

        # Automatic memory extraction
        if channel != "system":
            asyncio.create_task(
                self._extract_memories(message, final_text),
                name=f"memory-extract-{user_id}",
            )

        return AgentResponse(text=final_text, voice=voice_bytes)

    @staticmethod
    def _history_message_text(message: str, attachments: list[Attachment] | None = None) -> str:
        """Build the text to store in history for a user message."""
        image_attachments = [a for a in (attachments or []) if a.is_image]
        if image_attachments:
            n = len(image_attachments)
            label = "image" if n == 1 else f"{n} images"
            suffix = f" [{label} attached]"
            return (message + suffix) if message else suffix.strip()
        return message

    async def _maybe_synthesize_voice(self, text: str) -> bytes | None:
        """Synthesize voice if requested by the LLM."""
        if "[respond_with_voice]" in text and self.voice:
            clean_text = text.replace("[respond_with_voice]", "").strip()
            try:
                return await self.voice.synthesize(clean_text)
            except Exception:
                log.exception("TTS synthesis failed, sending text only")
        return None

    async def _execute_tool(self, tool_call: LLMToolCall, channel: str, user_id: str) -> dict:
        """Dispatch a tool call from the LLM, with permission checks."""
        name = tool_call.name
        params = tool_call.arguments

        # --- Permission check ---
        level = self.permissions.check(name, params)

        if level == PermissionLevel.NEVER:
            log.warning("Permission DENIED (NEVER): %s — %s", name, params)
            return {"error": "This action is not allowed."}

        if level == PermissionLevel.ASK and channel != "system":
            approved = await self._request_approval(name, params, channel, user_id)
            if not approved:
                log.info("Permission DENIED (user rejected): %s", name)
                return {"error": "Action denied by user."}

        # --- Dispatch ---
        if name == "run_command":
            log.info("Tool call: run_command — %s", params.get("purpose", ""))
            return await self.executor.run_command(params["command"])

        if name == "send_email":
            return await self._tool_send_email(params)

        if name == "reply_email":
            return await self._tool_reply_email(params)

        if name == "send_message":
            return await self._tool_send_message(params)

        if name == "create_calendar_event":
            return await self._tool_create_calendar_event(params)

        if name == "web_search":
            log.info("Tool call: web_search — %s", params.get("query", ""))
            return await self._tool_web_search(params)

        if name == "load_skill":
            skill_name = str(params.get("name", "")).strip()
            if not skill_name:
                return {"error": "Missing skill name."}
            content = await self.skills.get_skill_content(skill_name)
            if not content:
                return {"error": f"Skill not found: {skill_name}"}
            return {"name": skill_name, "content": content}

        if name == "schedule_task":
            log.info("Tool call: schedule_task — %s", params.get("task", ""))
            return self._tool_schedule_task(params)

        return {"error": f"Unknown tool: {name}"}

    # -- Structured tool implementations --

    async def _tool_send_email(self, params: dict) -> dict:
        """Send an email via himalaya CLI."""
        account = params["account"]
        to = params["to"]
        subject = params["subject"]
        body = params["body"]
        cc = params.get("cc")
        bcc = params.get("bcc")
        from_addr = params.get("from")
        log.info("Tool call: send_email — to=%s subject=%s", to, subject)

        # Build MML message headers
        headers = []
        if from_addr:
            headers.append(f"From: {from_addr}")
        headers.append(f"To: {to}")
        if cc:
            headers.append(f"Cc: {cc}")
        if bcc:
            headers.append(f"Bcc: {bcc}")
        headers.append(f"Subject: {subject}")
        mml = "\n".join(headers) + "\n\n" + body

        command = (
            f"printf %s {_shell_quote(mml)} | himalaya -a {_shell_quote(account)} message send"
        )
        return await self.executor.run_command_trusted(command)

    async def _tool_reply_email(self, params: dict) -> dict:
        """Reply to an email via himalaya CLI."""
        account = params["account"]
        message_id = params["message_id"]
        body = params["body"]
        reply_all = params.get("reply_all", False)
        folder = params.get("folder")
        log.info("Tool call: reply_email — account=%s message=%s", account, message_id)

        cmd_parts = [f"printf %s {_shell_quote(body)} | himalaya -a {_shell_quote(account)}"]
        if folder:
            cmd_parts.append(f"--folder {_shell_quote(folder)}")
        cmd_parts.append("message reply")
        if reply_all:
            cmd_parts.append("--all")
        cmd_parts.append(_shell_quote(message_id))

        return await self.executor.run_command_trusted(" ".join(cmd_parts))

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

    def _tool_schedule_task(self, params: dict) -> dict:
        """Schedule a one-shot future task via APScheduler."""
        task = params["task"]
        run_at_str = params["run_at"]
        channel = params.get("channel", "telegram")

        try:
            run_at = datetime.fromisoformat(run_at_str)
        except ValueError:
            return {"error": f"Invalid datetime format: {run_at_str!r}. Use ISO format."}

        job_id = f"oneshot_{uuid.uuid4().hex[:8]}"
        try:
            self.scheduler.add_one_shot(job_id, run_at, task, channel)
            return {
                "ok": True,
                "job_id": job_id,
                "run_at": run_at.isoformat(),
                "task": task,
                "channel": channel,
            }
        except Exception as exc:
            return {"error": f"Failed to schedule task: {exc}"}

    async def _tool_web_search(self, params: dict) -> dict:
        """Search the web via Tavily API."""
        if not self.search_client:
            return {"error": "Web search is not configured. Set search.api_key in config."}

        query = params.get("query", "").strip()
        if not query:
            return {"error": "Empty search query."}

        max_results = self.config.search.max_results

        try:
            response = await asyncio.to_thread(
                self.search_client.search,
                query=query,
                max_results=max_results,
            )
        except Exception as exc:
            log.exception("Tavily search failed for query: %s", query)
            return {"error": f"Search failed: {exc}"}

        # Format results for the LLM
        results = []
        for item in response.get("results", []):
            results.append(
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "content": item.get("content", ""),
                }
            )

        return {
            "query": query,
            "results": results,
        }

    async def _request_approval(
        self, tool_name: str, params: dict, channel: str, user_id: str
    ) -> bool:
        """Ask the user for approval via their channel (e.g. Telegram inline keyboard).

        Creates a pending approval future, sends the prompt to the channel,
        and waits for the user to respond. Returns True if approved.
        """
        ch = self.channels.get(channel)
        if not ch:
            # No channel available to ask — auto-approve (e.g. admin API)
            log.warning("No channel %r for approval, auto-approving %s", channel, tool_name)
            return True

        request_id, future = self.permissions.create_approval_request(tool_name, params)
        description = format_approval_message(tool_name, params)

        # Send the approval prompt via the channel
        try:
            await ch.send_approval_request(user_id, request_id, description)
        except AttributeError:
            # Channel doesn't support approval requests — auto-approve
            log.warning("Channel %r doesn't support approvals, auto-approving", channel)
            self.permissions.resolve_approval(request_id, True)
            return True
        except Exception:
            log.exception("Failed to send approval request")
            self.permissions.resolve_approval(request_id, True)
            return True

        # Wait for the user's response (timeout after 2 minutes)
        try:
            return await asyncio.wait_for(future, timeout=120)
        except TimeoutError:
            log.info("Approval request %s timed out", request_id)
            self.permissions._pending.pop(request_id, None)
            return False

    async def _extract_memories(self, user_msg: str, agent_msg: str) -> None:
        """Run automatic memory extraction in the background.

        Uses a cheap/fast model to identify facts worth remembering
        from the conversation turn, then stores them in the memory DB.
        Exceptions are logged and swallowed — this must never crash the
        main agent loop.
        """
        try:
            stored = await self.memory.extract_memories(
                llm=self.llm,
                model=self.config.memory.extraction_model,
                user_msg=user_msg,
                agent_msg=agent_msg,
            )
            if stored:
                log.info("Background memory extraction stored %d memories", stored)
        except Exception:
            log.exception("Background memory extraction failed")

    async def _build_system_prompt(self) -> str:
        cfg = self.config.agent
        skills_index = await self.skills.get_index_block()
        character = cfg.character
        personalia = cfg.personalia
        you_personalia = self.config.you.personalia
        about_user_block = (
            f"<about_user>\n{you_personalia}\n</about_user>\n\n" if you_personalia.strip() else ""
        )
        memories = await self.memory.format_for_prompt()

        prompt = f"""You are {cfg.name}, a personal AI assistant for {cfg.owner_name}.

Today is {datetime.now().strftime("%A, %B %d, %Y")}. Timezone: {cfg.timezone}.

<personalia>
{personalia}
</personalia>

<character>
{character}
</character>

{about_user_block}<tool_usage>
For write actions (sending emails, replying to emails, sending messages, creating calendar events,
scheduling tasks), ALWAYS use the dedicated structured tools: `send_email`, `reply_email`,
`send_message`, `create_calendar_event`, `schedule_task`. NEVER use `run_command` for these — the
structured tools handle quoting, piping, and permissions correctly.

Use `run_command` only for read/query operations (listing emails, reading messages, searching,
managing flags/folders, contacts, memory, etc.).
Always use the skill documentation to construct the correct command.
If you don't have the skill content in context, call `load_skill` with the skill name to load it.
Parse JSON output when available (himalaya supports -o json, sqlite3 supports -json).
If a command fails, read the error and try to fix it.
Never guess at command syntax — always refer to the skill file.
</tool_usage>

You can store and recall memories using the sqlite3 CLI (see the memory skill).
Proactively remember important facts about the user and their contacts.
Before inserting a new long-term memory, check if it already exists to avoid duplicates."""

        # Only include history_handling instructions in injection mode;
        # in session mode the conversation is natively threaded.
        if self.history_mode != "session":
            prompt += """

<history_handling>
Previous messages in this conversation have already been handled.
Always focus exclusively on the latest user message as the current, active request.
Use earlier messages only to understand context, resolve references (e.g. "that", "it",
"the one I mentioned"), and maintain conversational continuity.
</history_handling>"""

        if memories:
            prompt += f"""

<memories>
{memories}
</memories>"""

        if skills_index:
            prompt += f"""

<available_skills>
{skills_index}
</available_skills>"""

        return prompt

    def _extract_text(self, response) -> str:
        """Deprecated: retained for backward compatibility."""
        return response.text if hasattr(response, "text") else ""
