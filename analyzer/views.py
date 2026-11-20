"""
JSON API. Every endpoint is stateless: inputs are processed in memory and
returned; nothing about the user is stored.
"""
from __future__ import annotations

import base64
import json
import logging
import time

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_GET, require_POST

from resources.service import grouped_resources

from . import extraction, github, github_auth, roadmap, scoring
from .documents import DocumentError, extract_text
from .jobpage import LoginRequired, NoJobFound, extract_job_page
from .llm import llm_configured
from .net import FetchError, safe_get
from .ratelimit import rate_limit

log = logging.getLogger(__name__)

MAX_TEXT = 40_000
MIN_JOB_TEXT = 80


def error(code: str, message: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"error": {"code": code, "message": message}}, status=status)


def _json_body(request) -> dict:
    try:
        data = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("Invalid JSON body.")
    if not isinstance(data, dict):
        raise ValueError("JSON body must be an object.")
    return data


@require_GET
def health(request):
    return JsonResponse({
        "ok": True,
        "llm": llm_configured(),
        "youtube": bool(settings.SKILLMESH.get("YOUTUBE_API_KEY")),
        "web_search": bool(settings.SKILLMESH.get("BRAVE_SEARCH_API_KEY")),
        "github_token": bool(settings.SKILLMESH.get("GITHUB_TOKEN")),
    })


@require_POST
@rate_limit("github", per_minute=6)
def analyze_github(request):
    """``{"username": "octocat" | "https://github.com/octocat"}`` -> skills evidenced by public repos."""
    try:
        raw = str(_json_body(request).get("username") or "")
    except ValueError as exc:
        return error("bad_request", str(exc))
    try:
        username = github.parse_username(raw)
    except github.GitHubError as exc:
        return error(exc.code, exc.message, 400)
    if not github_auth.can_analyze(request, username):
        return JsonResponse({"error": {
            "code": "login_required",
            "message": "You've used your free GitHub analysis. Sign in with GitHub to analyze more profiles. "
                       "We request no permissions; the sign-in only lifts GitHub's rate limit.",
        }, "github_auth": {"oauth_configured": github_auth.oauth_configured()}}, status=401)
    token = github_auth.session_token(request)
    try:
        result = github.analyze_user(username, token=token)
    except github.GitHubError as exc:
        if exc.code == "token_invalid":
            request.session.pop("gh_token", None)
            request.session.pop("gh_login", None)
        if exc.code == "rate_limited" and not token:
            return JsonResponse({"error": {"code": "login_required", "message": exc.message},
                                 "github_auth": {"oauth_configured": github_auth.oauth_configured()}}, status=401)
        status = {"not_found": 404, "no_repos": 404, "rate_limited": 429, "token_invalid": 401}.get(exc.code, 502)
        return error(exc.code, exc.message, status)
    github_auth.record_free_use(request, username)
    return JsonResponse({"github": result, "github_auth": {
        "signed_in": bool(token), "free_remaining": github_auth.free_remaining(request)}})


@require_POST
@rate_limit("resume", per_minute=10)
def parse_resume(request):
    """Multipart upload (field ``file``) or JSON ``{"text": ...}`` -> candidate profile."""
    text = ""
    if request.FILES.get("file"):
        f = request.FILES["file"]
        if f.size > settings.MAX_UPLOAD_BYTES:
            return error("too_large", "Please upload a file under 5 MB.")
        try:
            text = extract_text(f.name, f.read())
        except DocumentError as exc:
            return error(exc.code, exc.message)
        finally:
            f.close()
    else:
        try:
            text = (_json_body(request).get("text") or "").strip()
        except ValueError as exc:
            return error("bad_request", str(exc))
        if len(text) < 80:
            return error("empty", "Please paste your full resume text.")
    text = text[:MAX_TEXT]
    profile = extraction.analyze_resume_text(text)
    return JsonResponse({"profile": profile, "chars": len(text)})


