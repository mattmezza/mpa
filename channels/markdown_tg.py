"""Convert common Markdown to Telegram-flavored HTML.

LLMs emit Markdown (`**bold**`, `*italic*`, fenced code, ...). Telegram renders a
small HTML subset, not Markdown. This module does a deterministic conversion so
agent responses display formatted instead of showing raw markup.

Supported Telegram HTML tags: b, i, u, s, code, pre, a, blockquote, tg-spoiler.
Anything not mappable is left as escaped text.
"""

from __future__ import annotations

import re

__all__ = ["to_telegram_html"]

_FENCE_RE = re.compile(r"```(\w+)?\n?(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+?)`")
_BOLD_RE = re.compile(r"(?<!\*)\*\*(?!\s)(.+?)(?<!\s)\*\*(?!\*)", re.DOTALL)
_BOLD_ALT_RE = re.compile(r"(?<!_)__(?!\s)(.+?)(?<!\s)__(?!_)", re.DOTALL)
_ITALIC_RE = re.compile(r"(?<![\*\w])\*(?!\s)([^\*\n]+?)(?<!\s)\*(?![\*\w])")
_ITALIC_ALT_RE = re.compile(r"(?<![_\w])_(?!\s)([^_\n]+?)(?<!\s)_(?![_\w])")
_STRIKE_RE = re.compile(r"~~(?!\s)(.+?)(?<!\s)~~", re.DOTALL)
_LINK_RE = re.compile(r"\[([^\]]+?)\]\((https?://[^\s)]+)\)")
_HEADER_RE = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t]*#*$", re.MULTILINE)
_BULLET_RE = re.compile(r"^([ \t]*)[\*\-][ \t]+", re.MULTILINE)

_PLACEHOLDER = "\x00{}\x00"


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def to_telegram_html(text: str) -> str:
    """Render Markdown `text` as Telegram-safe HTML.

    Code spans/blocks are protected before escaping so their contents are never
    interpreted as markup. Returns a string suitable for ``parse_mode="HTML"``.
    """
    stash: list[str] = []

    def _stash(html: str) -> str:
        stash.append(html)
        return _PLACEHOLDER.format(len(stash) - 1)

    # Protect fenced code blocks first.
    def _fence(m: re.Match[str]) -> str:
        body = _esc(m.group(2).rstrip("\n"))
        return _stash(f"<pre>{body}</pre>")

    text = _FENCE_RE.sub(_fence, text)

    # Then inline code spans.
    text = _INLINE_CODE_RE.sub(lambda m: _stash(f"<code>{_esc(m.group(1))}</code>"), text)

    # Escape everything that remains (placeholders carry no special chars).
    text = _esc(text)

    # Block-level: headers -> bold lines, bullets -> •.
    text = _HEADER_RE.sub(lambda m: f"<b>{m.group(1)}</b>", text)
    text = _BULLET_RE.sub(r"\1• ", text)

    # Links before emphasis so bracket/paren text isn't mangled.
    text = _LINK_RE.sub(lambda m: _stash(f'<a href="{_esc(m.group(2))}">{m.group(1)}</a>'), text)

    # Inline emphasis. Bold before italic so `**` isn't eaten by the `*` rule.
    text = _BOLD_RE.sub(r"<b>\1</b>", text)
    text = _BOLD_ALT_RE.sub(r"<b>\1</b>", text)
    text = _STRIKE_RE.sub(r"<s>\1</s>", text)
    text = _ITALIC_RE.sub(r"<i>\1</i>", text)
    text = _ITALIC_ALT_RE.sub(r"<i>\1</i>", text)

    # Restore protected spans.
    for i, html in enumerate(stash):
        text = text.replace(_PLACEHOLDER.format(i), html)

    return text
