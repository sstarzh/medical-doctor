"""Loader for Agent Skills (SKILL.md folders).

The same skill folders are consumed by three runtimes:
  * this MCP server (exposed as resources/prompts and injected into consults),
  * Claude Code / Claude Agent SDK (copy to .claude/skills/),
  * OpenClaw (copy to ~/.openclaw/workspace/skills/ or install from ClawHub).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    path: Path

    @property
    def full_text(self) -> str:
        return f"# Skill: {self.name}\n_{self.description}_\n\n{self.body.strip()}\n"


def skills_dir() -> Path:
    """Locate skills: $MEDICAL_DOCTOR_SKILLS_DIR, packaged copy, then repo checkout."""
    env = os.environ.get("MEDICAL_DOCTOR_SKILLS_DIR")
    candidates = [Path(env)] if env else []
    here = Path(__file__).resolve().parent
    candidates += [here / "skills", here.parent.parent / "skills"]
    for c in candidates:
        if c.is_dir():
            return c
    raise FileNotFoundError("No skills directory found; set MEDICAL_DOCTOR_SKILLS_DIR")


def parse_skill(path: Path) -> Skill:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError(f"{path}: missing YAML frontmatter")
    _, fm, body = text.split("---", 2)
    meta = yaml.safe_load(fm) or {}
    name, desc = meta.get("name"), meta.get("description")
    if not name or not desc:
        raise ValueError(f"{path}: frontmatter needs name and description")
    if name != path.parent.name:
        raise ValueError(f"{path}: name '{name}' must match folder '{path.parent.name}'")
    return Skill(name=name, description=desc.strip(), body=body, path=path)


@lru_cache(maxsize=1)
def load_skills() -> dict[str, Skill]:
    root = skills_dir()
    skills = [parse_skill(p) for p in sorted(root.glob("*/SKILL.md"))]
    return {s.name: s for s in skills}


def skill_library_text() -> str:
    """All skills concatenated in a stable order (cache-friendly system prompt)."""
    return "\n\n---\n\n".join(s.full_text for s in load_skills().values())
