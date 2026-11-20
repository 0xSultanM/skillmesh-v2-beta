"""
YouTube Data API v3 discovery of high-quality course playlists.

Quota note: ``search.list`` costs 100 units (default daily quota 10,000), so
discovery runs per *skill* and results are cached in SQLite. Users never
trigger a search for content that is already cached.
"""
from __future__ import annotations

import logging
from statistics import mean

import requests
from django.conf import settings

from .quality import score_youtube_playlist

log = logging.getLogger(__name__)
API = "https://www.googleapis.com/youtube/v3"
TIMEOUT = 10


class YouTubeUnavailable(Exception):
    pass


def _key() -> str:
    key = settings.SKILLMESH.get("YOUTUBE_API_KEY")
    if not key:
        raise YouTubeUnavailable("YOUTUBE_API_KEY is not configured.")
    return key


def _get(endpoint: str, **params) -> dict:
    params["key"] = _key()
    try:
        r = requests.get(f"{API}/{endpoint}", params=params, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise YouTubeUnavailable(str(exc)) from exc
    if r.status_code != 200:
        # Don't log the full URL - it contains the API key.
        raise YouTubeUnavailable(f"YouTube API {endpoint} returned HTTP {r.status_code}")
    return r.json()


def _chunks(seq, n=50):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _trust_lookup() -> tuple[dict[str, str], dict[str, str]]:
    from .models import Channel

    by_id, by_title = {}, {}
    for ch in Channel.objects.all():
        if ch.channel_id:
            by_id[ch.channel_id] = ch.trust
        by_title[ch.title.strip().lower()] = ch.trust
    return by_id, by_title


def discover_playlists(skill_name: str, skill_terms: list[str], max_results: int = 10,
                       sample_videos: int = 8) -> list[dict]:
    """Return candidate playlists sorted by quality score (rejected ones removed)."""
    search = _get(
        "search", part="snippet", type="playlist", q=f"{skill_name} full course tutorial",
        maxResults=max_results, relevanceLanguage="en", safeSearch="strict",
    )
    hits = [it for it in search.get("items", []) if it.get("id", {}).get("playlistId")]
    if not hits:
        return []

    playlist_ids = [h["id"]["playlistId"] for h in hits]
    channel_ids = list({h["snippet"]["channelId"] for h in hits})

    counts = {}
    for chunk in _chunks(playlist_ids):
        for p in _get("playlists", part="contentDetails", id=",".join(chunk)).get("items", []):
            counts[p["id"]] = int(p["contentDetails"].get("itemCount", 0))

    channels = {}
    for chunk in _chunks(channel_ids):
        for c in _get("channels", part="statistics", id=",".join(chunk)).get("items", []):
            st = c.get("statistics", {})
            channels[c["id"]] = {
                "subscriber_count": 0 if st.get("hiddenSubscriberCount") else int(st.get("subscriberCount", 0)),
                "channel_video_count": int(st.get("videoCount", 0)),
                "channel_view_count": int(st.get("viewCount", 0)),
            }

    by_id, by_title = _trust_lookup()
    results = []
    for h in hits:
        pid = h["id"]["playlistId"]
        sn = h["snippet"]
        ch_id = sn["channelId"]
        trust = by_id.get(ch_id) or by_title.get(sn.get("channelTitle", "").strip().lower())
        if trust == "block" or counts.get(pid, 0) < 3:
            continue

        # Sample items for duplication + engagement signals (1 unit + 1 unit).
        items = _get("playlistItems", part="snippet,contentDetails", playlistId=pid, maxResults=25).get("items", [])
        titles = [i["snippet"].get("title", "") for i in items]
        video_ids = [i["contentDetails"]["videoId"] for i in items if i.get("contentDetails", {}).get("videoId")]
        like_ratios = []
        if video_ids:
            vids = _get("videos", part="statistics", id=",".join(video_ids[:sample_videos])).get("items", [])
            for v in vids:
                st = v.get("statistics", {})
                views, likes = int(st.get("viewCount", 0)), int(st.get("likeCount", 0) or 0)
                if views > 0:
                    like_ratios.append(likes / views)

        meta = {
            "title": sn.get("title", ""),
            "item_count": counts.get(pid, 0),
            "published_at": sn.get("publishedAt"),
            "item_titles": titles,
            "avg_like_ratio": mean(like_ratios) if like_ratios else None,
            **channels.get(ch_id, {}),
        }
        score = score_youtube_playlist(meta, skill_terms, trust)
        if score < 0.45:
            continue
        results.append({
            "kind": "playlist",
            "source": "youtube",
            "title": sn.get("title", "")[:300],
            "url": f"https://www.youtube.com/playlist?list={pid}",
            "author": sn.get("channelTitle", "")[:200],
            "description": (sn.get("description") or "")[:1000],
            "quality_score": score,
            "metadata": {
                "playlist_id": pid,
                "channel_id": ch_id,
                "item_count": meta["item_count"],
                "subscribers": meta.get("subscriber_count"),
                "avg_like_ratio": meta["avg_like_ratio"],
                "published_at": meta["published_at"],
                "trusted": trust == "allow",
            },
        })
    results.sort(key=lambda r: r["quality_score"], reverse=True)
    return results
