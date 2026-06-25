"""Offline checks for the `explore` verb's pure logic (no browser, no LLM)."""

import pytest

from tools.browser import _apply_action, _format_state


def test_format_state_lists_indexed_elements():
    out = _format_state(
        "https://x.test/",
        "Title",
        "body text",
        [{"idx": 0, "tag": "button", "type": "", "label": "Book"}],
    )
    assert "URL: https://x.test/" in out
    assert "[0] button 'Book'" in out


def test_format_state_handles_empty():
    assert "(none found)" in _format_state("u", "t", "x", [])


class _FakePage:
    def __init__(self):
        self.calls = []

    def click(self, sel, timeout=None):
        self.calls.append(("click", sel))

    def fill(self, sel, text, timeout=None):
        self.calls.append(("fill", sel, text))


def test_apply_action_dispatches_to_indexed_selector():
    page = _FakePage()
    note = _apply_action(page, {"action": "click", "index": 3}, 1000)
    assert page.calls == [("click", '[data-bu-idx="3"]')]
    assert note == "clicked [3]"


def test_apply_action_rejects_unknown_verb():
    with pytest.raises(ValueError):
        _apply_action(_FakePage(), {"action": "teleport"}, 1000)
