"""
Rule-based extraction (no LLM). Used when no API key is configured and as a
fallback when the LLM call fails or refuses. Output matches the LLM schemas.
"""
from __future__ import annotations

import re
from datetime import date

from taxonomy.normalizer import get_index

SENIORITY_ORDER = ["intern", "junior", "mid", "senior", "lead"]

_REQ_HEAD = re.compile(
    r"^\W*(requirements?|qualifications?|must[- ]haves?|what you('ll)? (need|bring)|you (have|bring|are)|"
    r"required|minimum qualifications|basic qualifications|skills|technical skills|what we('re| are) looking for|"
    r"who you are|about you|your profile|responsibilities|what you('ll)? do|the role)\b",
    re.I,
)
_PREF_HEAD = re.compile(
    r"^\W*(nice[- ]to[- ]haves?|preferred( qualifications)?|bonus( points)?|plus(es)?|good to have|desirable|"
    r"additional (skills|qualifications)|it'?s a plus|even better)\b",
    re.I,
)
_IGNORE_HEAD = re.compile(r"^\W*(about (us|the company)|benefits|perks|what we offer|why join|our values|equal opportunity)\b", re.I)
_PREF_INLINE = re.compile(r"\b(nice to have|preferred|is a plus|a plus|bonus|desirable|good to have|ideally)\b", re.I)

_YEARS = re.compile(r"(\d{1,2})\s*(?:\+|plus)?\s*(?:[-–]|to)?\s*(\d{1,2})?\s*\+?\s*(?:years|yrs)", re.I)
_EDU = [
    ("phd", re.compile(r"\b(ph\.?d|doctorate)\b", re.I)),
    ("master", re.compile(r"\b(master'?s?|m\.?sc|m\.?s\.|mba|m\.?tech)\b", re.I)),
    ("bachelor", re.compile(r"\b(bachelor'?s?|b\.?sc|b\.?s\.|b\.?a\.|b\.?tech|b\.?e\.|undergraduate degree|degree in)\b", re.I)),
    ("associate", re.compile(r"\b(associate'?s? degree|diploma)\b", re.I)),
]
_HARD_REQ = re.compile(
    r"(security clearance|clearance|visa sponsorship|work authori[sz]ation|authori[sz]ed to work|right to work|"
    r"driver'?s licen[cs]e|licen[cs]ed|must be located|relocat|on-?site only|fluent in|native speaker|"
    r"certification required|must hold|must have a valid)",
    re.I,
)

_RESUME_SKILL_HEAD = re.compile(r"^\W*(technical )?(skills|technologies|tech stack|tools|core competencies|expertise|languages)\b", re.I)
_RESUME_APPLIED_HEAD = re.compile(r"^\W*(work )?(experience|employment|work history|professional experience|projects?|internships?|achievements)\b", re.I)
_RESUME_EDU_HEAD = re.compile(r"^\W*(education|academic|certifications?|courses|training)\b", re.I)
_ACTION = re.compile(
    r"\b(built|developed|designed|implemented|created|led|migrated|deployed|shipped|optimi[sz]ed|automated|"
    r"architected|maintained|integrated|wrote|launched|refactored|scaled|reduced|improved|engineered|managed|trained)\b",
    re.I,
)

_MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
_DATE = rf"(?:(?:{_MONTHS})[a-z]*\.?\s+)?(?:\d{{1,2}}/)?((?:19|20)\d{{2}})"
_RANGE = re.compile(rf"{_DATE}\s*(?:[-–—]|to|until)\s*(?:{_DATE}|(present|current|now|today|ongoing))", re.I)


def _is_heading(line: str) -> bool:
    s = line.strip()
    return 0 < len(s) <= 60 and (s.endswith(":") or s.isupper() or len(s.split()) <= 5)


_ROLE_WORDS = re.compile(
    r"\b(engineer|developer|analyst|scientist|designer|architect|administrator|consultant|specialist|manager|"
    r"intern|programmer|devops|sre|tester|qa|researcher|lead|technician|writer|officer|associate)\b", re.I)
_CHROME = re.compile(r"(careers? portal|job details|apply now|sign in|log in|share this job|back to (jobs|search)|"
                     r"skip to|cookie|menu|search jobs)", re.I)


def guess_title(lines: list[str]) -> str:
    """First short line near the top that names a role, skipping page/portal chrome (common in screenshots)."""
    top = [l.strip(" -*•|") for l in lines if l.strip()][:8]
    for line in top:
        if len(line) <= 100 and not _CHROME.search(line) and _ROLE_WORDS.search(line):
            return line[:120]
    for line in top:
        if len(line) <= 100 and not _CHROME.search(line):
            return line[:120]
    return ""


