"""
Orchestrates extraction (LLM first, heuristics as fallback) and normalises
all skill names onto the taxonomy so matching is deterministic.
"""
from __future__ import annotations

from django.conf import settings

from taxonomy.normalizer import get_index

from . import heuristics, llm
from .privacy import redact_pii


def _norm_skill_list(names: list[str]) -> list[dict]:
    idx = get_index()
    out, seen = [], set()
    for raw in names or []:
        slug, name, known = idx.normalize(str(raw))
        if not slug or slug in seen:
            continue
        seen.add(slug)
        out.append({"slug": slug, "name": name, "known": known})
    return out


def normalize_job(data: dict, source: str) -> dict:
    required = _norm_skill_list(data.get("required_skills", []))
    req_slugs = {s["slug"] for s in required}
    preferred = [s for s in _norm_skill_list(data.get("preferred_skills", [])) if s["slug"] not in req_slugs]
    return {
        "title": (data.get("title") or "").strip(),
        "company": (data.get("company") or "").strip(),
        "seniority": data.get("seniority") or "unknown",
        "years_required": max(0, int(data.get("years_required") or 0)),
        "education": data.get("education") or "",
        "domain": (data.get("domain") or "").strip(),
        "hard_requirements": [h for h in (data.get("hard_requirements") or []) if h][:10],
        "summary": (data.get("summary") or "").strip(),
        "required": required,
        "preferred": preferred,
        "source": source,
    }


def normalize_profile(data: dict, source: str) -> dict:
    idx = get_index()
    skills, by_slug = [], {}
    for s in data.get("skills") or []:
        raw = s.get("name", "") if isinstance(s, dict) else str(s)
        slug, name, known = idx.normalize(raw)
        if not slug:
            continue
        strength = (s.get("strength") if isinstance(s, dict) else None) or "listed"
        evidence = (s.get("evidence") if isinstance(s, dict) else "") or ""
        if slug in by_slug:
            if strength == "applied":
                by_slug[slug].update(strength="applied", evidence=evidence or by_slug[slug]["evidence"])
            continue
        entry = {"slug": slug, "name": name, "known": known, "strength": strength, "evidence": evidence[:240]}
        by_slug[slug] = entry
        skills.append(entry)
    return {
        "headline": (data.get("headline") or "").strip(),
        "seniority": data.get("seniority") or "unknown",
        "years_experience": max(0, int(data.get("years_experience") or 0)),
        "education": data.get("education") or "",
        "domains": [d for d in (data.get("domains") or []) if d][:8],
        "skills": skills,
        "source": source,
    }


def _prepare(text: str) -> str:
    return redact_pii(text) if settings.SKILLMESH.get("REDACT_PII", True) else text


def analyze_job_text(text: str) -> dict:
    try:
        data = llm.extract_job(_prepare(text))
        job = normalize_job(data, "llm")
        if job["required"] or job["preferred"]:
            return job
    except llm.LLMUnavailable:
        pass
    return normalize_job(heuristics.extract_job(text), "heuristic")


def analyze_resume_text(text: str) -> dict:
    try:
        data = llm.extract_profile(_prepare(text))
        profile = normalize_profile(data, "llm")
        if profile["skills"]:
            return profile
    except llm.LLMUnavailable:
        pass
    return normalize_profile(heuristics.extract_profile(text), "heuristic")
