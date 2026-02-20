"""Scheduler — APScheduler wrapper for cron jobs and one-shot tasks.

Three job types:
  - "agent": natural-language task → agent.process() → send result to channel
  - "system": raw CLI command → executor.run_command_trusted()
    (e.g. memory cleanup)
  - "memory_consolidation": review short-term memories, promote worthy ones
    to long-term, delete expired entries (uses a lightweight LLM call)
"""

from __future__ import annotations

import logging
import shlex
from datetime import datetime
from typing import TYPE_CHECKING

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

if TYPE_CHECKING:
    from core.agent import AgentCore
    from core.config import SchedulerConfig

log = logging.getLogger(__name__)

_AGENT_CONTEXT: AgentCore | None = None


def set_agent_context(agent: AgentCore | None) -> None:
    """Set the global agent context for persisted scheduler jobs."""
    global _AGENT_CONTEXT
    _AGENT_CONTEXT = agent


def _get_agent_context() -> AgentCore | None:
    return _AGENT_CONTEXT


async def run_agent_task(
    task: str,
    channel: str = "telegram",
    job_id: str | None = None,
    silent: bool = False,
) -> None:
    """Execute a natural-language task through the agent and deliver the result."""
    agent = _get_agent_context()
    if agent is None:
        log.error("Scheduler agent task dropped; agent not initialized")
        return

    lower_task = task.lower()
    is_email_check = job_id == "email_check" or (
        "email" in lower_task and "notify me if any" in lower_task
    )
    silent_mode = silent or is_email_check
    if silent_mode:
        task = (
            task
            + "\n\nIf there is nothing important or urgent to report, respond with [NO_UPDATES] "
            "only."
        )

    log.info("Scheduler running agent task: %s", task[:100])
    try:
        response = await agent.process(
            message=task,
            channel="system",
            user_id="scheduler",
        )

        # Deliver the response to the target channel
        ch = agent.channels.get(channel)
        if ch and response.text:
            text = response.text.replace("[NO_UPDATES]", "").strip()
            if silent_mode and not text:
                log.info("Scheduler silent task produced no updates; skipping send")
                return
            # For Telegram, send to the first allowed user (the owner)
            chat_id = _get_owner_chat_id(agent, channel)
            if chat_id:
                await ch.send(chat_id, text or response.text)
            else:
                log.warning("Scheduler: no owner chat ID for channel %r, response dropped", channel)
        elif not ch:
            log.warning("Scheduler: channel %r not registered, response dropped", channel)
    except Exception:
        log.exception("Scheduler agent task failed: %s", task[:100])


async def run_system_command(command: str) -> None:
    """Execute a raw CLI command (e.g. memory cleanup)."""
    agent = _get_agent_context()
    if agent is None:
        log.error("Scheduler system command dropped; agent not initialized")
        return

    command = _maybe_rewrite_vdirsyncer(command)
    log.info("Scheduler running system command: %s", command[:100])
    try:
        result = await agent.executor.run_command_trusted(command)
        if result.get("exit_code", 0) != 0:
            log.warning(
                "Scheduler system command failed (exit %s): %s",
                result.get("exit_code"),
                result.get("stderr", "")[:200],
            )
    except Exception:
        log.exception("Scheduler system command failed: %s", command[:100])


def _maybe_rewrite_vdirsyncer(command: str) -> str:
    return command


async def run_memory_consolidation() -> None:
    """Review short-term memories, promote worthy ones, delete expired."""
    agent = _get_agent_context()
    if agent is None:
        log.error("Scheduler memory consolidation dropped; agent not initialized")
        return

    log.info("Scheduler running memory consolidation")
    try:
        llm = agent._memory_llm(agent.config.memory.consolidation_provider)
        result = await agent.memory.consolidate_and_cleanup(
            llm=llm,
            model=agent.config.memory.consolidation_model,
        )
        log.info(
            "Memory consolidation done: %d reviewed, %d promoted, %d expired deleted",
            result["active_reviewed"],
            result["promoted_to_long_term"],
            result["expired_deleted"],
        )
    except Exception:
        log.exception("Scheduler memory consolidation failed")


def _get_owner_chat_id(agent: AgentCore, channel: str) -> int | str | None:
    """Get the owner's chat ID for proactive messages."""
    if channel == "telegram":
        tg_config = agent.config.channels.telegram
        if tg_config.allowed_user_ids:
            return tg_config.allowed_user_ids[0]
    return None


def _parse_cron(expr: str) -> dict:
    """Parse a standard 5-field cron expression into APScheduler kwargs.

    Format: minute hour day_of_month month day_of_week
    Example: "0 7 * * *" → every day at 07:00
    """
    parts = expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Expected 5-field cron expression, got {len(parts)} fields: {expr!r}")

    fields = ["minute", "hour", "day", "month", "day_of_week"]
    result = {}
    for field, value in zip(fields, parts):
        if value != "*":
            result[field] = value
    return result


class AgentScheduler:
    """Wraps APScheduler with agent-aware job execution."""

    def __init__(self, db_path: str, agent: AgentCore):
        self.agent = agent
        set_agent_context(agent)
        self.scheduler = AsyncIOScheduler(
            jobstores={"default": SQLAlchemyJobStore(url=f"sqlite:///{db_path}")},
        )

    def load_jobs(self, config: SchedulerConfig) -> None:
        """Register cron jobs from config. Replaces any existing jobs with the same ID."""
        for job in config.jobs:
            cron_kwargs = _parse_cron(job.cron)

            if job.type == "system":
                self.scheduler.add_job(
                    run_system_command,
                    "cron",
                    id=job.id,
                    kwargs={"command": job.task},
                    replace_existing=True,
                    **cron_kwargs,
                )
            elif job.type == "memory_consolidation":
                self.scheduler.add_job(
                    run_memory_consolidation,
                    "cron",
                    id=job.id,
                    replace_existing=True,
                    **cron_kwargs,
                )
            else:
                self.scheduler.add_job(
                    run_agent_task,
                    "cron",
                    id=job.id,
                    kwargs={
                        "task": job.task,
                        "channel": job.channel,
                        "job_id": job.id,
                    },
                    replace_existing=True,
                    **cron_kwargs,
                )

            log.info("Registered cron job %r: %s (%s)", job.id, job.cron, job.type)

    def add_one_shot(self, job_id: str, run_at: datetime, task: str, channel: str) -> None:
        """Schedule a one-time future task (used by the schedule_task tool)."""
        self.scheduler.add_job(
            run_agent_task,
            "date",
            id=job_id,
            run_date=run_at,
            kwargs={"task": task, "channel": channel, "job_id": job_id},
            replace_existing=True,
        )
        log.info("Scheduled one-shot job %r at %s", job_id, run_at)

    def start(self) -> None:
        """Start the scheduler. Call after load_jobs()."""
        self.scheduler.start()
        log.info("Scheduler started with %d jobs", len(self.scheduler.get_jobs()))

    def shutdown(self) -> None:
        """Gracefully shut down the scheduler."""
        self.scheduler.shutdown(wait=False)
        log.info("Scheduler shut down")
