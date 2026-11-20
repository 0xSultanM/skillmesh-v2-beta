"""
Deterministic, explainable fit scoring.

score = 0.55 * required coverage
      + 0.15 * preferred coverage
      + 0.15 * experience fit
      + 0.10 * domain fit
      + 0.05 * education fit

then capped when critical gates fail, so a number never looks better than
the evidence supports.
"""
from __future__ import annotations

from taxonomy.normalizer import get_index

WEIGHTS = {"required": 0.55, "preferred": 0.15, "experience": 0.15, "domain": 0.10, "education": 0.05}
SENIORITY_RANK = {"intern": 0, "junior": 1, "mid": 2, "senior": 3, "lead": 4}
EDU_RANK = {"": 0, "associate": 1, "bachelor": 2, "master": 3, "phd": 4}

CREDIT_APPLIED = 1.0
CREDIT_LISTED = 0.75
CREDIT_IMPLIED = 0.85  # e.g. React experience implies JavaScript
CREDIT_RELATED = 0.45  # e.g. MySQL when PostgreSQL is wanted


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


# Proficiency a role expects on its required skills, by seniority (0-100 scale).
REQUIRED_LEVEL = {"intern": 35, "junior": 45, "mid": 60, "senior": 75, "lead": 85, "unknown": 60}
PREFERRED_DISCOUNT = 10


def required_level(job: dict, kind: str) -> int:
    base = REQUIRED_LEVEL.get(job.get("seniority") or "unknown", 60)
    return base - (PREFERRED_DISCOUNT if kind == "preferred" else 0)


def candidate_level(held: dict, relation: str, years: int) -> int:
    """Estimated 0-100 proficiency on the wanted skill given one piece of held evidence."""
    sources = held.get("sources") or ["resume"]
    gh = held.get("github_level") or 0
    if relation == "exact":
        resume_part = 0
        if "resume" in sources:
            resume_part = 60 + min(15, 3 * (years or 0)) if held.get("strength") == "applied" else 35
        level = max(resume_part, gh)
        if "resume" in sources and gh:
            level += 8  # claim corroborated by public code
    elif relation == "prerequisite":
        level = max(50, int(gh * 0.8))
    elif relation == "related":
        level = max(30, int(gh * 0.5))
    else:
        level = 0
    return max(0, min(95, level))


def match_skill(wanted: dict, profile_skills: list[dict], years: int = 0) -> dict:
    """Best evidence the candidate has for one wanted skill."""
    idx = get_index()
    best = {"status": "missing", "credit": 0.0, "via": None, "evidence": "", "strength": None,
            "relation": None, "your_level": 0, "sources": []}
    want_slug = wanted["slug"]
    want_name = wanted["name"].lower()

    for held in profile_skills:
        if held["slug"] == want_slug or held["name"].lower() == want_name:
            rel = "exact"
        else:
            rel = idx.relation(held["slug"], want_slug)

        if rel == "exact":
            credit = CREDIT_APPLIED if held.get("strength") == "applied" else CREDIT_LISTED
        elif rel == "prerequisite":
            credit = CREDIT_IMPLIED
        elif rel == "related":
            credit = CREDIT_RELATED
        else:
            continue

        level = candidate_level(held, rel, years)
        if (credit, level) > (best["credit"], best["your_level"]):
            best = {
                "status": "matched" if credit >= 0.75 else "partial",
                "credit": credit,
                "via": None if rel == "exact" else held["name"],
                "relation": rel,
                "evidence": held.get("evidence", ""),
                "github_evidence": held.get("github_evidence", ""),
                "strength": held.get("strength"),
                "your_level": level,
                "sources": held.get("sources") or ["resume"],
            }
    return best


def _coverage(wanted: list[dict], profile: dict, job: dict, kind: str) -> tuple[float, list[dict]]:
    rows = []
    req_level = required_level(job, kind)
    for w in wanted:
        m = match_skill(w, profile.get("skills") or [], profile.get("years_experience") or 0)
        rows.append({**w, **m, "required_level": req_level, "level_gap": max(0, req_level - m["your_level"])})
    if not rows:
        return 1.0, rows
    return sum(r["credit"] for r in rows) / len(rows), rows


def readiness(rows: list[dict]) -> int:
    """Average of your level relative to the required level, capped at 100% per skill."""
    if not rows:
        return 100
    return int(round(100 * sum(min(1.0, r["your_level"] / max(1, r["required_level"])) for r in rows) / len(rows)))


def cross_check(rows: list[dict], github_used: bool) -> dict | None:
    """Compare resume claims with public-code evidence, for the skills this job asks about."""
    if not github_used:
        return None
    confirmed, resume_only, github_only = [], [], []
    for r in rows:
        if r["status"] == "missing" or r.get("relation") != "exact":
            continue
        src = set(r.get("sources") or [])
        if {"resume", "github"} <= src:
            confirmed.append(r["name"])
        elif "github" in src:
            github_only.append(r["name"])
        else:
            resume_only.append(r["name"])
    return {"confirmed": confirmed, "resume_only": resume_only, "github_only": github_only}


def experience_fit(job: dict, profile: dict) -> tuple[float, str]:
    req = job.get("years_required") or 0
    have = profile.get("years_experience") or 0
    years = 1.0 if req == 0 else _clamp(have / req)

    js, ps = SENIORITY_RANK.get(job.get("seniority")), SENIORITY_RANK.get(profile.get("seniority"))
    level = 1.0
    if js is not None and ps is not None:
        gap = js - ps
        level = 1.0 if gap <= 0 else (0.7 if gap == 1 else 0.35)
    fit = min(years, level) if req else level

    if req:
        note = f"{have} of {req}+ years required"
    elif js is not None:
        note = f"Role is {job['seniority']}-level"
    else:
        note = "No explicit experience requirement"
    return fit, note


