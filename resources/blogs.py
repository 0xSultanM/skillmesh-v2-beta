"""
Article / tutorial discovery via web search (Brave API if configured, otherwise
keyless), ranked by domain trust, with paywalled or dead pages filtered out by
an actual fetch.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from analyzer.net import FetchError, safe_get

from .quality import domain_of, domain_trust, looks_paywalled, score_article
from .websearch import SearchUnavailable, search  # noqa: F401  (re-exported for callers)

log = logging.getLogger(__name__)


def _verify(url: str) -> bool:
    try:
        page = safe_get(url, max_bytes=1_500_000, timeout=8)
    except FetchError:
        return False
    return page.status < 400 and not looks_paywalled(page.text)


def discover_articles(skill_name: str, skill_terms: list[str], limit: int = 5, verify: bool = True) -> list[dict]:
    raw = search(f"{skill_name} tutorial guide documentation", 15)
    candidates = []
    for item in raw:
        url, title = item.get("url", ""), item.get("title", "")
        if not url.startswith("https://") or "youtube.com" in url or "duckduckgo.com" in url:
            continue
        score = score_article(url, title, skill_terms, item.get("page_age"))
        if score >= 0.5:
            candidates.append((score, url, title, item))
    candidates.sort(key=lambda c: c[0], reverse=True)

    # Diversity first (max 2 per site), then verify the shortlist in parallel.
    shortlist, per_site = [], {}
    for c in candidates:
        host = domain_of(c[1])
        if per_site.get(host, 0) < 2:
            per_site[host] = per_site.get(host, 0) + 1
            shortlist.append(c)
        if len(shortlist) >= limit + 3:
            break
    if verify and shortlist:
        with ThreadPoolExecutor(max_workers=4) as pool:
            ok = list(pool.map(lambda c: _verify(c[1]), shortlist))
        shortlist = [c for c, good in zip(shortlist, ok) if good]

    results = []
    for score, url, title, item in shortlist[:limit]:
        host = domain_of(url)
        trust = domain_trust(url)
        results.append({
            "kind": "docs" if host.startswith("docs.") or trust >= 1.0 else "article",
            "source": "web",
            "title": title[:300],
            "url": url,
            "author": host.removeprefix("www."),
            "description": (item.get("snippet") or "")[:1000],
            "quality_score": score,
            "metadata": {"domain": host, "domain_trust": trust, "page_age": item.get("page_age")},
        })
    return results
