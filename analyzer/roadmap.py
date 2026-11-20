"""
Roadmap builder: turns skill gaps into an ordered, time-boxed learning plan
with vetted free resources and a portfolio project per step.
"""
from __future__ import annotations

import math

from taxonomy.normalizer import get_index

PROJECT_IDEAS = {
    "language": "Build a small CLI tool in {skill} (e.g. a todo or expense tracker) with tests, and publish it on GitHub.",
    "frontend": "Build a responsive app with {skill} that consumes a public API (weather, movies). Deploy it free on GitHub Pages, Netlify or Vercel.",
    "backend": "Build a REST API with {skill}: auth, CRUD, validation and tests. Document it with OpenAPI.",
    "database": "Design a schema for a real-world domain (library, clinic) in {skill}. Write 10 analytical queries and add indexes.",
    "devops": "Containerise one of your projects and automate build/test/deploy using {skill}.",
    "cloud": "Deploy a project on the {skill} free tier with infrastructure you can tear down and recreate.",
    "data": "Analyse a public dataset (Kaggle, data.gov) using {skill} and publish a notebook with clear findings.",
    "ml": "Train and evaluate a model with {skill} on a public dataset; write up results and limitations in the README.",
    "mobile": "Ship a small {skill} app with two or three screens, local storage and one API integration.",
    "testing": "Add a {skill} suite to an existing project and wire it into CI with a coverage badge.",
    "security": "Audit one of your projects against {skill} best practices and document the fixes.",
    "cs": "Solve 15 to 20 {skill} problems and write short explanations of your approach in a public repo.",
    "design": "Redesign a screen from a real app using {skill} principles and present a before/after case study.",
    "process": "Run a personal project for two weeks using {skill} practices (board, sprints, retro notes).",
    "tools": "Use {skill} daily on a real project; document your workflow in a short blog post.",
    "soft": "Write a STAR-format story showing {skill} for interviews, and practise it out loud.",
    "other": "Build a small portfolio project that clearly demonstrates {skill}.",
}

DEFAULT_CUSTOM_HOURS = 20
# Conceptually heavy categories are one notch harder than their hours alone suggest.
HARD_CATEGORIES = {"ml", "cs"}
EASY_CATEGORIES = {"soft", "process", "tools"}
LEVELS = ["easy", "medium", "hard"]


def difficulty(step: dict) -> str:
    hours = step["hours"] * (2 if step.get("partial") else 1)  # judge the topic, not the remaining effort
    idx = 0 if hours <= 12 else 1 if hours <= 35 else 2
    if step.get("category") in HARD_CATEGORIES:
        idx += 1
    if step.get("category") in EASY_CATEGORIES:
        idx -= 1
    if step.get("partial"):
        idx -= 1  # you already have a related foundation
    return LEVELS[max(0, min(2, idx))]


def _info(slug: str):
    return get_index().get(slug)


def _held(profile: dict) -> set[str]:
    """Skills the candidate has, plus skills implied by them (prerequisites of held skills)."""
    idx = get_index()
    held = {s["slug"] for s in profile.get("skills") or []}
    stack = list(held)
    while stack:
        info = idx.get(stack.pop())
        for p in (info.prerequisites if info else []):
            if p not in held:
                held.add(p)
                stack.append(p)
    return held


