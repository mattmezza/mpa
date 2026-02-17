"""Skills engine — loads markdown skill files into LLM context."""

from __future__ import annotations

from pathlib import Path


class SkillsEngine:
    """Loads and manages skill files that teach the LLM to use CLI tools."""

    def __init__(self, skills_dir: str | Path = "skills/"):
        self.skills_dir = Path(skills_dir)
        self.skills: dict[str, str] = {}
        self._load_all()

    def _load_all(self) -> None:
        if not self.skills_dir.exists():
            return
        for skill_file in sorted(self.skills_dir.glob("*.md")):
            self.skills[skill_file.stem] = skill_file.read_text()

    def get_all_skills(self) -> str:
        """Concatenate all skills into a single context block."""
        if not self.skills:
            return ""
        sections = []
        for name, content in self.skills.items():
            sections.append(f'<skill name="{name}">\n{content}\n</skill>')
        return "\n\n".join(sections)
