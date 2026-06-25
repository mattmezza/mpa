#!/usr/bin/env python3
"""Browser CLI — headless web automation via Playwright (Chromium).

The agent drives this with the ``run_command`` tool, same as the other CLIs.
Three verbs:

  read       Load a page and return its readable text (for JS-heavy sites).
  screenshot Load a page and save a PNG (the agent/you can then view it).
  act        Load a page and run an ordered list of steps (click/fill/...).

``read`` and ``screenshot`` are read-only (pre-approved). ``act`` changes state
(it clicks/submits) so it asks for approval each time — and because every command
carries ``--url``, per-domain permission rules work out of the box, e.g.
``run_command:python3 tools/browser.py act*github.com*`` -> ALWAYS.

Persistent profiles
  Each ``--profile NAME`` reuses a Chromium ``user-data-dir`` under
  ``data/browser/profiles/NAME``, so cookies/sessions survive between calls. Log
  in once (via the guided ``act`` flow), then the session is reused.

Why one verb does goto+steps: every ``run_command`` is a fresh process, so page
state (the open page, form fields) does NOT survive between calls — only cookies
do, via the profile. So a multi-step interaction (fill user, fill pass, submit)
must be a single ``act`` invocation.
  # ponytail: no live browser kept between calls — each verb re-navigates.
  # Upgrade path if iterative "click around" sessions matter: a per-profile
  # browser daemon / the BROWSER_CDP_URL sidecar below.

Sidecar (optional, off by default)
  If ``BROWSER_CDP_URL`` is set, connect to a remote Chromium over CDP instead of
  launching one locally. Keeps the main image lean (no bundled Chromium). With
  CDP the profile lives on the sidecar, so ``--profile`` is informational only.

Usage examples
  python3 tools/browser.py read --url https://example.com
  python3 tools/browser.py screenshot --url https://example.com -o shot.png
  python3 tools/browser.py act --url https://site/login --profile acme \\
      --steps '[{"fill":["#user","alice"]},{"fill":["#pass","s3cr3t"]},{"click":"#login"}]'
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_DESKTOP_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _data_dir() -> Path:
    # Mirror the convention used by the other tools (tools/skills.py).
    return Path("/app/data") if Path("/app/data").exists() else Path("data")


def _validate_profile(name: str) -> str:
    value = (name or "").strip().lower()
    if not value:
        raise ValueError("Profile name is required")
    if not NAME_PATTERN.match(value):
        raise ValueError("Profile name must be lowercase letters, digits, '-' or '_'")
    return value


def _profile_dir(name: str) -> Path:
    return _data_dir() / "browser" / "profiles" / name


def _state_file(name: str) -> Path:
    # Playwright storage_state (cookies + localStorage). The user-data-dir alone
    # drops *session* cookies on close, so we also persist them here — and a user
    # can drop their own exported storage_state.json here to import a live login.
    return _profile_dir(name) / "storage_state.json"


def _preview_path(name: str) -> Path:
    # Latest screenshot per profile — the approval flow attaches this so the user
    # sees the page before approving an `act`. Kept outside the user-data-dir.
    return _data_dir() / "browser" / "last" / f"{name}.png"


def _parse_steps(raw: str) -> list[dict]:
    try:
        steps = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--steps is not valid JSON: {exc}") from exc
    if not isinstance(steps, list) or not steps:
        raise ValueError("--steps must be a non-empty JSON array")
    for step in steps:
        if not isinstance(step, dict) or len(step) != 1:
            raise ValueError('each step must be a single-key object, e.g. {"click":"#btn"}')
    return steps


# -- Playwright glue ---------------------------------------------------------


def _settle(page, timeout_ms: int) -> None:
    """Best-effort wait for the page to stop loading (JS-heavy sites)."""
    try:
        page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 10_000))
    except Exception:
        pass  # networkidle never fires on some sites (long-poll/websockets) — fine.


def _run_step(page, step: dict, timeout_ms: int) -> str:
    verb, arg = next(iter(step.items()))
    if verb == "click":
        page.click(arg, timeout=timeout_ms)
        return f"click {arg}"
    if verb == "fill":
        sel, val = arg
        page.fill(sel, val, timeout=timeout_ms)
        return f"fill {sel}"
    if verb == "select":
        sel, val = arg
        page.select_option(sel, val, timeout=timeout_ms)
        return f"select {sel}={val}"
    if verb == "press":
        sel, key = arg
        page.press(sel, key, timeout=timeout_ms)
        return f"press {sel} {key}"
    if verb == "wait":
        if isinstance(arg, int | float):
            page.wait_for_timeout(arg)
            return f"wait {arg}ms"
        page.wait_for_selector(arg, timeout=timeout_ms)
        return f"wait {arg}"
    if verb == "goto":
        page.goto(arg, timeout=timeout_ms, wait_until="domcontentloaded")
        return f"goto {arg}"
    raise ValueError(f"unknown step verb: {verb!r}")


class _Session:
    """A Chromium page bound to a persistent profile (or a CDP sidecar)."""

    def __init__(self, profile: str, headless: bool):
        self.profile = profile
        self.headless = headless
        self.is_cdp = False
        self._pw = None
        self._ctx = None
        self._browser = None
        self.page = None

    def __enter__(self):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self.is_cdp = bool(os.environ.get("BROWSER_CDP_URL", "").strip())
        if self.is_cdp:
            # ponytail: CDP profile lives on the sidecar; --profile is advisory here.
            cdp = os.environ["BROWSER_CDP_URL"].strip()
            self._browser = self._pw.chromium.connect_over_cdp(cdp)
            self._ctx = (
                self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
            )
        else:
            user_data_dir = _profile_dir(self.profile) / "udd"
            user_data_dir.mkdir(parents=True, exist_ok=True)
            self._ctx = self._pw.chromium.launch_persistent_context(
                str(user_data_dir),
                headless=self.headless,
                user_agent=_DESKTOP_UA,
                viewport={"width": 1280, "height": 800},
            )
            self._restore_session()
        self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        return self

    def _restore_session(self) -> None:
        """Re-add saved cookies (incl. session cookies the user-data-dir drops)."""
        state = _state_file(self.profile)
        if not state.exists():
            return
        try:
            cookies = json.loads(state.read_text()).get("cookies", [])
            if cookies:
                self._ctx.add_cookies(cookies)
        except Exception:
            pass  # corrupt/foreign state file — fall back to whatever the udd has.

    def __exit__(self, *exc):
        try:
            if not self.is_cdp and self._ctx:
                # Persist cookies + localStorage (captures session cookies too).
                try:
                    self._ctx.storage_state(path=str(_state_file(self.profile)))
                except Exception:
                    pass
            if self.is_cdp:
                if self._browser:
                    self._browser.close()
            elif self._ctx:
                self._ctx.close()
        finally:
            if self._pw:
                self._pw.stop()

    def goto(self, url: str, timeout_ms: int):
        self.page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
        _settle(self.page, timeout_ms)

    def snapshot(self, dest: Path, full_page: bool = True) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(dest), full_page=full_page)
        # Mirror to the per-profile preview the approval flow reads.
        preview = _preview_path(self.profile)
        preview.parent.mkdir(parents=True, exist_ok=True)
        preview.write_bytes(dest.read_bytes())
        return dest


# -- Verbs -------------------------------------------------------------------


def cmd_read(args) -> dict:
    profile = _validate_profile(args.profile)
    with _Session(profile, args.headless) as s:
        s.goto(args.url, args.timeout)
        text = s.page.inner_text("body")
        return {"url": s.page.url, "title": s.page.title(), "text": text.strip()}


def cmd_screenshot(args) -> dict:
    profile = _validate_profile(args.profile)
    if args.output:
        dest = Path(args.output)
    else:
        # Default to ~/Downloads with a spottable name so a REPL user can find it.
        host = urlparse(args.url).netloc or "page"
        dest = Path.home() / "Downloads" / f"clio-{host}-{int(time.time())}.png"
    with _Session(profile, args.headless) as s:
        s.goto(args.url, args.timeout)
        s.snapshot(dest, full_page=args.full_page)
        return {"url": s.page.url, "title": s.page.title(), "path": str(dest)}


def cmd_act(args) -> dict:
    profile = _validate_profile(args.profile)
    steps = _parse_steps(args.steps)
    done: list[str] = []
    with _Session(profile, args.headless) as s:
        s.goto(args.url, args.timeout)
        for step in steps:
            done.append(_run_step(s.page, step, args.timeout))
        _settle(s.page, args.timeout)
        dest = _data_dir() / "browser" / "screenshots" / f"{profile}-{int(time.time())}.png"
        s.snapshot(dest, full_page=True)
        return {"url": s.page.url, "title": s.page.title(), "steps": done, "screenshot": str(dest)}


# -- explore: one process, one live browser, deepseek drives the loop ---------
#
# The other verbs are one-shot: a fresh process re-navigates every call, so an
# outer agent that wants to "look, decide, click, look again" ping-pongs and
# re-loads the page each round. `explore` instead keeps ONE browser open and
# runs an inner LLM loop (the project's own cheap model, vision OFF) that
# observes the page as indexed elements and issues ONE action per step until it
# reports `done`. Built for openai-compatible providers (deepseek default).

# JS: tag every visible, enabled interactive element with data-bu-idx and return
# a compact [{idx, tag, type, label}] list. Selectors are then [data-bu-idx="N"].
_ENUM_JS = """
() => {
  const sel = 'a,button,input,textarea,select,[role=button],[role=link],[onclick]';
  const out = []; let i = 0;
  for (const el of document.querySelectorAll(sel)) {
    if (!el.getClientRects().length) continue;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none') continue;
    if (el.disabled) continue;
    el.setAttribute('data-bu-idx', i);
    const label = (el.innerText || el.value || el.getAttribute('placeholder') ||
      el.getAttribute('aria-label') || el.name || '').trim().replace(/\\s+/g, ' ').slice(0, 80);
    out.push({idx: i, tag: el.tagName.toLowerCase(), type: el.getAttribute('type') || '', label});
    i++;
  }
  return out;
}
"""

_EXPLORE_SYSTEM = """You are a web-automation agent driving a real browser.
Each turn you receive the current page: its URL, title, a text excerpt, and a
numbered list of interactive elements. Achieve the user's TASK by calling
`browser_action` exactly ONCE per turn.
Actions: click(index), fill(index,text), select(index,text), goto(url),
scroll(text="down"|"up"), done(answer).
Element indices are reassigned after every action — always use the indices from
the LATEST page state, never an old one. Work efficiently; steps are limited.
When the task is complete (or you have the answer), call done with the result in
`answer`."""

_ACTION_TOOL = {
    "name": "browser_action",
    "description": "Take exactly one action on the page, or finish with done.",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["click", "fill", "select", "goto", "scroll", "done"],
            },
            "index": {"type": "integer", "description": "target element index"},
            "text": {"type": "string", "description": "text to fill / option / scroll dir"},
            "url": {"type": "string", "description": "url for goto"},
            "answer": {"type": "string", "description": "final result when action=done"},
        },
        "required": ["action"],
    },
}


def _format_state(url: str, title: str, excerpt: str, elements: list[dict]) -> str:
    lines = [f"URL: {url}", f"TITLE: {title}", "", "EXCERPT:", excerpt.strip()[:800]]
    lines += ["", "ELEMENTS:"]
    for e in elements:
        t = f"{e['tag']}" + (f"({e['type']})" if e.get("type") else "")
        lines.append(f"[{e['idx']}] {t} {e.get('label', '')!r}")
    if not elements:
        lines.append("(none found)")
    return "\n".join(lines)


def _apply_action(page, action: dict, timeout_ms: int) -> str:
    """Execute one model action against the live page. Returns a short result note."""
    verb = action.get("action")
    idx = action.get("index")
    sel = f'[data-bu-idx="{idx}"]'
    if verb == "click":
        page.click(sel, timeout=timeout_ms)
        return f"clicked [{idx}]"
    if verb == "fill":
        page.fill(sel, action.get("text", ""), timeout=timeout_ms)
        return f"filled [{idx}]"
    if verb == "select":
        page.select_option(sel, action.get("text", ""), timeout=timeout_ms)
        return f"selected [{idx}]"
    if verb == "goto":
        page.goto(action.get("url", ""), timeout=timeout_ms, wait_until="domcontentloaded")
        return f"went to {action.get('url')}"
    if verb == "scroll":
        page.mouse.wheel(0, -800 if action.get("text", "").startswith("up") else 800)
        return "scrolled"
    raise ValueError(f"unknown action: {verb!r}")


def _load_agent_config():
    """Pull the agent's LLM config from the same store the app uses."""
    import asyncio

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from core.config_store import ConfigStore

    store = ConfigStore(str(_data_dir() / "config.db"))
    return asyncio.run(store.export_to_config()).agent


