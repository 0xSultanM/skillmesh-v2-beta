"""
Essential topics per skill, mined from public course syllabi (Coursera).

* Course discovery uses Coursera's public course listing (``/courses?query=``),
  which robots.txt allows (``/search`` is disallowed, so we never use it).
* Each course page publishes schema.org ``Course`` JSON-LD with
  ``syllabusSections`` (module name, description, duration) and ``teaches``
  (learning outcomes). We read that structured data only.
* Modules from several courses are normalised, merged when similar and ranked
  by how many courses teach them, so the result reflects what the industry's
  courses consider essential, not one author's outline.

Udemy is intentionally not used: it blocks automated access (HTTP 403) and its
terms forbid scraping. Results are cached per skill in SQLite (``SkillSyllabus``).
"""
from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from urllib.parse import quote_plus

from bs4 import BeautifulSoup
from django.utils import timezone

from analyzer.net import FetchError, safe_get

log = logging.getLogger(__name__)

LISTING = "https://www.coursera.org/courses?query={q}"
COURSE = "https://www.coursera.org/learn/{slug}"
MAX_COURSES = 4
MAX_TOPICS = 10
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130.0 Safari/537.36 SkillMeshBot/0.1"}

GENERIC = re.compile(
    r"^(course )?(introduction|intro|welcome|overview|getting started|summary|conclusion|wrap[- ]?up|final (project|exam|assessment)|"
    r"capstone( project)?|practice (quiz|test|exam)|graded (quiz|assignment)|course (project|summary|conclusion|wrap[- ]?up)|"
    r"next steps|bonus|resources|assessment|quiz|review|final assignment|peer review|course project|project)$", re.I)
DURATION = re.compile(r"(\d+)\s*(hours?|hrs?|minutes?|mins?)\s+to complete", re.I)
PREFIX = re.compile(r"^\s*((module|week|unit|lesson|section|part|chapter)\s*\d+\s*[:.\-–—]?\s*|\d+[.)]\s*)", re.I)


def _minutes(iso: str | None) -> int | None:
    m = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", iso or "")
    if not m:
        return None
    h, mi, s = (int(x or 0) for x in m.groups())
    total = h * 60 + mi + (1 if s >= 30 else 0)
    return total or None