def build_steps(score_result: dict, profile: dict, include_preferred: bool = True) -> list[dict]:
    held = _held(profile)
    steps: dict[str, dict] = {}

    def add(slug: str, name: str, reason: str, priority: str, partial: bool = False, via: str | None = None):
        if slug in steps:
            # A skill that is both a prerequisite and explicitly required is shown as required.
            if priority == "required" and steps[slug]["priority"] != "required":
                steps[slug]["priority"] = "required"
                steps[slug]["reason"] = reason
            return
        info = _info(slug)
        hours = info.learn_hours if info else DEFAULT_CUSTOM_HOURS
        if partial:
            hours = max(4, math.ceil(hours * 0.5))
        steps[slug] = {
            "slug": slug, "name": info.name if info else name, "category": info.category if info else "other",
            "priority": priority, "reason": reason, "hours": hours, "partial": partial, "via": via,
        }
        # Pull in missing prerequisites first.
        for p in (info.prerequisites if info else []):
            if p not in held and p not in steps:
                pinfo = _info(p)
                if pinfo:
                    add(p, pinfo.name, f"Foundation for {steps[slug]['name']}", "foundation")

    for r in score_result.get("required") or []:
        if r["status"] == "missing":
            add(r["slug"], r["name"], "Required by the job", "required")
        elif r["status"] == "partial":
            add(r["slug"], r["name"], f"Required; build on your {r.get('via') or 'existing'} experience",
                "required", partial=True, via=r.get("via"))
    if include_preferred:
        for r in score_result.get("preferred") or []:
            if r["status"] == "missing":
                add(r["slug"], r["name"], "Nice-to-have that makes you stand out", "preferred")
            elif r["status"] == "partial":
                add(r["slug"], r["name"], "Nice-to-have; you're partly there", "preferred", partial=True, via=r.get("via"))

    return topo_order(list(steps.values()))


def topo_order(steps: list[dict]) -> list[dict]:
    """Prerequisites before dependants; within a level, required before preferred, shorter first."""
    idx = get_index()
    by_slug = {s["slug"]: s for s in steps}
    prio = {"foundation": 0, "required": 1, "preferred": 2}
    indeg = {s: 0 for s in by_slug}
    children: dict[str, list[str]] = {s: [] for s in by_slug}
    for slug in by_slug:
        info = idx.get(slug)
        for p in (info.prerequisites if info else []):
            if p in by_slug:
                indeg[slug] += 1
                children[p].append(slug)

    def key(slug):
        s = by_slug[slug]
        return (prio[s["priority"]], s["hours"], s["name"])

    ready = sorted([s for s, d in indeg.items() if d == 0], key=key)
    out = []
    while ready:
        slug = ready.pop(0)
        out.append(by_slug[slug])
        for c in children[slug]:
            indeg[c] -= 1
            if indeg[c] == 0:
                ready.append(c)
        ready.sort(key=key)
    # Cycles (shouldn't happen with a sane taxonomy): append leftovers.
    out += [by_slug[s] for s in by_slug if by_slug[s] not in out]
    return out


def schedule(steps: list[dict], hours_per_week: float) -> list[dict]:
    hours_per_week = max(1.0, float(hours_per_week))
    cursor = 0.0
    for s in steps:
        start = cursor
        cursor += s["hours"]
        s["start_week"] = int(start // hours_per_week) + 1
        s["end_week"] = max(s["start_week"], math.ceil(cursor / hours_per_week))
    return steps


def phases(steps: list[dict]) -> list[dict]:
    groups = [
        ("foundation", "Phase 1: Foundations", "Prerequisites the job skills build on."),
        ("required", "Phase 2: Close the required gaps", "These directly affect whether you get shortlisted."),
        ("preferred", "Phase 3: Stand out", "Nice-to-haves that differentiate you from other applicants."),
    ]
    result = []
    for key, title, blurb in groups:
        items = [s for s in steps if s["priority"] == key]
        if items:
            result.append({
                "key": key, "title": title, "description": blurb,
                "weeks": [min(s["start_week"] for s in items), max(s["end_week"] for s in items)],
                "hours": sum(s["hours"] for s in items),
                "steps": items,
            })
    return result


def build_roadmap(score_result: dict, profile: dict, hours_per_week: float = 10,
                  include_preferred: bool = True, resource_lookup=None) -> dict:
    steps = build_steps(score_result, profile, include_preferred)
    schedule(steps, hours_per_week)
    for s in steps:
        s["difficulty"] = difficulty(s)
        s["project"] = PROJECT_IDEAS.get(s["category"], PROJECT_IDEAS["other"]).format(skill=s["name"])
        s["resources"] = resource_lookup(s) if resource_lookup else {"video": [], "content": []}
    total_hours = sum(s["hours"] for s in steps)
    return {
        "hours_per_week": hours_per_week,
        "total_hours": total_hours,
        "total_weeks": max((s["end_week"] for s in steps), default=0),
        "phases": phases(steps),
        "step_count": len(steps),
    }
