"""Tests for SkillsEngine."""

from __future__ import annotations

import pytest

from core.skills import SkillsEngine


@pytest.mark.asyncio
async def test_get_index_block_empty_db(tmp_path) -> None:
    db_path = str(tmp_path / "skills.db")
    engine = SkillsEngine(db_path=db_path, seed_dir=tmp_path)
    assert await engine.get_index_block() == ""


@pytest.mark.asyncio
async def test_get_index_block_lists_seeded_skills(tmp_path) -> None:
    (tmp_path / "alpha.md").write_text("Alpha skill")
    (tmp_path / "beta.md").write_text("Beta skill")

    db_path = str(tmp_path / "skills.db")
    engine = SkillsEngine(db_path=db_path, seed_dir=tmp_path)
    index = await engine.get_index_block()

    assert "- alpha: Alpha skill" in index
    assert "- beta: Beta skill" in index


@pytest.mark.asyncio
async def test_get_skill_content_reads_seeded_skill(tmp_path) -> None:
    (tmp_path / "memory.md").write_text("# Memory\n\nUse sqlite3.")
    db_path = str(tmp_path / "skills.db")
    engine = SkillsEngine(db_path=db_path, seed_dir=tmp_path)

    content = await engine.get_skill_content("memory")

    assert "Use sqlite3." in content
