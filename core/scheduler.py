"""Scheduler — APScheduler wrapper for cron jobs and one-shot tasks.

Two job types:
  - "agent": natural-language task → agent.process() → send result to channel
  - "system": raw CLI command → executor.run_command_trusted()
    (e.g. vdirsyncer sync, memory cleanup)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

if TYPE_CHECKING:
    from core.agent import AgentCore
    from core.config import SchedulerConfig

log = logging.getLogger(__name__)


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
        self.scheduler = AsyncIOScheduler(
            jobstores={"default": SQLAlchemyJobStore(url=f"sqlite:///{db_path}")},
        )

    def load_jobs(self, config: SchedulerConfig) -> None:
        """Register cron jobs from config. Replaces any existing jobs with the same ID."""
        for job in config.jobs:
            cron_kwargs = _parse_cron(job.cron)

            if job.type == "system":
                self.scheduler.add_job(
                    self._run_system_command,
                    "cron",
                    id=job.id,
                    kwargs={"command": job.task},
                    replace_existing=True,
                    **cron_kwargs,
                )
            else:
                self.scheduler.add_job(
                    self._run_agent_task,
                    "cron",
                    id=job.id,
                    kwargs={"task": job.task, "channel": job.channel},
                    replace_existing=True,
                    **cron_kwargs,
                )

            log.info("Registered cron job %r: %s (%s)", job.id, job.cron, job.type)

    def add_one_shot(self, job_id: str, run_at: datetime, task: str, channel: str) -> None:
        """Schedule a one-time future task (used by the schedule_task tool)."""
        self.scheduler.add_job(
            self._run_agent_task,
            "date",
            id=job_id,
            run_date=run_at,
            kwargs={"task": task, "channel": channel},
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

    # -- Internal job runners ------------------------------------------------

    async def _run_agent_task(self, task: str, channel: str = "telegram") -> None:
        """Execute a natural-language task through the agent and deliver the result."""
        log.info("Scheduler running agent task: %s", task[:100])
        try:
            response = await self.agent.process(
                message=task,
                channel="system",
                user_id="scheduler",
            )

            # Deliver the response to the target channel
            ch = self.agent.channels.get(channel)
            if ch and response.text:
                # For Telegram, send to the first allowed user (the owner)
                chat_id = self._get_owner_chat_id(channel)
                if chat_id:
                    await ch.send(chat_id, response.text)
                else:
                    log.warning(
                        "Scheduler: no owner chat ID for channel %r, response dropped", channel
                    )
            elif not ch:
                log.warning("Scheduler: channel %r not registered, response dropped", channel)
        except Exception:
            log.exception("Scheduler agent task failed: %s", task[:100])

    async def _run_system_command(self, command: str) -> None:
        """Execute a raw CLI command (e.g. vdirsyncer sync, memory cleanup)."""
        log.info("Scheduler running system command: %s", command[:100])
        try:
            result = await self.agent.executor.run_command_trusted(command)
            if result.get("exit_code", 0) != 0:
                log.warning(
                    "Scheduler system command failed (exit %s): %s",
                    result.get("exit_code"),
                    result.get("stderr", "")[:200],
                )
        except Exception:
            log.exception("Scheduler system command failed: %s", command[:100])

    def _get_owner_chat_id(self, channel: str) -> int | str | None:
        """Get the owner's chat ID for proactive messages."""
        if channel == "telegram":
            tg_config = self.agent.config.channels.telegram
            if tg_config.allowed_user_ids:
                return tg_config.allowed_user_ids[0]
        return None