def detect_seniority(text: str) -> str:
    low = text.lower()
    if re.search(r"\b(intern|internship|trainee)\b", low):
        return "intern"
    if re.search(r"\b(principal|staff|lead|head of|architect|manager)\b", low):
        return "lead"
    if re.search(r"\b(senior|sr\.?)\b", low):
        return "senior"
    if re.search(r"\b(junior|jr\.?|entry[- ]level|graduate|new grad|associate)\b", low):
        return "junior"
    if re.search(r"\b(mid[- ]level|intermediate)\b", low):
        return "mid"
    return "unknown"


def detect_education(text: str) -> str:
    for level, pat in _EDU:
        if pat.search(text):
            return level
    return ""


def years_required(text: str) -> int:
    """Smallest 'N years ... experience' figure (ranges like '3-5 years' count as 3)."""
    best = None
    for m in _YEARS.finditer(text):
        around = (text[max(0, m.start() - 60): m.start()] + text[m.end(): m.end() + 60]).lower()
        if "experience" not in around and "exp" not in around:
            continue
        n = int(m.group(1))
        if 0 < n <= 20:
            best = n if best is None else min(best, n)
    return best or 0


def extract_job(text: str) -> dict:
    idx = get_index()
    lines = [l.rstrip() for l in text.splitlines()]
    has_req_section = any(_REQ_HEAD.match(l) and _is_heading(l) for l in lines)
    section = "general"
    required, preferred, hard = {}, {}, []

    for line in lines:
        s = line.strip()
        if not s:
            continue
        if _is_heading(s):
            if _PREF_HEAD.match(s):
                section = "preferred"
                continue
            if _REQ_HEAD.match(s):
                section = "required"
                continue
            if _IGNORE_HEAD.match(s):
                section = "ignore"
                continue
        if section == "ignore":
            continue
        if _HARD_REQ.search(s) and len(s) < 250:
            hard.append(s.lstrip("•-* ").strip())
        bucket = preferred if (section == "preferred" or _PREF_INLINE.search(s)) else required
        if section == "general" and has_req_section:
            bucket = required  # intro/responsibilities mentions still describe the job
        for m in idx.find_mentions(s):
            bucket.setdefault(m.slug, idx.get(m.slug).name)

    for slug in list(preferred):
        if slug in required:
            preferred.pop(slug)

    title = guess_title(lines)
    first = title or next((l.strip() for l in lines if l.strip()), "")
    return {
        "title": title,
        "company": "",
        "seniority": detect_seniority(first + "\n" + text[:1500]),
        "years_required": years_required(text),
        "education": detect_education(text),
        "domain": "",
        "required_skills": list(required.values()),
        "preferred_skills": list(preferred.values()),
        "hard_requirements": list(dict.fromkeys(hard))[:8],
        "summary": "",
    }


def _year_ranges(text: str) -> list[tuple[int, int]]:
    today = date.today()
    out = []
    for m in _RANGE.finditer(text):
        start = int(m.group(1))
        end = today.year if m.group(3) else int(m.group(2) or start)
        if 1970 <= start <= end <= today.year + 1:
            out.append((start, end))
    return out


def estimate_years_experience(text: str) -> int:
    """Union of employment date ranges (education section excluded), in whole years."""
    lines = text.splitlines()
    kept, section = [], "other"
    for l in lines:
        s = l.strip()
        if _is_heading(s):
            if _RESUME_EDU_HEAD.match(s):
                section = "edu"
            elif _RESUME_APPLIED_HEAD.match(s) or _RESUME_SKILL_HEAD.match(s):
                section = "work"
        if section != "edu":
            kept.append(l)
    ranges = sorted(_year_ranges("\n".join(kept)))
    merged: list[list[int]] = []
    for a, b in ranges:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return min(40, sum(b - a for a, b in merged))


def extract_profile(text: str) -> dict:
    idx = get_index()
    section = "other"
    skills: dict[str, dict] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if _is_heading(s):
            if _RESUME_SKILL_HEAD.match(s):
                section = "skills"
            elif _RESUME_APPLIED_HEAD.match(s):
                section = "applied"
            elif _RESUME_EDU_HEAD.match(s):
                section = "edu"
        applied = section == "applied" or bool(_ACTION.search(s))
        for m in idx.find_mentions(s):
            cur = skills.get(m.slug)
            strength = "applied" if applied else "listed"
            if not cur or (cur["strength"] == "listed" and strength == "applied"):
                skills[m.slug] = {"name": idx.get(m.slug).name, "evidence": s[:200], "strength": strength}

    first = next((l.strip() for l in text.splitlines() if l.strip()), "")
    return {
        "headline": first[:120],
        "seniority": detect_seniority(text[:600]),
        "years_experience": estimate_years_experience(text),
        "education": detect_education(text),
        "skills": list(skills.values()),
        "domains": [],
    }