class _Aio:
    """Run coroutines on a persistent background loop.

    Playwright's sync API holds an event loop on the main thread, so `asyncio.run`
    there raises "cannot be called from a running event loop". One long-lived loop
    in its own thread sidesteps that and keeps the AsyncOpenAI client bound to a
    single loop across calls.
    """

    def __init__(self):
        import asyncio
        import threading

        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()

    def run(self, coro):
        import asyncio

        return asyncio.run_coroutine_threadsafe(coro, self.loop).result()

    def close(self):
        self.loop.call_soon_threadsafe(self.loop.stop)


def cmd_explore(args) -> dict:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from core.llm import LLMClient

    profile = _validate_profile(args.profile)
    agent_cfg = _load_agent_config()
    llm = LLMClient.from_agent_config(agent_cfg)
    model = agent_cfg.model
    aio = _Aio()

    def observe(page) -> str:
        elements = page.evaluate(_ENUM_JS)
        excerpt = page.inner_text("body")
        return _format_state(page.url, page.title(), excerpt, elements)

    trail: list[str] = []
    try:
        with _Session(profile, args.headless) as s:
            s.goto(args.url, args.timeout)
            messages: list[dict] = [
                {"role": "user", "content": f"TASK: {args.task}\n\n{observe(s.page)}"}
            ]
            answer = None
            for _ in range(args.max_steps):
                resp = aio.run(
                    llm.generate(
                        model=model,
                        system=_EXPLORE_SYSTEM,
                        messages=messages,
                        tools=[_ACTION_TOOL],
                        max_tokens=1024,
                    )
                )
                if not resp.tool_calls:
                    answer = resp.text  # model answered in prose — treat as done
                    break
                call = resp.tool_calls[0]
                action = call.arguments or {}
                # openai-compatible assistant turn carrying exactly one tool_call, so
                # the required tool-result message pairs up cleanly. ponytail: deepseek
                # path only; anthropic tool threading differs (this verb is deepseek).
                messages.append(
                    {
                        "role": "assistant",
                        "content": resp.text or "",
                        "tool_calls": [
                            {
                                "id": call.id,
                                "type": "function",
                                "function": {"name": call.name, "arguments": json.dumps(action)},
                            }
                        ],
                    }
                )
                if action.get("action") == "done":
                    answer = action.get("answer", "")
                    trail.append("done")
                    break
                try:
                    note = _apply_action(s.page, action, args.timeout)
                except Exception as exc:
                    note = f"error: {type(exc).__name__}: {exc}"
                trail.append(note)
                _settle(s.page, args.timeout)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": f"{note}\n\n{observe(s.page)}",
                    }
                )

            host = urlparse(s.page.url).netloc or "page"
            shot = Path.home() / "Downloads" / f"clio-{host}-{int(time.time())}.png"
            s.snapshot(shot, full_page=True)
            return {
                "task": args.task,
                "answer": answer,
                "done": answer is not None,
                "steps": trail,
                "url": s.page.url,
                "screenshot": str(shot),
            }
    finally:
        aio.close()


