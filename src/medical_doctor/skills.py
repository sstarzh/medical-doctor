"""Loader for Agent Skills (SKILL.md folders).

The same skill folders are consumed by three runtimes:
  * this MCP server (exposed as resources/prompts and injected into consults),
  * Claude Code / Claude Agent SDK (copy to .claude/skills/),
  * OpenClaw (copy to ~/.openclaw/workspace/skills/ or install from ClawHub).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    path: Path
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def full_text(self) -> str:
        f = freshness(self)
        stamp = f"Last reviewed {f['last_reviewed'] or 'never'}"
        if f["status"] != "current":
            stamp += " — REVIEW OVERDUE: confirm recommendations against current guidelines before relying on them"
        return f"# Skill: {self.name}\n_{self.description}_\n_{stamp}._\n\n{self.body.strip()}\n"


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
    md = meta.get("metadata") or {}
    if not isinstance(md, dict):
        raise ValueError(f"{path}: metadata must be a mapping")
    return Skill(name=name, description=desc.strip(), body=body, path=path, metadata={str(k): str(v) for k, v in md.items()})


# --- guideline freshness ------------------------------------------------------

GUIDELINE_MAX_AGE_YEARS = 5
_GUIDELINE_CITE = re.compile(
    r"\b((?:[A-Z][A-Za-z]*[/&-])*(?:AAP|ACC|AHA|ACOG|ADA|ATS|IDSA|PIDS|USPSTF|CDC|NICE|WHO|GOLD|GINA|KDIGO|ESC|"
    r"NASPGHAN|ESPGHAN|ACIP|NIAID|Fleischner|AGS|ACR|AAN|ACG|HFSA|ESH|ISH|NAEPP|SCCM|ILAE|APA|AASLD)(?:[/&-][A-Z][A-Za-z]*)*)"
    r"[^\n.;()]{0,40}?\(?((?:19|20)\d{2})\)?"
)


def cited_guidelines(text: str) -> list[tuple[str, int]]:
    """(body, year) for guideline citations such as 'AAP 2022' or 'ACC/AHA/HFSA heart failure guideline (2022)'."""
    return sorted({(m.group(1), int(m.group(2))) for m in _GUIDELINE_CITE.finditer(text)}, key=lambda x: (x[1], x[0]))


def freshness(skill: Skill, today: date | None = None) -> dict[str, object]:
    today = today or date.today()
    reviewed = skill.metadata.get("last_reviewed")
    interval = int(skill.metadata.get("review_interval_days", "365"))
    try:
        reviewed_on = date.fromisoformat(reviewed) if reviewed else None
    except ValueError:
        reviewed_on = None
    age = (today - reviewed_on).days if reviewed_on else None
    old = [
        {"guideline": g, "year": y}
        for g, y in cited_guidelines(skill.body)
        if today.year - y > GUIDELINE_MAX_AGE_YEARS
    ]
    return {
        "skill": skill.name,
        "last_reviewed": reviewed_on.isoformat() if reviewed_on else None,
        "days_since_review": age,
        "status": "never_reviewed" if age is None else ("review_due" if age > interval else "current"),
        "clinical_review": skill.metadata.get("clinical_review", "pending"),
        "older_guidelines": old,  # cited editions over GUIDELINE_MAX_AGE_YEARS old: check for an update
    }


def freshness_report(today: date | None = None) -> dict[str, object]:
    rows = [freshness(s, today) for s in load_skills().values()]
    return {
        "skills": rows,
        "review_due": [r["skill"] for r in rows if r["status"] != "current"],
        "pending_clinical_review": [r["skill"] for r in rows if r["clinical_review"] != "approved"],
        "older_guideline_citations": sum(len(r["older_guidelines"]) for r in rows),  # type: ignore[arg-type]
    }


@lru_cache(maxsize=1)
def load_skills() -> dict[str, Skill]:
    root = skills_dir()
    skills = [parse_skill(p) for p in sorted(root.glob("*/SKILL.md"))]
    return {s.name: s for s in skills}


def skill_library_text() -> str:
    """All skills concatenated in a stable order (cache-friendly system prompt)."""
    return "\n\n---\n\n".join(s.full_text for s in load_skills().values())


_WORD = re.compile(r"[a-z]{4,}")


def select_skills(text: str, k: int = 6, always: tuple[str, ...] = ()) -> list[str]:
    """Pick the most relevant skills for a case by keyword overlap (for models without a large,
    cheaply cached context). `always` skills are included first."""
    skills = load_skills()
    terms = set(_WORD.findall(text.lower()))
    scored = []
    for s in skills.values():
        if s.name in always:
            continue
        head = set(_WORD.findall((s.name.replace("-", " ") + " " + s.description).lower()))
        body = set(_WORD.findall(s.body.lower()))
        score = 4 * len(terms & head) + len(terms & body)
        scored.append((score, s.name))
    scored.sort(reverse=True)
    return [n for n in always if n in skills] + [n for score, n in scored[:k] if score > 0]
