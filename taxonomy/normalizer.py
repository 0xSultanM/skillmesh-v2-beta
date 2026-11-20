"""
Skill normalisation: map free-text skill mentions to canonical taxonomy slugs.

The index is built from the database (falling back to the bundled JSON seed
when the table is empty, e.g. before ``seed_taxonomy`` has run) and cached
in-process. It is invalidated by signals whenever a Skill changes.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from django.utils.text import slugify

SEED_PATH = Path(__file__).resolve().parent / "data" / "skills.json"

# Boundaries that work for tokens such as "c++", "c#", ".net", "node.js".
_LEFT = r"(?<![A-Za-z0-9+#.])"
_RIGHT = r"(?![A-Za-z0-9+#]|\.[A-Za-z0-9])"


@dataclass
class SkillInfo:
    slug: str
    name: str
    category: str
    aliases: list[str]
    learn_hours: int
    prerequisites: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)


@dataclass
class Mention:
    slug: str
    alias: str
    start: int
    end: int


class SkillIndex:
    def __init__(self, skills: list[SkillInfo]):
        self.skills: dict[str, SkillInfo] = {s.slug: s for s in skills}
        self.alias_to_slug: dict[str, str] = {}
        for s in skills:
            # Display names of 1-2 chars ("Go", "R") are too ambiguous to match in prose;
            # such skills rely on explicit aliases ("golang", "r programming").
            phrases = {a.lower() for a in s.aliases}
            if len(s.name) > 2:
                phrases.add(s.name.lower())
            for phrase in phrases:
                if phrase.strip():
                    self.alias_to_slug.setdefault(phrase.strip(), s.slug)
        # Exact-slug lookups (used by normalize) without making slugs matchable in prose.
        self._slugs = set(self.skills)
        self._names = {s.name.lower(): s.slug for s in skills}
        # Longest aliases first so "react native" wins over "react".
        aliases = sorted(self.alias_to_slug, key=len, reverse=True)
        self._pattern = re.compile(
            "|".join(f"{_LEFT}{re.escape(a)}{_RIGHT}" for a in aliases), re.IGNORECASE
        ) if aliases else None

    # -- lookups ----------------------------------------------------------
    def get(self, slug: str) -> SkillInfo | None:
        return self.skills.get(slug)

    def normalize(self, raw: str) -> tuple[str, str, bool]:
        """Return ``(slug, display_name, known)`` for a raw skill string."""
        cleaned = re.sub(r"\s+", " ", (raw or "").strip().strip(".,;:")).lower()
        if not cleaned:
            return "", "", False
        if cleaned in self._slugs:
            return cleaned, self.skills[cleaned].name, True
        if cleaned in self._names:  # exact display name, incl. short ones like "Go" / "R"
            slug = self._names[cleaned]
            return slug, self.skills[slug].name, True
        if cleaned in self.alias_to_slug:
            slug = self.alias_to_slug[cleaned]
            return slug, self.skills[slug].name, True
        # Try the phrase inside parentheses removed, e.g. "PostgreSQL (or MySQL)".
        base = re.sub(r"\(.*?\)", "", cleaned).strip()
        if base in self.alias_to_slug:
            slug = self.alias_to_slug[base]
            return slug, self.skills[slug].name, True
        # A single embedded known alias, e.g. "experience with Docker".
        found = {m.slug for m in self.find_mentions(cleaned)}
        if len(found) == 1:
            slug = found.pop()
            return slug, self.skills[slug].name, True
        return f"custom-{slugify(cleaned)[:60]}", raw.strip(), False

    def find_mentions(self, text: str) -> list[Mention]:
        """All non-overlapping skill mentions in ``text`` (longest match wins)."""
        if not text or not self._pattern:
            return []
        out: list[Mention] = []
        for m in self._pattern.finditer(text):
            alias = m.group(0).lower()
            slug = self.alias_to_slug.get(alias)
            if slug:
                out.append(Mention(slug=slug, alias=alias, start=m.start(), end=m.end()))
        return out

    def relation(self, a: str, b: str) -> str | None:
        """How skill ``a`` (held) relates to skill ``b`` (wanted)."""
        if a == b:
            return "exact"
        sa, sb = self.skills.get(a), self.skills.get(b)
        if not sa or not sb:
            return None
        if b in sa.related or a in sb.related:
            return "related"
        if b in sa.prerequisites:
            return "prerequisite"  # held skill builds on wanted one => wanted is likely known
        return None


_lock = threading.Lock()
_index: SkillIndex | None = None


def _load_from_seed() -> list[SkillInfo]:
    data = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    return [SkillInfo(**row) for row in data]


def _load_from_db() -> list[SkillInfo]:
    from .models import Skill

    skills = []
    qs = Skill.objects.prefetch_related("prerequisites", "related")
    for s in qs:
        skills.append(
            SkillInfo(
                slug=s.slug,
                name=s.name,
                category=s.category,
                aliases=list(s.aliases or []),
                learn_hours=s.learn_hours,
                prerequisites=[p.slug for p in s.prerequisites.all()],
                related=[r.slug for r in s.related.all()],
            )
        )
    return skills


def get_index() -> SkillIndex:
    global _index
    if _index is not None:
        return _index
    with _lock:
        if _index is None:
            try:
                skills = _load_from_db()
            except Exception:  # table missing (fresh checkout / tests before migrate)
                skills = []
            _index = SkillIndex(skills or _load_from_seed())
    return _index


def invalidate_index() -> None:
    global _index
    with _lock:
        _index = None
