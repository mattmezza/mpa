"""Subagents — scoped sub-loops the main agent can delegate to (issue #15).

A *subagent* is one execution primitive (``AgentCore.run_subagent``) reached by
two trigger paths: on demand via the ``spawn_subagent`` tool, or on a schedule
via a ``subagent`` job. Either way it runs the existing agent loop with **system
semantics** (no goal decomposition / memory / reflection / approval prompts) and
a persona whose tool/skill/secret scope is a *subset* of the caller's — never
wider (``narrow_scope``).

This module holds only the lifecycle bookkeeping: a small in-memory registry of
runs so list / status / cancel work from Telegram and the admin UI. Runs are
ephemeral (an ``asyncio`` task each) — a restart kills them, so an in-memory
registry is the right scope; nothing here needs to survive a reboot.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass, field

# Keep at most this many finished runs for after-the-fact inspection.
_MAX_FINISHED = 50

# Appended to a subagent's system prompt so files it writes reach the spawning
# agent. Subagents and the main agent share one process cwd / filesystem (see
# core/executor.py — subprocesses inherit the cwd), so a file a subagent creates
# is already on disk where the parent can read it; the only gap is the parent
# knowing the path. This closes that gap by making the subagent report paths in
# its result, which is folded into the parent's history.
FILE_HANDOFF_INSTRUCTION = (
    "You share a filesystem with the agent that spawned you: it can read any "
    "file you leave behind, but only if it knows the path. So if you create or "
    "modify any files, end your final reply with a line 'Files:' followed by "
    "their absolute paths, one per line. If you made no files, omit it."
)

# Appended to a subagent's system prompt. A subagent reports to the agent that
# spawned it, never to a human — the agent synthesises the user-facing answer
# from this result, so prose, greetings, and big formatted tables here are just
# noise the agent has to wade through. Keep the result a dense fact dump.
RESULT_FOR_AGENT_INSTRUCTION = (
    "Your final reply is read by the agent that spawned you, NOT by a human. "
    "It synthesises the user's answer from it. So return only the findings — "
    "dense and factual, no greeting, no preamble, no offers to help further, no "
    "elaborate tables. Just the facts the agent asked for, as briefly as possible."
)

_EFFORT_LEVELS = {"off": "", "low": "low", "medium": "medium", "high": "high"}


def normalize_effort(value: str | None) -> str | None:
    """Map a tool-supplied thinking effort to an LLM thinking level.

    ``None``/empty → ``None`` = *inherit the caller's level* (the default, so a
    subagent thinks as hard as its parent unless told otherwise). ``"off"`` →
    ``""`` (reasoning off); ``low``/``medium``/``high`` pass through. Anything
    unrecognised → ``None``, degrading to the safe inherit default rather than
    erroring on a bad value.
    """
    if not value:
        return None
    return _EFFORT_LEVELS.get(str(value).strip().lower())


def resolve_cap(value: object, ceiling: int, floor: int = 1) -> int:
    """Clamp a caller-requested run cap (steps / token budget) into bounds.

    ``None`` (the caller didn't choose) → the configured ``ceiling``, preserving
    prior behaviour. A chosen value is honoured up to the ceiling: the config is
    a safety guardrail the agent may dial *down* but never exceed. A non-numeric
    value coerces to the ceiling rather than raising.
    """
    if value is None:
        return ceiling
    try:
        return max(floor, min(int(value), ceiling))  # type: ignore[arg-type]
    except TypeError, ValueError, OverflowError:
        # OverflowError: json.loads accepts the literal `Infinity`, and int(inf)
        # overflows — degrade it to the ceiling like any other bad value.
        return ceiling


def narrow_scope(parent: list[str] | None, child: list[str] | None) -> list[str]:
    """Intersect a child scope with the parent's — inherit, never widen.

    The allowlist convention (see :class:`core.personae.Persona`) is ``[]``/``None``
    = *all*. So an empty parent means "no restriction → the child's own scope
    applies", an empty child means "unspecified → inherit the parent's", and when
    both list names the result is their intersection (the child can never gain a
    name the parent lacked).
    """
    p = parent or []
    c = child or []
    if not p:
        return list(c)
    if not c:
        return list(p)
    return [x for x in c if x in p]


@dataclass(slots=True)
class SubagentRun:
    """One subagent execution and its live status."""

    run_id: str
    persona: str
    task: str
    depth: int = 1
    background: bool = False
    # Per-run sizing the spawning agent chose, resolved/clamped via resolve_cap
    # before the run starts (so always concrete on a live run; the 0 defaults are
    # only placeholders for direct construction). effort None = inherit caller level.
    max_steps: int = 0
    token_budget: int = 0
    effort: str | None = None
    status: str = "running"  # running | done | cancelled | error
    progress: str = ""
    result: str = ""
    error: str = ""
    # True once a background run's result has been folded into a synthesis turn,
    # so it is not picked up again by a later batch (#15).
    synthesized: bool = False
    started_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    origin_channel: str = ""
    origin_user_id: str = ""
    origin_chat_id: str = ""
    _task: asyncio.Task | None = field(default=None, repr=False, compare=False)

    @property
    def elapsed(self) -> float:
        return (self.finished_at or time.time()) - self.started_at

    @property
    def elapsed_str(self) -> str:
        secs = int(self.elapsed)
        if secs < 60:
            return f"{secs}s"
        return f"{secs // 60}m {secs % 60}s"


class SubagentRegistry:
    """In-memory registry of subagent runs for list / status / cancel."""

    def __init__(self) -> None:
        self._runs: OrderedDict[str, SubagentRun] = OrderedDict()

    def register(self, run: SubagentRun) -> None:
        self._runs[run.run_id] = run
        self._trim()

    def attach_task(self, run_id: str, task: asyncio.Task) -> None:
        run = self._runs.get(run_id)
        if run:
            run._task = task

    def get(self, run_id: str) -> SubagentRun | None:
        return self._runs.get(run_id)

    def active_count(self) -> int:
        return sum(1 for r in self._runs.values() if r.status == "running")

    def list_runs(self, active_only: bool = False) -> list[SubagentRun]:
        runs = [r for r in self._runs.values() if not active_only or r.status == "running"]
        return sorted(runs, key=lambda r: r.started_at, reverse=True)

    def running_for(self, channel: str, chat_id: str) -> list[SubagentRun]:
        """Still-running background runs spawned from one chat, oldest first.

        Surfaced in the spawning agent's turn preamble so it always knows what is
        pending. (Finished runs are folded into the chat history instead, so the
        agent remembers their results natively rather than as ephemeral status.)
        """
        out = [
            r
            for r in self._runs.values()
            if r.status == "running" and r.origin_channel == channel and r.origin_chat_id == chat_id
        ]
        return sorted(out, key=lambda r: r.started_at)

    def finish(self, run_id: str, status: str, *, result: str = "", error: str = "") -> bool:
        """Move a *running* run to a terminal state. Returns False (a no-op) when
        the run is unknown or already finished, so a terminal state is sticky —
        e.g. a late normal completion cannot overwrite a cancellation."""
        run = self._runs.get(run_id)
        if not run or run.status != "running":
            return False
        run.status = status
        run.result = result
        run.error = error
        run.finished_at = time.time()
        self._trim()
        return True

    def cancel(self, run_id: str) -> bool:
        """Request cancellation of a running subagent. Returns False if it is not
        running (unknown id or already finished)."""
        run = self._runs.get(run_id)
        if not run or run.status != "running":
            return False
        if run._task and not run._task.done():
            run._task.cancel()
        # The task's CancelledError handler flips status to "cancelled"; set it
        # eagerly too so a sync caller / the next poll sees it immediately.
        run.status = "cancelled"
        run.finished_at = time.time()
        return True

    def _trim(self) -> None:
        """Drop the oldest finished runs once we exceed the cap (running runs are
        always kept)."""
        finished = [rid for rid, r in self._runs.items() if r.status != "running"]
        excess = len(finished) - _MAX_FINISHED
        for rid in finished[:excess] if excess > 0 else []:
            self._runs.pop(rid, None)


def short_summary(text: str, limit: int = 280) -> str:
    """A one-glance summary of a subagent's result — first non-empty line, capped.

    ponytail: a truncation, not an LLM call. Add a summariser model only if the
    plain preview proves too lossy in practice.
    """
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line[:limit] + ("…" if len(line) > limit else "")
    return (text or "").strip()[:limit]