def cmd_profiles(_args) -> dict:
    """List saved profiles with a rough 'authenticated' hint (has a cookies DB)."""
    root = _data_dir() / "browser" / "profiles"
    out = []
    if root.exists():
        for d in sorted(p for p in root.iterdir() if p.is_dir()):
            state = d / "storage_state.json"
            authed = False
            if state.exists():
                try:
                    authed = bool(json.loads(state.read_text()).get("cookies"))
                except Exception:
                    authed = False
            out.append(
                {
                    "name": d.name,
                    # ponytail: "logged in" approximated by having saved cookies.
                    "authenticated": authed,
                    "updated": int(state.stat().st_mtime)
                    if state.exists()
                    else int(d.stat().st_mtime),
                }
            )
    return {"profiles": out}


def main() -> None:
    parser = argparse.ArgumentParser(description="Headless browser automation (Playwright).")
    # Default headless from the env injected by core/tools.py (config.tools.browser).
    headless_default = os.environ.get("BROWSER_HEADLESS", "1") != "0"
    parser.add_argument(
        "--headless", dest="headless", action="store_true", default=headless_default
    )
    parser.add_argument("--headed", dest="headless", action="store_false")
    parser.add_argument("--timeout", type=int, default=30_000, help="per-action timeout (ms)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_read = sub.add_parser("read", help="Load a page, return readable text")
    p_read.add_argument("--url", required=True)
    p_read.add_argument("--profile", default="default")

    p_shot = sub.add_parser("screenshot", help="Load a page, save a PNG")
    p_shot.add_argument("--url", required=True)
    p_shot.add_argument("--profile", default="default")
    p_shot.add_argument("-o", "--output", help="output PNG path")
    p_shot.add_argument("--full-page", action="store_true", default=True)
    p_shot.add_argument("--viewport-only", dest="full_page", action="store_false")

    p_act = sub.add_parser("act", help="Load a page and run ordered steps")
    p_act.add_argument("--url", required=True)
    p_act.add_argument("--profile", default="default")
    p_act.add_argument("--steps", required=True, help="JSON array of single-key step objects")

    p_exp = sub.add_parser("explore", help="LLM-driven loop: one live browser, act until done")
    p_exp.add_argument("--url", required=True)
    p_exp.add_argument("--task", required=True, help="what to accomplish on the site")
    p_exp.add_argument("--profile", default="default")
    p_exp.add_argument("--max-steps", type=int, default=12, dest="max_steps")

    sub.add_parser("profiles", help="List saved profiles + auth hint")

    args = parser.parse_args()
    handlers = {
        "read": cmd_read,
        "screenshot": cmd_screenshot,
        "act": cmd_act,
        "explore": cmd_explore,
        "profiles": cmd_profiles,
    }
    try:
        result = handlers[args.command](args)
        print(json.dumps(result, indent=2, default=str))
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)
    except Exception as exc:  # Playwright errors, navigation failures, etc.
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
