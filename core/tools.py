"""Optional external CLI tools the agent can use (e.g. the GitHub `gh` CLI).

Tools are configured under ``config.tools.*``.  When a tool is *enabled*, two
things happen:

1. Its authentication is wired into the executor environment (via :func:`tool_env`),
   so the underlying CLI is authenticated when the agent runs it.
2. It is advertised to the LLM in the system prompt (via :func:`active_tool_prompts`),
   so the model knows the capability exists and how to use it.

A tool that is *not* enabled is neither authenticated nor advertised, keeping the
prompt lean and the capability hidden.

Adding a new tool means: add a config sub-model in ``core/config.py``, then add an
entry to ``_REGISTRY`` below describing its env + prompt advertisement.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.config import Config

if TYPE_CHECKING:
    from core.personae import Persona

# Advertisement injected into the system prompt when `gh` is active.
_GH_PROMPT = """<tool name="gh">
The GitHub CLI `gh` is installed and authenticated. Run it with the `run_command`
tool for GitHub operations. Read operations run without asking; creating issues,
PRs or releases ask for confirmation first.
Examples:
  gh issue list --repo owner/name
  gh pr view 123 --repo owner/name
  gh pr list --repo owner/name --state open
  gh api repos/owner/name/commits
  gh search issues "is:open label:bug" --repo owner/name
Always pass `--repo owner/name` unless the working directory is a checkout of the
target repository. Use `-o json` / `gh api` and parse JSON when you need fields.
</tool>"""


# Advertisement injected into the system prompt when `browser` is active.
_BROWSER_PROMPT = """<tool name="browser">
A headless browser (`/app/tools/browser.py`, Playwright) is available via `run_command`
for JS-heavy pages and acting on the user's behalf. Prefer an existing API/CLI
over the browser whenever one exists — it is a last resort.
Verbs (always pass `--url`; add `--profile NAME` to reuse a logged-in session):
  python3 /app/tools/browser.py read --url URL                  # readable page text
  python3 /app/tools/browser.py screenshot --url URL            # save a PNG (path in result)
  python3 /app/tools/browser.py act --url URL --profile P --steps JSON
  python3 /app/tools/browser.py explore --url URL --task "..."  # self-driving loop
  python3 /app/tools/browser.py profiles                        # list saved sessions
PREFER `explore` for ANYTHING interactive — clicking buttons, opening modals/
widgets, multi-step forms, bookings, checkouts, anything inside an iframe. One
browser stays open and an inner loop sees every frame and clicks/types on its
own until done, then returns an answer. It is the ONLY verb that can drive
embedded widgets and payment iframes; `read`/`act` only see the top page and
will fail on them.
CRITICAL: every browser command starts a BRAND-NEW browser that reloads `--url`
from scratch — there is NO shared session or page state between calls. So you
CANNOT do a flow step-by-step across several commands; each call would restart
from the beginning and lose all progress. A whole interactive flow MUST be ONE
explore call that carries the entire task.
CAUTION: `explore` self-drives to completion under a SINGLE approval — there is NO
per-step gate once it starts. Do NOT use it to spend money or submit irreversible
actions on its own: for purchases/payments confirm the exact details with the owner
first, and prefer guided `act` steps (each fill/submit is approved separately).
How to use it well: put the ENTIRE flow in one `--task` as numbered steps with every
value it needs (product, dates, name, email, phone, card details) — it cannot ask
mid-run. It runs for a few minutes and returns ONE JSON `answer`: do NOT treat the wait
as a hang, split the task, retry, or fall back to `read`/`act` (those only see the top
page, never the widget/iframe). Quote the `answer` (and screenshot) back; if it reports
pending/awaiting-approval don't upgrade to "confirmed"; if it returns `done:false` with a
`reason`, report what blocked it and don't blindly re-run.
`read`/`screenshot` run without asking; `act` asks approval each call (shows a screenshot
on chat) and takes `--steps`, a JSON array of single-key objects, e.g.
  [{"fill":["#user","alice"]},{"click":"#login"}]   (fill/click/select/press/wait/goto).
