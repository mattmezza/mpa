"""Permission engine — glob-pattern rules with ALWAYS/ASK/NEVER levels.

Each rule maps a pattern like "run_command:himalaya*list*" to a permission level.
The engine checks tool calls against these patterns to decide whether to execute
immediately, ask the user for approval, or block entirely.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import uuid

log = logging.getLogger(__name__)


class PermissionLevel:
    ALWAYS = "ALWAYS"  # Pre-approved, execute without asking
    ASK = "ASK"  # Pause and ask the user for approval
    NEVER = "NEVER"  # Block entirely


# Default rules — read operations are ALWAYS, write operations ASK, destructive NEVER.
DEFAULT_RULES: dict[str, str] = {
    # Read operations — safe by default
    "run_command:himalaya*list*": "ALWAYS",
    "run_command:himalaya*read*": "ALWAYS",
    "run_command:himalaya*envelope*": "ALWAYS",
    "run_command:himalaya*folder*": "ALWAYS",
    "run_command:khard*": "ALWAYS",
    "run_command:python3 /app/tools/calendar_read.py*": "ALWAYS",
    "run_command:vdirsyncer*": "ALWAYS",
    "run_command:sqlite3*/app/data/memory.db*SELECT*": "ALWAYS",
    "run_command:sqlite3*/app/data/memory.db*INSERT*": "ALWAYS",
    "run_command:sqlite3*/app/data/memory.db*UPDATE*": "ALWAYS",
    "run_command:sqlite3*/app/data/memory.db*DELETE*": "ALWAYS",
    "run_command:jq*": "ALWAYS",
    "web_search": "ALWAYS",
    # Write operations — ask first
    "send_email": "ASK",
    "reply_email": "ASK",
    "send_message": "ASK",
    "create_calendar_event": "ASK",
    "run_command:himalaya*send*": "ASK",
    "run_command:himalaya*delete*": "ASK",
    "run_command:himalaya*move*": "ASK",
    "schedule_task": "ASK",
    # Dangerous — never allow
    "run_command:sqlite3*DROP*": "NEVER",
    "run_command:sqlite3*ALTER*": "NEVER",
}


class PermissionEngine:
    """Check tool actions against permission rules using glob patterns."""

    def __init__(self) -> None:
        self.rules: dict[str, str] = dict(DEFAULT_RULES)
        # Pending approval requests: request_id → asyncio.Future[bool]
        self._pending: dict[str, asyncio.Future[bool]] = {}

    def check(self, tool_name: str, params: dict | None = None) -> str:
        """Return the permission level for a tool call.

        Builds a match key like "run_command:himalaya envelope list ..."
        and checks it against all rules. First match wins, with more
        specific (longer) patterns tried first.
        """
        if tool_name == "run_command" and params and "command" in params:
            match_key = f"run_command:{params['command']}"
        else:
            match_key = tool_name

        # Sort rules by pattern length descending so more specific rules match first
        for pattern in sorted(self.rules, key=len, reverse=True):
            if fnmatch.fnmatch(match_key, pattern):
                return self.rules[pattern]

        # Default: ASK for unknown actions (safe fallback)
        return PermissionLevel.ASK

    def add_rule(self, pattern: str, level: str) -> None:
        """Add or update a permission rule."""
        if level not in (PermissionLevel.ALWAYS, PermissionLevel.ASK, PermissionLevel.NEVER):
            raise ValueError(f"Invalid permission level: {level!r}")
        self.rules[pattern] = level
        log.info("Permission rule added: %s → %s", pattern, level)

    def create_approval_request(self) -> tuple[str, asyncio.Future[bool]]:
        """Create a pending approval request. Returns (request_id, future).

        The caller awaits the future. When the user approves/denies via
        Telegram callback, resolve_approval() completes the future.
        """
        request_id = uuid.uuid4().hex[:12]
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()
        self._pending[request_id] = future
        return request_id, future

    def resolve_approval(self, request_id: str, approved: bool) -> bool:
        """Resolve a pending approval request. Returns False if not found."""
        future = self._pending.pop(request_id, None)
        if future is None or future.done():
            return False
        future.set_result(approved)
        return True

    def format_approval_message(self, tool_name: str, params: dict) -> str:
        """Format a human-readable approval prompt for a tool call."""
        if tool_name == "send_email":
            to = params.get("to", "?")
            subject = params.get("subject", "?")
            return f"Send email to {to}\nSubject: {subject}"
        if tool_name == "reply_email":
            account = params.get("account", "?")
            msg_id = params.get("message_id", "?")
            return f"Reply to message {msg_id} on {account}"
        if tool_name == "send_message":
            channel = params.get("channel", "?")
            to = params.get("to", "?")
            text = params.get("text", "")
            preview = text[:100] + ("…" if len(text) > 100 else "")
            return f"Send {channel} message to {to}\n{preview}"
        if tool_name == "create_calendar_event":
            summary = params.get("summary", "?")
            start = params.get("start", "?")
            return f"Create event: {summary}\nAt: {start}"
        if tool_name == "schedule_task":
            task = params.get("task", "?")
            run_at = params.get("run_at", "?")
            return f"Schedule task at {run_at}\n{task}"
        if tool_name == "run_command":
            cmd = params.get("command", "?")
            purpose = params.get("purpose", "")
            return f"Run command: {cmd}" + (f"\n({purpose})" if purpose else "")
        return f"{tool_name}: {params}"