@require_POST
@rate_limit("scrape", per_minute=10)
def scrape_job(request):
    """``{"url": ...}`` -> job posting text from a public page (no login-walled sites)."""
    try:
        url = (_json_body(request).get("url") or "").strip()
    except ValueError as exc:
        return error("bad_request", str(exc))
    if not url:
        return error("invalid_url", "Please enter a job URL.")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        page = safe_get(url)
    except FetchError as exc:
        return error(exc.code, exc.message, status=422)
    if page.status in (401, 403):
        return error("login_required", "This page requires login or blocks automated access. Paste the job description instead.", 422)
    if page.status >= 400:
        return error("unreachable", f"The site returned HTTP {page.status}.", 422)
    if "html" not in page.content_type and "xml" not in page.content_type and page.content_type:
        return error("unsupported", "That link doesn't point to a web page.", 422)
    try:
        job = extract_job_page(page.final_url, page.text)
    except LoginRequired:
        return error("login_required", "This posting is behind a login. Paste the job description or upload a screenshot instead.", 422)
    except NoJobFound:
        return error("no_job_found", "We couldn't find a job description on that page. Try pasting it instead.", 422)
    header = "\n".join(filter(None, [job.title, job.company, job.location]))
    return JsonResponse({
        "title": job.title, "company": job.company, "location": job.location,
        "method": job.method, "final_url": page.final_url,
        "text": (header + "\n\n" + job.text).strip()[:MAX_TEXT],
    })


@require_POST
@rate_limit("job", per_minute=15)
def parse_job(request):
    """``{"text": ...}`` -> structured job requirements."""
    try:
        data = _json_body(request)
    except ValueError as exc:
        return error("bad_request", str(exc))
    text = (data.get("text") or "").strip()[:MAX_TEXT]
    if len(text) < MIN_JOB_TEXT:
        return error("empty", "Please provide the full job description.")
    job = extraction.analyze_job_text(text)
    if not job["required"] and not job["preferred"]:
        return error("no_skills", "We couldn't identify any skills in this job description. Add them manually below.", 422)
    return JsonResponse({"job": job})


def _clean_skill_list(items, with_strength=False) -> list[dict]:
    from taxonomy.normalizer import get_index

    idx = get_index()
    out, seen = [], set()
    for it in items or []:
        if not isinstance(it, dict):
            continue
        given_slug = str(it.get("slug") or "")
        if given_slug in idx.skills:
            slug, name, known = given_slug, idx.skills[given_slug].name, True
        else:
            slug, name, known = idx.normalize(str(it.get("name") or "")[:120])
        if not slug or slug in seen:
            continue
        seen.add(slug)
        row = {"slug": slug, "name": name, "known": known}
        if with_strength:
            row["strength"] = "applied" if it.get("strength") == "applied" else "listed"
            row["evidence"] = str(it.get("evidence") or "")[:240]
        out.append(row)
    return out[:80]


