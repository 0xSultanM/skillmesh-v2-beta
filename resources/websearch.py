"""
Web search for resource discovery, with or without API keys.

Provider chain:
1. Brave Search API, when ``BRAVE_SEARCH_API_KEY`` is set (reliable, recommended in production)
2. DuckDuckGo's HTML endpoint (keyless). It throttles bursts (HTTP 202), so calls
   are serialised with a minimum interval and results are cached by the caller
   (SQLite) and here (in-process, 6 h), so each skill is searched rarely.

Only public search-result metadata is used: URL, title and snippet.
"""
from __future__ import annotations

import hashlib
import html
import logging
import re
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit

import requests
from django.conf import settings
from django.core.cache import cache

log = logging.getLogger(__name__)

BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
DDG_URL = "https://html.duckduckgo.com/html/"
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
MIN_INTERVAL = 2.5  # seconds between keyless queries
CACHE_SECONDS = 6 * 3600
BREAKER_KEY = "ws:throttled"
BREAKER_SECONDS = 600  # pause keyless search after being throttled

_lock = threading.Lock()
_last_call = 0.0

_RESULT_RE = re.compile(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=class="result__a"|$)', re.S)
_SNIPPET_RE = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.S)


class SearchUnavailable(Exception):
    pass


def _strip(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s or ""))).strip()


def _unwrap_ddg(href: str) -> str:
    href = html.unescape(href)
    if "uddg=" in href:
        return unquote(parse_qs(urlsplit(href if href.startswith("http") else "https:" + href).query).get("uddg", [""])[0])
    return href


def parse_ddg_html(page: str) -> list[dict]:
    out = []
    for href, title, rest in _RESULT_RE.findall(page):
        url = _unwrap_ddg(href)
        if not url.startswith("http") or "duckduckgo.com/y.js" in url:  # skip ads
            continue
        snip = _SNIPPET_RE.search(rest)
        out.append({"url": url, "title": _strip(title), "snippet": _strip(snip.group(1)) if snip else ""})
    return out


def _brave(query: str, count: int, key: str) -> list[dict]:
    try:
        r = requests.get(BRAVE_URL, params={"q": query, "count": count, "safesearch": "strict"},
                         headers={"X-Subscription-Token": key, "Accept": "application/json"}, timeout=10)
    except requests.RequestException as exc:
        raise SearchUnavailable(str(exc)) from exc
    if r.status_code != 200:
        raise SearchUnavailable(f"Brave returned HTTP {r.status_code}")
    return [{"url": i.get("url", ""), "title": i.get("title", ""), "snippet": i.get("description", ""),
             "page_age": i.get("page_age")} for i in r.json().get("web", {}).get("results", [])]


def _ddg(query: str) -> list[dict]:
    global _last_call
    if cache.get(BREAKER_KEY):
        raise SearchUnavailable("Keyless search is cooling down after being throttled")
    with _lock:  # serialise keyless queries to stay polite
        for attempt in range(2):
            wait = MIN_INTERVAL - (time.monotonic() - _last_call)
            if wait > 0:
                time.sleep(wait)
            try:
                r = requests.get(DDG_URL, params={"q": query}, headers={"User-Agent": BROWSER_UA, "Accept-Language": "en"},
                                 timeout=12)
            except requests.RequestException as exc:
                raise SearchUnavailable(str(exc)) from exc
            finally:
                _last_call = time.monotonic()
            if r.status_code == 200:
                return parse_ddg_html(r.text)
            if r.status_code == 202 and attempt == 0:  # throttled: back off once
                time.sleep(4)
                continue
            if r.status_code == 202:
                cache.set(BREAKER_KEY, True, BREAKER_SECONDS)
            raise SearchUnavailable(f"Keyless search throttled (HTTP {r.status_code})")
    return []


def search(query: str, count: int = 12) -> list[dict]:
    key = "ws:" + hashlib.sha256(f"{query.lower()}|{count}".encode()).hexdigest()
    if (hit := cache.get(key)) is not None:
        return hit
    brave_key = settings.SKILLMESH.get("BRAVE_SEARCH_API_KEY")
    results = _brave(query, count, brave_key) if brave_key else _ddg(query)[:count]
    cache.set(key, results, CACHE_SECONDS)
    return results


def keyless_cooling_down() -> bool:
    return not settings.SKILLMESH.get("BRAVE_SEARCH_API_KEY") and bool(cache.get(BREAKER_KEY))
