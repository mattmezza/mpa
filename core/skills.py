"""Skills engine — loads skill docs from a SQLite-backed store."""

from __future__ import annotations

from pathlib import Path

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS skills (
    name TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    summary TEXT DEFAULT '',
    created_at DATETIME DEFAULT (datetime('now')),
    updated_at DATETIME DEFAULT (datetime('now'))
);
"""


def _extract_summary(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            stripped = stripped.lstrip("#").strip()
        return stripped[:120]
    return ""


class SkillsStore:
    """SQLite-backed store for skill documents."""

    def __init__(self, db_path: str = "data/skills.db", seed_dir: str | Path = "skills/"):
        self.db_path = db_path
        self.seed_dir = Path(seed_dir) if seed_dir else None
        self._ready = False

    async def _ensure_schema(self) -> None:
        if self._ready:
            return
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(_SCHEMA)
        self._ready = True

    async def _count(self) -> int:
        await self._ensure_schema()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM skills")
            row = await cursor.fetchone()
            return int(row[0]) if row else 0

    async def ensure_seeded(self) -> bool:
        """Seed from markdown files if the store is empty."""
        await self._ensure_schema()
        count = await self._count()
        if count > 0:
            return False
        if not self.seed_dir or not self.seed_dir.exists():
            return False

        inserted = 0
        async with aiosqlite.connect(self.db_path) as db:
            for skill_file in sorted(self.seed_dir.glob("*.md")):
                content = skill_file.read_text().strip()
                if not content:
                    continue
                name = skill_file.stem
                summary = _extract_summary(content)
                await db.execute(
                    "INSERT INTO skills (name, content, summary) VALUES (?, ?, ?) "
                    "ON CONFLICT(name) DO UPDATE SET "
                    "content = excluded.content, summary = excluded.summary, "
                    "updated_at = datetime('now')",
                    (name, content, summary),
                )
                inserted += 1
            await db.commit()
        return inserted > 0

    async def list_skills(self) -> list[dict]:
        await self.ensure_seeded()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT name, summary, content, updated_at FROM skills ORDER BY name"
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def get_skill(self, name: str) -> dict | None:
        await self.ensure_seeded()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT name, content, summary, updated_at FROM skills WHERE name = ?",
                (name,),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def upsert_skill(self, name: str, content: str) -> None:
        await self._ensure_schema()
        summary = _extract_summary(content)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO skills (name, content, summary) VALUES (?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET "
                "content = excluded.content, summary = excluded.summary, "
                "updated_at = datetime('now')",
                (name, content, summary),
            )
            await db.commit()

    async def delete_skill(self, name: str) -> bool:
        await self._ensure_schema()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM skills WHERE name = ?", (name,))
            await db.commit()
            return cursor.rowcount > 0


class SkillsEngine:
    """Skill index + lazy loader for LLM usage."""

    def __init__(self, db_path: str = "data/skills.db", seed_dir: str | Path = "skills/"):
        self.store = SkillsStore(db_path=db_path, seed_dir=seed_dir)

    async def get_index_block(self) -> str:
        skills = await self.store.list_skills()
        if not skills:
            return ""
        lines = []
        for skill in skills:
            summary = (skill.get("summary") or "").strip()
            if summary:
                lines.append(f"- {skill['name']}: {summary}")
            else:
                lines.append(f"- {skill['name']}")
        return "\n".join(lines)

    async def get_skill_content(self, name: str) -> str:
        skill = await self.store.get_skill(name)
        if not skill:
            return ""
        return str(skill.get("content", ""))