For the full reference — steps syntax, guided first-time login + 2FA, profiles — run
`load_skill browser`.
</tool>"""


# Advertisement injected into the system prompt when `whatsapp` is active (#97).
_WHATSAPP_PROMPT = """<tool name="whatsapp">
WhatsApp is available through the `wacli` CLI — run it with the `run_command` tool.
Read operations (sync, messages, contacts, chats, groups) run without asking;
sending a message asks for confirmation first.
Send a message:
  wacli --json send text --to <jid> --message "..."
Read (sync first whenever checking for new/recent messages):
  wacli --json sync --once --idle-exit 5s
  wacli --json messages list --limit 20
  wacli --json messages search "invoice" --chat <jid>
  wacli --json contacts search "Marco"
JIDs: users are `<phone>@s.whatsapp.net` (digits only, no `+`); groups `<id>@g.us`.
Run `load_skill wacli-whatsapp` for the full command reference.
</tool>"""


@dataclass(frozen=True)
class ToolSpec:
    """Describes an optional external tool the agent can use."""

    key: str  # config sub-key under `tools.` (e.g. "gh")
    label: str  # human-friendly name for the admin UI
    summary: str  # one-line description for the admin UI
    # Returns env vars to inject when the tool is enabled (auth, etc.).
    env: Callable[[Config], dict[str, str]]
    # Returns the system-prompt advertisement block for the tool.
    prompt: Callable[[Config], str]


def _gh_env(config: Config) -> dict[str, str]:
    gh = config.tools.gh
    if not gh.enabled:
        return {}
    # GitHub App takes precedence over the PAT (#111): mint a short-lived
    # installation token so `gh` acts as the bot identity with its own rate limit.
    if gh.app_id and gh.installation_id and gh.private_key:
        from core import github_app

        token = github_app.installation_token(gh.app_id, gh.installation_id, gh.private_key)
        if token:
            return {"GH_TOKEN": token}
        # App configured but the mint failed → fall back to the PAT if present.
    if gh.token:
        # `gh` reads GH_TOKEN (preferred) / GITHUB_TOKEN for non-interactive auth.
        return {"GH_TOKEN": gh.token}
    return {}


def _gh_app_configured(config: Config) -> bool:
    gh = config.tools.gh
    return bool(gh.app_id and gh.installation_id and gh.private_key)


def _whatsapp_env(config: Config) -> dict[str, str]:
    wa = config.tools.whatsapp
    if not wa.enabled:
        return {}
    # Identity knobs for the wacli store. Per-persona overrides ride #93's
    # per-persona tool_env on top of these defaults.
    env: dict[str, str] = {}
    if wa.store:
        env["WACLI_STORE"] = wa.store
    if wa.device_label:
        env["WACLI_DEVICE_LABEL"] = wa.device_label
    return env


def _browser_env(config: Config) -> dict[str, str]:
    browser = config.tools.browser
    if not browser.enabled:
        return {}
    env = {"BROWSER_HEADLESS": "1" if browser.headless else "0"}
    if browser.cdp_url:
        env["BROWSER_CDP_URL"] = browser.cdp_url
    if browser.user_agent:
        env["BROWSER_USER_AGENT"] = browser.user_agent
    return env


_REGISTRY: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="gh",
        label="GitHub CLI (gh)",
        summary="Let the agent query and act on GitHub (issues, PRs, repos, API).",
        env=_gh_env,
        prompt=lambda _cfg: _GH_PROMPT,
    ),
    ToolSpec(
        key="browser",
        label="Browser automation",
        summary="Let the agent read JS-heavy pages and act on sites via a headless browser.",
        env=_browser_env,
        prompt=lambda _cfg: _BROWSER_PROMPT,
    ),
    ToolSpec(
        key="whatsapp",
        label="WhatsApp (wacli)",
        summary="Let the agent read and send WhatsApp messages via the local wacli CLI.",
        env=_whatsapp_env,
        prompt=lambda _cfg: _WHATSAPP_PROMPT,
    ),
)


# Env vars the tool registry manages. When a per-persona env override is applied
# (#93), any managed key absent from the override is stripped from the inherited
# process environment too — so a persona that switched `gh` off can never inherit
# a token that leaked in via `.env`/Docker ENV and act as the owner. `gh` reads
# GH_TOKEN, then GITHUB_TOKEN, then the enterprise variants (in that precedence),
# so ALL of them must be stripped, not just GH_TOKEN.
MANAGED_TOOL_ENV_KEYS: frozenset[str] = frozenset(
    {
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GH_ENTERPRISE_TOKEN",
        "GITHUB_ENTERPRISE_TOKEN",
        "BROWSER_HEADLESS",
        "BROWSER_CDP_URL",
        "BROWSER_USER_AGENT",
        "BROWSER_PROFILE",
    }
)


def registry() -> tuple[ToolSpec, ...]:
    """Return all known optional tools."""
    return _REGISTRY


def _is_enabled(config: Config, key: str) -> bool:
    sub = getattr(config.tools, key, None)
    return bool(getattr(sub, "enabled", False))


def active_tool_prompts(config: Config, persona: Persona | None = None) -> list[str]:
    """Return system-prompt advertisement blocks for every *enabled* tool.

    When ``persona`` is active and has per-tool config (#93), a tool it opted out
    of is dropped, and a note about its own identity (own ``gh`` token, own browser
    profile) is injected into the tool block so the model authenticates as itself.
    """
    blocks: list[str] = []
    for spec in _REGISTRY:
        if not _is_enabled(config, spec.key):
            continue
        setting = persona.tool_setting(spec.key) if persona else None
        if setting is not None and not setting.get("enabled"):
            continue  # this persona has the tool switched off
        block = spec.prompt(config).strip()
        if not block:
            continue
        note = _persona_tool_note(spec.key, setting, persona)
        if note:
            block = block.replace("</tool>", f"{note}\n</tool>", 1)
        blocks.append(block)
    return blocks


def tool_env(config: Config) -> dict[str, str]:
    """Return the merged environment for every *enabled* tool (auth tokens, etc.)."""
    env: dict[str, str] = {}
    for spec in _REGISTRY:
        if _is_enabled(config, spec.key):
            env.update(spec.env(config))
    return env


# -- Per-persona tool identity (#93) ----------------------------------------


def gh_token_secret_name(persona_name: str) -> str:
    """Infra-vault name holding a persona's own GitHub token.

    Namespaced per persona so each agent authenticates as a distinct GitHub user.
    Stored in the *infra* vault (machine-key, boot-unsealed) so it works headless
    and in scheduled jobs — same on-disk posture as the system-wide ``GH_TOKEN``.

    The slug is kept verbatim (case preserved; infra names are case-sensitive) so
    two personae whose slugs differ only by case don't collide on one token.
    """
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", (persona_name or "").strip())
    return f"GH_TOKEN_{slug}"


# Matches the explicit `--repo owner/name` / `-R owner/name` target flag.
_GH_REPO_FLAG_RE = re.compile(r"(?:--repo[=\s]+|-R[=\s]+)([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)")


def github_repo_violation(persona: Persona | None, command: str) -> str | None:
    """First GitHub repo the persona is NOT allowed to touch, or ``None`` (#111).

    Best-effort per-persona repo allowlist from ``tool_config["gh"]["repos"]``
    (empty/absent = unrestricted). Only the explicit ``--repo owner/name`` /
    ``-R owner/name`` flag is inspected — the documented way to target a repo.
    ponytail: the HARD boundary is the GitHub App installation's own repo
    selection (server-enforced); this is defense-in-depth, so it deliberately
    does NOT parse cwd checkouts, `gh api` paths, or git remotes — a parser for
    those is a bug farm and would give false confidence. Tighten the App
    installation to narrow access for real.
    """
    if persona is None or not command:
        return None
    gh = persona.tool_setting("gh") or {}
    allowed = {r.strip().lower() for r in (gh.get("repos") or []) if r and r.strip()}
    if not allowed:
        return None
    for repo in _GH_REPO_FLAG_RE.findall(command):
        if repo.lower() not in allowed:
            return repo
    return None


def _persona_tool_note(key: str, setting: dict | None, persona: Persona | None) -> str:
    """A one-line identity note injected into a tool's prompt block for a persona."""
    if persona is None or setting is None:
        return ""
    if key == "gh":
        repos = [r for r in (setting.get("repos") or []) if str(r).strip()]
        repo_note = (
            f" You may only target these repos with `--repo`: {', '.join(repos)}." if repos else ""
        )
        if (setting.get("token_secret") or "").strip():
            return (
                f'Running as persona "{persona.name}": `gh` is authenticated with the '
                "GitHub token configured for this persona. Every gh/git action appears "
                "as that token's GitHub user." + repo_note
            )
        return (
            f'Running as persona "{persona.name}": `gh` is authenticated with this '
            "persona's OWN GitHub identity (a distinct identity from the owner). Every "
            "gh/git action appears as this persona's GitHub user or the configured "
            "GitHub App bot." + repo_note
        )
    if key == "browser":
        profile = (setting.get("profile") or persona.name).strip()
        if profile:
            return (
                f'Running as persona "{persona.name}": your browser profile is '
                f'"{profile}" — it is used by default, so your logged-in sessions are '
                "isolated from other personae. Pass `--profile " + profile + "` "
                "explicitly when a command needs it."
            )
    return ""


def effective_tool_env(
    config: Config,
    persona: Persona | None,
    resolve_secret: Callable[[str], str | None],
) -> dict[str, str]:
    """The tool environment for a turn, adjusted for the active persona (#93).

    Starts from the system-wide :func:`tool_env` and, for a persona that has its
    own tool config, swaps in its own identity:

    * ``gh`` — replace ``GH_TOKEN`` with the persona's own token (resolved from the
      infra vault). If the persona switched ``gh`` off, ``GH_TOKEN`` is *removed*
      so it can never act as the owner; if it has no own token, it also falls back
      to no token rather than silently borrowing the owner's.
    * ``browser`` — set ``BROWSER_PROFILE`` to the persona's isolated profile.

    A persona with no entry for a tool inherits the system config unchanged, so
    existing setups keep working (migration §6).
    """
    env = tool_env(config)
    if persona is None:
        return env

    gh = persona.tool_setting("gh")
    if gh is not None:
        # Persona has an explicit gh policy → never inherit the owner's token.
        env.pop("GH_TOKEN", None)
        if gh.get("enabled") and config.tools.gh.enabled:
            # ``token_secret`` lets a persona reuse an existing infra-vault secret
            # (e.g. the system GH_TOKEN, or a shared PAT) instead of storing its own
            # copy; otherwise its own namespaced token is used (#93).
            name = (gh.get("token_secret") or "").strip() or gh_token_secret_name(persona.name)
            token = resolve_secret(name)
            if not token and _gh_app_configured(config):
                # No own PAT, but a GitHub App is configured → use the shared bot
                # identity (#111). Mint it directly (App-or-nothing) rather than via
                # _gh_env: a persona must NEVER fall back to the owner's PAT, which
                # is exactly what #93's no-borrow rule prevents.
                from core import github_app

                gh_cfg = config.tools.gh
                token = github_app.installation_token(
                    gh_cfg.app_id, gh_cfg.installation_id, gh_cfg.private_key
                )
            if token:
                env["GH_TOKEN"] = token

    browser = persona.tool_setting("browser")
    if browser is not None and browser.get("enabled") and config.tools.browser.enabled:
        profile = (browser.get("profile") or persona.name).strip()
        if profile:
            env["BROWSER_PROFILE"] = profile

    return env


if __name__ == "__main__":
    # ponytail: one runnable check covering per-persona env swap + prompt notes.
    from core.personae import Persona

    cfg = Config()
    cfg.tools.gh.enabled = True
    cfg.tools.gh.token = "owner-token"
    cfg.tools.browser.enabled = True

    assert gh_token_secret_name("coding-helper") == "GH_TOKEN_coding-helper"
    assert gh_token_secret_name("Hopper") != gh_token_secret_name("hopper")  # case-distinct

    vault = {"GH_TOKEN_hopper": "hopper-token", "SHARED_PAT": "shared-token"}
    resolve = vault.get  # Callable[[str], str | None]

    # No persona → system token, no profile.
    base = effective_tool_env(cfg, None, resolve)
    assert base["GH_TOKEN"] == "owner-token" and "BROWSER_PROFILE" not in base

    # Persona with no tool_config → inherits system config unchanged (migration).
    plain = Persona(name="plain")
    assert effective_tool_env(cfg, plain, resolve)["GH_TOKEN"] == "owner-token"

    # Persona with its own gh token + browser profile → own identity.
    hopper = Persona(
        name="hopper",
        tool_config={"gh": {"enabled": True}, "browser": {"enabled": True, "profile": "hop"}},
    )
    env = effective_tool_env(cfg, hopper, resolve)
    assert env["GH_TOKEN"] == "hopper-token", env.get("GH_TOKEN")
    assert env["BROWSER_PROFILE"] == "hop"

    # gh enabled but no own token stored → no token (never borrows the owner's).
    atlas = Persona(name="atlas", tool_config={"gh": {"enabled": True}})
    assert "GH_TOKEN" not in effective_tool_env(cfg, atlas, resolve)

    # token_secret reuses an existing vault secret instead of the namespaced one.
    ref = Persona(name="atlas", tool_config={"gh": {"enabled": True, "token_secret": "SHARED_PAT"}})
    assert effective_tool_env(cfg, ref, resolve)["GH_TOKEN"] == "shared-token"

    # gh explicitly disabled → GH_TOKEN removed.
    lingua = Persona(name="lingua", tool_config={"gh": {"enabled": False}})
    assert "GH_TOKEN" not in effective_tool_env(cfg, lingua, resolve)

    # GitHub App configured + persona has no own PAT → uses the shared bot token (#111).
    app_cfg = Config()
    app_cfg.tools.gh.enabled = True
    app_cfg.tools.gh.app_id = "42"
    app_cfg.tools.gh.installation_id = "7"
    app_cfg.tools.gh.private_key = "PEM"
    import core.github_app as _ga

    _real_it, _ga.installation_token = _ga.installation_token, lambda *_a: "bot-token"
    try:
        assert effective_tool_env(app_cfg, atlas, resolve)["GH_TOKEN"] == "bot-token"
    finally:
        _ga.installation_token = _real_it

    # Per-persona repo allowlist (#111): only --repo targets outside the list are blocked.
    scoped = Persona(name="coder", tool_config={"gh": {"enabled": True, "repos": ["me/mpa"]}})
    assert github_repo_violation(scoped, "gh issue list --repo me/mpa") is None
    assert github_repo_violation(scoped, "gh pr view 1 -R me/other") == "me/other"
    assert github_repo_violation(scoped, "gh api user") is None  # no --repo → can't tell → allow
    assert github_repo_violation(plain, "gh pr view 1 --repo any/thing") is None  # no allowlist

    # Prompts: hopper sees gh + browser, with identity notes; lingua's gh is hidden.
    hp = "\n".join(active_tool_prompts(cfg, hopper))
    assert 'persona "hopper"' in hp and "OWN GitHub identity" in hp and "hop" in hp
    lp = "\n".join(active_tool_prompts(cfg, lingua))
    assert 'name="gh"' not in lp and 'name="browser"' in lp
    # No persona → plain blocks, no identity notes.
    nop = "\n".join(active_tool_prompts(cfg))
    assert 'name="gh"' in nop and "Running as persona" not in nop

    print("tools.py self-check OK")