@require_POST
@rate_limit("analyze", per_minute=20)
def analyze(request):
    """
    ``{"job": {...}, "profile": {...}, "hours_per_week": 10, "include_preferred": true}``
    (both objects as returned by the parse endpoints, possibly edited by the user)
    -> score report + roadmap.
    """
    try:
        data = _json_body(request)
    except ValueError as exc:
        return error("bad_request", str(exc))
    job_in, prof_in = data.get("job") or {}, data.get("profile") or {}
    if not isinstance(job_in, dict) or not isinstance(prof_in, dict):
        return error("bad_request", "job and profile must be objects.")

    def as_int(v, lo, hi, default):
        try:
            return max(lo, min(hi, int(v)))
        except (TypeError, ValueError):
            return default

    job = {
        "title": str(job_in.get("title") or "")[:200],
        "company": str(job_in.get("company") or "")[:200],
        "seniority": str(job_in.get("seniority") or "unknown"),
        "years_required": as_int(job_in.get("years_required"), 0, 30, 0),
        "education": str(job_in.get("education") or ""),
        "domain": str(job_in.get("domain") or "")[:100],
        "hard_requirements": [str(h)[:250] for h in (job_in.get("hard_requirements") or [])][:10],
        "required": _clean_skill_list(job_in.get("required")),
        "preferred": _clean_skill_list(job_in.get("preferred")),
    }
    profile = {
        "seniority": str(prof_in.get("seniority") or "unknown"),
        "years_experience": as_int(prof_in.get("years_experience"), 0, 50, 0),
        "education": str(prof_in.get("education") or ""),
        "domains": [str(d)[:80] for d in (prof_in.get("domains") or [])][:10],
        "skills": _clean_skill_list(prof_in.get("skills"), with_strength=True),
    }
    if not job["required"] and not job["preferred"]:
        return error("no_skills", "The job has no skills to compare against.")

    for s in profile["skills"]:
        s["sources"] = ["resume"]
    gh_in = data.get("github") or {}
    gh_skills = _clean_skill_list(gh_in.get("skills") if isinstance(gh_in, dict) else [], with_strength=True)
    raw_gh = {str(it.get("slug")): it for it in (gh_in.get("skills") or []) if isinstance(it, dict)} if isinstance(gh_in, dict) else {}
    by_slug = {s["slug"]: s for s in profile["skills"]}
    for g in gh_skills:
        level = as_int((raw_gh.get(g["slug"]) or {}).get("level"), 0, 95, 50)
        if g["slug"] in by_slug:
            held = by_slug[g["slug"]]
            held["sources"].append("github")
            held["github_level"] = level
            held["github_evidence"] = g["evidence"]
            held["strength"] = "applied"
        else:
            profile["skills"].append({**g, "strength": "applied", "sources": ["github"],
                                      "github_level": level, "github_evidence": g["evidence"]})

    hours = as_int(data.get("hours_per_week"), 1, 60, 10)
    include_pref = bool(data.get("include_preferred", True))

    report = scoring.score(job, profile)

    # Live discovery for uncached skills gets a time budget; later steps fall back to
    # search links (and are discovered on the next analysis or by `refresh_resources`).
    deadline = time.monotonic() + settings.SKILLMESH.get("DISCOVERY_BUDGET_SECONDS", 25)

    def lookup(step):
        live = None if time.monotonic() < deadline else False
        return grouped_resources(step["slug"], step["name"], allow_live=live)

    plan = roadmap.build_roadmap(report, profile, hours_per_week=hours,
                                 include_preferred=include_pref, resource_lookup=lookup)
    return JsonResponse({"job": {k: job[k] for k in ("title", "company", "seniority", "years_required")},
                         "report": report, "roadmap": plan})


@require_POST
@rate_limit("pdf", per_minute=10)
def roadmap_pdf(request):
    """Render an exported roadmap (same JSON as the "Export JSON" file) to a styled PDF, in memory."""
    from .pdf import RoadmapPDFError, build_roadmap_pdf

    try:
        data = _json_body(request)
    except ValueError as exc:
        return error("bad_request", str(exc))
    try:
        pdf_bytes, filename = build_roadmap_pdf(data)
    except RoadmapPDFError as exc:
        return error("bad_roadmap", str(exc))
    # Returned as base64 inside JSON rather than application/pdf: browser download managers
    # (and some extensions) hijack PDF responses to fetch() and hand the page an empty body.
    # Add ?format=pdf to get the raw file (e.g. for curl).
    if request.GET.get("format") == "pdf":
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
    return JsonResponse({"filename": filename, "content_type": "application/pdf",
                         "data": base64.b64encode(pdf_bytes).decode("ascii")})


@require_POST
@rate_limit("reader", per_minute=30)
def reader(request):
    """``{"url": ...}`` -> plain-text article blocks for the split-view reader (vetted URLs only)."""
    from .reader import ReaderError, read

    try:
        url = str(_json_body(request).get("url") or "")[:1000]
    except ValueError as exc:
        return error("bad_request", str(exc))
    try:
        return JsonResponse({"page": read(url)})
    except ReaderError as exc:
        status = {"not_allowed": 403, "invalid_url": 400}.get(exc.code, 422)
        return error(exc.code, exc.message, status)
