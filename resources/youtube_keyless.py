"""
YouTube course discovery without a YouTube Data API key.

1. Web search (``websearch``) for "site:youtube.com <skill> full course".
2. Every candidate is verified with YouTube's official, keyless **oEmbed**
   endpoint: it confirms the video/playlist exists, is public and embeddable
   (needed for the in-app player) and returns its title and channel.
3. Candidates are scored on channel trust (allow/block list), relevance,
   course structure and clickbait signals, then cached in SQLite.

We do not scrape YouTube's own pages. With ``YOUTUBE_API_KEY`` set, the richer
Data API pipeline in ``youtube.py`` is used instead (subscriber counts,
duplication and engagement signals).
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit

import requests

from .quality import score_youtube_keyless
from .websearch import SearchUnavailable, search

OEMBED = "https://www.youtube.com/oembed"
YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtube-nocookie.com"}
_ID = re.compile(r"^[A-Za-z0-9_-]{6,64}$")


def parse_youtube(url: str) -> dict | None:
    """Return {"kind": "playlist"|"video", "id": ..., "url": canonical} for a YouTube URL, else None."""
    try:
        parts = urlsplit(url or "")
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if host not in YT_HOSTS:
        return None
    q = parse_qs(parts.query)
    lst = (q.get("list") or [""])[0]
    if lst and _ID.match(lst) and not lst.startswith("RD"):  # skip auto-generated mixes
        return {"kind": "playlist", "id": lst, "url": f"https://www.youtube.com/playlist?list={lst}"}
    vid = ""
    if host == "youtu.be":
        vid = parts.path.strip("/").split("/")[0]
    elif parts.path == "/watch":
        vid = (q.get("v") or [""])[0]
    elif parts.path.startswith(("/embed/", "/live/")):
        vid = parts.path.split("/")[2]
    if vid and _ID.match(vid):
        return {"kind": "video", "id": vid, "url": f"https://www.youtube.com/watch?v={vid}"}
    return None


def oembed(url: str) -> dict | None:
    """Official keyless metadata; None if private, removed or not embeddable."""
    try:
        r = requests.get(OEMBED, params={"url": url, "format": "json"}, timeout=8)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def _trust_lookup() -> dict[str, str]:
    from .models import Channel

    return {c.title.strip().lower(): c.trust for c in Channel.objects.all()}


def discover_videos(skill_name: str, skill_terms: list[str], limit: int = 3) -> list[dict]:
    candidates: dict[str, dict] = {}
    for q in (f"site:youtube.com {skill_name} full course", f"site:youtube.com {skill_name} tutorial playlist"):
        try:
            results = search(q, 15)
        except SearchUnavailable:
            if not candidates:
                raise  # let the caller know search was unavailable (not "nothing found")
            break
        for r in results:
            if "/shorts/" in r["url"]:
                continue
            yt = parse_youtube(r["url"])
            if yt and yt["url"] not in candidates:
                candidates[yt["url"]] = yt
        if len(candidates) >= 6:
            break
    if not candidates:
        return []

    items = list(candidates.values())[:10]
    with ThreadPoolExecutor(max_workers=5) as pool:
        metas = list(pool.map(lambda yt: oembed(yt["url"]), items))

    trust = _trust_lookup()
    out = []
    for yt, meta in zip(items, metas):
        if not meta:
            continue
        title, channel = meta.get("title", ""), meta.get("author_name", "")
        t = trust.get(channel.strip().lower())
        score = score_youtube_keyless(title, yt["kind"], t, skill_terms)
        if score < 0.5:
            continue
        out.append({
            "kind": yt["kind"], "source": "youtube", "title": title[:300], "url": yt["url"], "author": channel[:200],
            "description": (f"{'Playlist' if yt['kind'] == 'playlist' else 'Video'} by {channel}"
                            f"{' (trusted educator)' if t == 'allow' else ''}. Verified public and embeddable: "
                            "play it inside SkillMesh and your watch time counts toward your daily streak."),
            "quality_score": score,
            "metadata": {f"{yt['kind']}_id": yt["id"], "channel_url": meta.get("author_url", ""),
                         "thumbnail_url": meta.get("thumbnail_url", ""), "trusted": t == "allow",
                         "discovered_via": "web-search+oembed"},
        })
    out.sort(key=lambda c: c["quality_score"], reverse=True)
    return out[:limit]
