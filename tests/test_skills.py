"""Tests for SkillsEngine."""

from __future__ import annotations

from core.skills import SkillsEngine


def test_get_all_skills_empty_dir(tmp_path) -> None:
    engine = SkillsEngine(tmp_path)
    assert engine.get_all_skills() == ""


def test_get_all_skills_includes_wrapped_sections(tmp_path) -> None:
    (tmp_path / "alpha.md").write_text("Alpha skill")
    (tmp_path / "beta.md").write_text("Beta skill")

    engine = SkillsEngine(tmp_path)
    combined = engine.get_all_skills()

    assert '<skill name="alpha">' in combined
    assert '<skill name="beta">' in combined
    assert "Alpha skill" in combined
    assert "Beta skill" in combined