def domain_fit(job: dict, profile: dict) -> float:
    domain = (job.get("domain") or "").lower().strip()
    if not domain:
        return 1.0
    words = {w for w in domain.replace("/", " ").replace("-", " ").split() if len(w) > 2}
    for d in profile.get("domains") or []:
        low = d.lower()
        if domain in low or low in domain or words & set(low.replace("-", " ").split()):
            return 1.0
    return 0.5


def education_fit(job: dict, profile: dict) -> float:
    need = EDU_RANK.get(job.get("education") or "", 0)
    have = EDU_RANK.get(profile.get("education") or "", 0)
    if need == 0 or have >= need:
        return 1.0
    return 0.5  # many employers accept equivalent experience


def verdict_for(score: int, critical_missing: int) -> dict:
    if score >= 80 and critical_missing == 0:
        return {"key": "strong", "label": "Strong match", "message": "You meet the core requirements. Apply with confidence."}
    if score >= 70:
        return {"key": "competitive", "label": "Competitive", "message": "You're a credible candidate. Close the highlighted gaps to stand out."}
    if score >= 50:
        return {"key": "stretch", "label": "Stretch", "message": "Reachable with focused upskilling. Follow the roadmap before applying, or apply while you learn."}
    return {"key": "not_yet", "label": "Not yet", "message": "There are significant gaps. The roadmap below shows the fastest path."}


def quick_wins(required_rows: list[dict], preferred_rows: list[dict], github_used: bool = False) -> list[str]:
    idx = get_index()
    wins = []
    for r in required_rows + preferred_rows:
        if r.get("relation") == "exact" and (r.get("sources") or []) == ["github"]:
            wins.append(f"Your GitHub shows {r['name']} but your resume doesn't mention it. Add it to your resume.")
    for r in required_rows:
        if github_used and r.get("relation") == "exact" and (r.get("sources") or []) == ["resume"]:
            wins.append(f"No public code backs up {r['name']}. A small public repo would let recruiters verify it.")
        elif r["status"] == "matched" and r.get("strength") == "listed" and not r.get("via"):
            wins.append(f"You list {r['name']} but don't show using it. Add a project or job bullet with a concrete result.")
        elif r["status"] == "partial" and r.get("via"):
            wins.append(f"Highlight your {r['via']} experience; it transfers to {r['name']}. Mention it next to {r['name']} in your summary.")
    for r in preferred_rows:
        info = idx.get(r["slug"])
        if r["status"] == "missing" and info and info.learn_hours <= 10:
            wins.append(f"{r['name']} is a nice-to-have you can pick up in about {info.learn_hours} hours.")
    return wins[:8]


def score(job: dict, profile: dict) -> dict:
    github_used = any("github" in (s.get("sources") or []) for s in profile.get("skills") or [])
    req_cov, req_rows = _coverage(job.get("required") or [], profile, job, "required")
    pref_cov, pref_rows = _coverage(job.get("preferred") or [], profile, job, "preferred")
    exp, exp_note = experience_fit(job, profile)
    dom = domain_fit(job, profile)
    edu = education_fit(job, profile)

    components = {
        "required": req_cov, "preferred": pref_cov, "experience": exp, "domain": dom, "education": edu,
    }
    raw = sum(WEIGHTS[k] * v for k, v in components.items()) * 100

    caps = []
    if job.get("required") and req_cov < 0.4:
        caps.append((55, "Fewer than half of the required skills are evidenced."))
    req_years = job.get("years_required") or 0
    if req_years and (profile.get("years_experience") or 0) < req_years * 0.5:
        caps.append((65, f"Experience is well below the {req_years}+ years asked for."))
    final = raw
    for cap, _ in caps:
        final = min(final, cap)
    final = int(round(final))

    missing_required = [r for r in req_rows if r["status"] == "missing"]
    return {
        "score": final,
        "raw_score": round(raw, 1),
        "verdict": verdict_for(final, len(missing_required)),
        "breakdown": [
            {"key": k, "label": label, "weight": WEIGHTS[k], "value": round(components[k], 3),
             "points": round(WEIGHTS[k] * components[k] * 100, 1), "note": note}
            for k, label, note in [
                ("required", "Required skills",
                 f"{sum(1 for r in req_rows if r['status'] == 'matched')} of {len(req_rows)} matched"),
                ("preferred", "Nice-to-have skills",
                 f"{sum(1 for r in pref_rows if r['status'] == 'matched')} of {len(pref_rows)} matched"),
                ("experience", "Experience level", exp_note),
                ("domain", "Domain relevance", job.get("domain") or "Not specified"),
                ("education", "Education", job.get("education") or "Not specified"),
            ]
        ],
        "caps": [reason for _, reason in caps],
        "required": req_rows,
        "preferred": pref_rows,
        "critical_gaps": [r["name"] for r in missing_required],
        "hard_requirements": job.get("hard_requirements") or [],
        "quick_wins": quick_wins(req_rows, pref_rows, github_used),
        "readiness": readiness(req_rows),
        "required_level": required_level(job, "required"),
        "cross_check": cross_check(req_rows + pref_rows, github_used),
    }