def _first_sentence(text: str, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    m = re.match(r"(.{40,}?[.!?])(\s|$)", text)
    out = m.group(1) if m else text
    return out[:limit].rstrip() + ("…" if len(out) > limit else "")


def clean_topic(name: str) -> str:
    name = PREFIX.sub("", re.sub(r"\s+", " ", name or "")).strip(" -:–—")
    return name[:120]


def find_course_slugs(query: str) -> list[str]:
    page = safe_get(LISTING.format(q=quote_plus(query)), headers=UA, max_bytes=4_000_000, timeout=15)
    if page.status != 200:
        return []
    return list(dict.fromkeys(re.findall(r'href="/learn/([a-z0-9-]+)"', page.text)))[:12]


def parse_course(html: str, url: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        nodes = data.get("@graph", [data]) if isinstance(data, dict) else data
        for n in nodes if isinstance(nodes, list) else []:
            if not isinstance(n, dict) or n.get("@type") != "Course":
                continue
            provider = n.get("provider") or n.get("creator") or {}
            if isinstance(provider, list):
                provider = provider[0] if provider else {}
            sections = []
            for s in n.get("syllabusSections") or []:
                if isinstance(s, dict) and s.get("name"):
                    sections.append({"name": clean_topic(s["name"]), "description": s.get("description", ""),
                                     "minutes": _minutes(s.get("timeRequired"))})
            rating = n.get("aggregateRating") or {}
            return {
                "title": str(n.get("name", ""))[:200], "url": url,
                "provider": str(provider.get("name", "") if isinstance(provider, dict) else "")[:120],
                "level": str(n.get("educationalLevel", ""))[:40],
                "rating": rating.get("ratingValue") if isinstance(rating, dict) else None,
                "teaches": [t.strip() for t in (n.get("teaches") or []) if isinstance(t, str) and t.strip()][:6],
                "skills": [a for a in (n.get("about") or []) if isinstance(a, str)][:12],
                "sections": sections,
            }
    return _parse_course_html(soup, url)


_STOP_H3 = re.compile(r"^(earn a career certificate|instructors?|offered by|why people choose|learner reviews|"
                      r"frequently asked questions|explore more|recommended)", re.I)


def _parse_course_html(soup: BeautifulSoup, url: str) -> dict | None:
    """Fallback for course pages without syllabus JSON-LD: read the visible module list."""
    marker = soup.find(string=re.compile(r"\bmodules? in this course\b", re.I))
    if not marker:
        return None
    sections = []
    for h3 in marker.find_all_next("h3"):
        name = h3.get_text(" ", strip=True)
        if _STOP_H3.match(name) or len(sections) >= 15:
            break
        container = h3.find_parent(["div", "li", "section"])
        desc, minutes = "", None
        if container:
            desc = container.get_text(" ", strip=True).replace(name, "", 1).strip()
            m = DURATION.search(desc)
            if m:
                minutes = int(m.group(1)) * (60 if m.group(2).lower().startswith("h") else 1)
            desc = re.sub(r"^\s*Module\s+\d+\s*[•·-]?\s*", "", DURATION.sub("", desc)).strip(" •·-")
        sections.append({"name": clean_topic(name), "description": desc[:400], "minutes": minutes})
    if not sections:
        return None
    h1 = soup.find("h1")
    teaches = []
    learn_head = soup.find(string=re.compile(r"What you('|&#x27;|’)ll learn", re.I))
    if learn_head:
        ul = learn_head.find_next("ul")
        if ul:
            teaches = [li.get_text(" ", strip=True) for li in ul.find_all("li")][:6]
    return {"title": h1.get_text(" ", strip=True)[:200] if h1 else "", "url": url, "provider": "Coursera",
            "level": "", "rating": None, "teaches": teaches, "skills": [], "sections": sections}


def _fetch_course(slug: str) -> dict | None:
    url = COURSE.format(slug=slug)
    try:
        page = safe_get(url, headers=UA, max_bytes=4_000_000, timeout=15)
    except FetchError:
        return None
    return parse_course(page.text, url) if page.status == 200 else None


def _about_other_skill(name: str, slug: str | None) -> bool:
    """True when a module is really about a different known skill (e.g. Kubernetes in a Docker list)."""
    if not slug:
        return False
    from taxonomy.normalizer import get_index

    idx = get_index()
    found = {m.slug for m in idx.find_mentions(name)}
    if not found or slug in found:
        return False
    info = idx.get(slug)
    close = set(info.prerequisites) if info else set()  # foundations are fine; sibling skills are not
    return not (found & close)


def merge_topics(courses: list[dict], skill_terms: list[str], slug: str | None = None) -> list[dict]:
    """Merge similar modules across courses; rank by how many courses teach them, then by position."""
    merged: list[dict] = []
    for ci, course in enumerate(courses):
        for pos, sec in enumerate(course["sections"]):
            name = sec["name"]
            if not name or GENERIC.match(name) or len(name) < 3 or _about_other_skill(name, slug):
                continue
            key = name.lower()
            hit = next((m for m in merged if SequenceMatcher(None, m["key"], key).ratio() >= 0.78), None)
            if hit:
                if ci not in hit["_courses"]:
                    hit["_courses"].add(ci)
                    hit["sources"].append({"course": course["title"], "url": course["url"], "provider": course["provider"]})
                hit["_pos"] = min(hit["_pos"], pos)
                continue
            merged.append({
                "key": key, "name": name, "description": _first_sentence(sec["description"]),
                "minutes": sec["minutes"], "_courses": {ci}, "_pos": pos,
                "sources": [{"course": course["title"], "url": course["url"], "provider": course["provider"]}],
            })
    for m in merged:
        m["courses"] = len(m.pop("_courses"))
    merged.sort(key=lambda m: (-m["courses"], m["_pos"]))
    top = merged[:MAX_TOPICS]
    top.sort(key=lambda m: m["_pos"])  # present in a sensible learning order
    for m in top:
        m.pop("_pos", None)
        m.pop("key", None)
    return top


def _title_rank(course: dict, terms: list[str]) -> int:
    title = course["title"].lower()
    hits = [title.find(t) for t in terms if t and t in title]
    return min(hits) if hits else 999


def _queries(skill_name: str, skill_terms: list[str]) -> list[str]:
    """'Git & Version Control' -> ['Git & Version Control', 'Git', 'git'] (deduplicated, max 3)."""
    short = re.split(r"\s+[&(]\s*|\s*\(", skill_name)[0].strip()
    alias = next((t for t in skill_terms[1:] if t and len(t) > 2), "")
    out = []
    for q in (skill_name, short, alias):
        if q and q.lower() not in {x.lower() for x in out}:
            out.append(q)
    return out[:3]


def discover_topics(skill_name: str, skill_terms: list[str], slug: str | None = None) -> dict:
    terms = [t.lower() for t in skill_terms if t]
    relevant = []
    for query in _queries(skill_name, skill_terms):
        slugs = find_course_slugs(query)
        if not slugs:
            continue
        with ThreadPoolExecutor(max_workers=3) as pool:
            courses = [c for c in pool.map(_fetch_course, slugs[:MAX_COURSES + 3]) if c]
        relevant = [c for c in courses if c["sections"] and any(
            t in (c["title"] + " " + " ".join(c["skills"])).lower() for t in terms)]
        if relevant:
            break
    # Courses that are *about* the skill (named early in the title) first.
    relevant.sort(key=lambda c: _title_rank(c, terms))
    relevant = [c for c in relevant if _title_rank(c, terms) < 20][:MAX_COURSES] or relevant[:2]
    if not relevant:
        return {}
    outcomes = []
    for c in relevant:
        for t in c["teaches"]:
            if all(SequenceMatcher(None, t.lower(), o.lower()).ratio() < 0.7 for o in outcomes):
                outcomes.append(t)
    return {
        "topics": merge_topics(relevant, skill_terms, slug),
        "outcomes": outcomes[:6],
        "courses": [{"title": c["title"], "url": c["url"], "provider": c["provider"], "level": c["level"],
                     "rating": c["rating"]} for c in relevant],
        "source": "Coursera course syllabi",
    }


def topics_for_skill(slug: str, allow_live: bool = True) -> dict | None:
    """Cached syllabus topics for a skill; discovers and stores them on first use."""
    from taxonomy.models import Skill

    from .models import SkillSyllabus
    from .service import _terms_for

    row = SkillSyllabus.objects.filter(skill__slug=slug).first()
    if row and (row.topics or (timezone.now() - row.fetched_at).days < 3):
        return row.as_dict() if row.topics else None
    if not allow_live:
        return None
    skill = Skill.objects.filter(slug=slug).first()
    if not skill:
        return None
    name, terms = _terms_for(slug)
    try:
        data = discover_topics(name, terms, slug)
    except FetchError:
        return None
    except Exception:  # never break an analysis over topics
        log.exception("Topic discovery failed for %s", slug)
        return None
    SkillSyllabus.objects.update_or_create(skill=skill, defaults={
        "topics": data.get("topics", []), "outcomes": data.get("outcomes", []),
        "courses": data.get("courses", []), "source": data.get("source", ""), "fetched_at": timezone.now()})
    return data if data.get("topics") else None
