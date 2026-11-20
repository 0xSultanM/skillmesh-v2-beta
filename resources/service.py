"""
Resource selection for roadmaps.

Read path is cache-first (SQLite). Live discovery (YouTube / web search) only
runs for skills that have too few cached resources, and its results are stored
so the next user gets them instantly. The LLM never invents links: everything
shown comes from this table.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.db import IntegrityError
from django.utils import timezone

from taxonomy.models import Skill
from taxonomy.normalizer import get_index

from .models import LearningResource

log = logging.getLogger(__name__)

# What a good roadmap step contains, in display order.
KIND_PRIORITY = {"course": 0, "playlist": 1, "docs": 2, "interactive": 3, "article": 4, "video": 5}


def _terms_for(slug: str) -> tuple[str, list[str]]:
    info = get_index().get(slug)
    if not info:
        return slug, [slug]
    return info.name, [info.name, *info.aliases[:4]]


def store_candidates(skill: Skill, candidates: list[dict]) -> int:
    saved = 0
    for c in candidates:
        try:
            _, created = LearningResource.objects.update_or_create(
                skill=skill,
                url=c["url"],
                defaults={
                    "kind": c["kind"],
                    "source": c.get("source", "curated"),
                    "title": c["title"],
                    "author": c.get("author", ""),
                    "description": c.get("description", ""),
                    "level": c.get("level", "beginner"),
                    "est_hours": c.get("est_hours"),
                    "quality_score": c.get("quality_score", 0.5),
                    "metadata": c.get("metadata", {}),
                    "is_free": True,
                    "is_active": True,
                    "last_checked": timezone.now(),
                },
            )
            saved += int(created)
        except IntegrityError:
            continue
    return saved


def discover_for_skill(slug: str, want_video: bool = True, want_content: bool = True) -> dict:
    """Run live discovery for one skill and cache the results. Returns counts per source.

    Videos: YouTube Data API when ``YOUTUBE_API_KEY`` is set, otherwise keyless
    web search + oEmbed verification. Articles: web search (Brave or keyless).
    """
    from concurrent.futures import ThreadPoolExecutor

    from django.core.cache import cache

    from .blogs import discover_articles
    from .websearch import SearchUnavailable

    skill = Skill.objects.filter(slug=slug).first()
    if not skill:
        return {"error": "unknown skill"}
    name, terms = _terms_for(slug)

    def videos():
        if settings.SKILLMESH.get("YOUTUBE_API_KEY"):
            from .youtube import YouTubeUnavailable, discover_playlists
            try:
                return discover_playlists(name, terms)[:3], "youtube-api"
            except YouTubeUnavailable as exc:
                log.warning("YouTube API unavailable (%s); falling back to keyless discovery", exc)
        from .youtube_keyless import discover_videos
        return discover_videos(name, terms, limit=3), "keyless"

    def articles():
        return discover_articles(name, terms, limit=4)

    report = {}
    jobs = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        if want_video:
            jobs["video"] = pool.submit(videos)
        if want_content:
            jobs["content"] = pool.submit(articles)
        for key, fut in jobs.items():
            try:
                res = fut.result()
                if key == "video":
                    found, via = res
                    report["video"], report["video_via"] = store_candidates(skill, found), via
                else:
                    report["content"] = store_candidates(skill, res)
            except SearchUnavailable as exc:
                report[f"{key}_error"] = str(exc)
            except Exception as exc:  # network hiccups must never break analysis
                log.exception("Discovery (%s) failed for %s", key, slug)
                report[f"{key}_error"] = str(exc)
    # Remember empty results for a day so we don't hammer search for obscure skills.
    searched_ok = not any(k.endswith("_error") for k in report)
    if searched_ok and not report.get("video") and not report.get("content"):
        cache.set(f"disc-miss:{slug}", True, 24 * 3600)
    return report


def resources_for_skill(slug: str, limit: int = 4, allow_live: bool | None = None) -> list[dict]:
    from django.core.cache import cache

    if allow_live is None:
        allow_live = settings.SKILLMESH.get("LIVE_DISCOVERY", True)

    def fetch():
        return list(
            LearningResource.objects.filter(skill__slug=slug, is_active=True, is_free=True)
            .order_by("-quality_score")[:20]
        )

    rows = fetch()
    want_video = not any(r.kind in ("playlist", "video") for r in rows)
    want_content = sum(r.kind not in ("playlist", "video") for r in rows) < 2
    from .websearch import keyless_cooling_down

    if (allow_live and (want_video or want_content) and not cache.get(f"disc-miss:{slug}")
            and not (keyless_cooling_down() and not settings.SKILLMESH.get("YOUTUBE_API_KEY"))):
        try:
            discover_for_skill(slug, want_video=want_video, want_content=want_content)
            rows = fetch()
        except Exception:  # discovery is best-effort; never break the analysis
            log.exception("Live discovery failed for %s", slug)

    # Pick a balanced mix: best of each kind first, then fill by score.
    picked, seen_kinds = [], set()
    for r in sorted(rows, key=lambda r: -r.quality_score):
        if r.kind not in seen_kinds:
            picked.append(r)
            seen_kinds.add(r.kind)
    for r in rows:
        if len(picked) >= limit:
            break
        if r not in picked:
            picked.append(r)
    picked = picked[:limit]
    picked.sort(key=lambda r: (KIND_PRIORITY.get(r.kind, 9), -r.quality_score))
    return [r.as_dict() for r in picked]


def grouped_resources(slug: str, skill_name: str, videos: int = 3, content: int = 4,
                      allow_live: bool | None = None) -> dict:
    """Resources split for the roadmap's Video and Blog/Content tabs, with honest fallbacks."""
    rows = [] if slug.startswith("custom-") else resources_for_skill(slug, limit=videos + content + 2,
                                                                     allow_live=allow_live)
    vids = [r for r in rows if r["group"] == "video"][:videos]
    docs = [r for r in rows if r["group"] == "content"][:content]
    topics = None
    if not slug.startswith("custom-"):
        from .topics import topics_for_skill
        live = settings.SKILLMESH.get("LIVE_DISCOVERY", True) if allow_live is None else allow_live
        topics = topics_for_skill(slug, allow_live=live)
    return {
        "video": vids or [youtube_search_link(skill_name)],
        "content": docs or [web_search_link(skill_name)],
        "topics": topics,
    }


def _search_item(title: str, url: str, author: str, description: str, group: str) -> dict:
    return {
        "title": title, "url": url, "kind": "search", "kind_label": "Search (not yet vetted)",
        "group": group, "author": author, "description": description,
        "highlights": ["Not yet vetted: pick results from trusted educators"],
        "level": "beginner", "est_hours": None, "quality": None, "source": "search", "embed": None,
    }


def youtube_search_link(skill_name: str) -> dict:
    from urllib.parse import quote_plus

    q = quote_plus(f"{skill_name} full course")
    return _search_item(
        f"YouTube playlists: {skill_name} full course",
        f"https://www.youtube.com/results?search_query={q}&sp=EgIQAw%3D%3D",  # playlist filter
        "YouTube search",
        "We haven't vetted a playlist for this skill yet. This search is filtered to playlists. Prefer "
        "structured courses (10+ videos) from channels with many subscribers and recent uploads.",
        "video",
    )


def web_search_link(skill_name: str) -> dict:
    from urllib.parse import quote_plus

    q = quote_plus(f"{skill_name} official documentation tutorial")
    return _search_item(
        f"Find official docs & tutorials for {skill_name}",
        f"https://duckduckgo.com/?q={q}",
        "Web search",
        "No vetted article is cached yet. Start with the official documentation, then well-known "
        "educational sites. Avoid paywalled or SEO-farm pages.",
        "content",
    )


def fallback_search_links(skill_name: str) -> list[dict]:
    """When nothing vetted is cached, give honest search links rather than invented URLs."""
    return [youtube_search_link(skill_name), web_search_link(skill_name)]
