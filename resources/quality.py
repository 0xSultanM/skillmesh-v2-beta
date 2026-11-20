"""
Pure scoring functions for learning resources (no I/O, fully unit-testable).

YouTube playlists are scored on signals that separate structured, informative
courses from generic or repetitive uploads:

* channel credibility (subscribers, views-per-video, trust list)
* structure (a reasonable number of videos forming a course)
* low title duplication (repeated / re-uploaded content is penalised)
* engagement (likes per view on sampled videos)
* topical relevance of the title
* recency (stale tech content is down-weighted, gently)
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from urllib.parse import urlsplit

# Sites with a strong track record for free, accurate technical content.
TRUSTED_DOMAINS: dict[str, float] = {
    "developer.mozilla.org": 1.0,
    "web.dev": 0.95,
    "docs.python.org": 1.0,
    "realpython.com": 0.9,
    "freecodecamp.org": 0.9,
    "www.freecodecamp.org": 0.9,
    "javascript.info": 0.95,
    "react.dev": 1.0,
    "nextjs.org": 1.0,
    "vuejs.org": 1.0,
    "angular.dev": 1.0,
    "nodejs.org": 1.0,
    "typescriptlang.org": 1.0,
    "www.typescriptlang.org": 1.0,
    "docs.djangoproject.com": 1.0,
    "fastapi.tiangolo.com": 1.0,
    "go.dev": 1.0,
    "doc.rust-lang.org": 1.0,
    "dev.java": 1.0,
    "learn.microsoft.com": 0.95,
    "kubernetes.io": 1.0,
    "docs.docker.com": 1.0,
    "git-scm.com": 1.0,
    "www.postgresql.org": 1.0,
    "developers.google.com": 0.95,
    "cloud.google.com": 0.9,
    "aws.amazon.com": 0.85,
    "css-tricks.com": 0.85,
    "www.smashingmagazine.com": 0.85,
    "martinfowler.com": 0.9,
    "roadmap.sh": 0.85,
    "www.theodinproject.com": 0.95,
    "cs50.harvard.edu": 1.0,
    "ocw.mit.edu": 1.0,
    "www.khanacademy.org": 0.9,
    "course.fast.ai": 0.95,
    "huggingface.co": 0.9,
    "scikit-learn.org": 1.0,
    "pandas.pydata.org": 1.0,
    "pytorch.org": 1.0,
    "www.tensorflow.org": 1.0,
    "owasp.org": 1.0,
    "sqlbolt.com": 0.85,
    "linuxjourney.com": 0.85,
    "jvns.ca": 0.85,
    "overreacted.io": 0.85,
    "kentcdodds.com": 0.8,
    "joshwcomeau.com": 0.85,
    "dev.to": 0.55,
    "hashnode.dev": 0.5,
    "medium.com": 0.45,
}

# Content-farm patterns; also anything paywalled is removed outright.
LOW_QUALITY_DOMAINS = {"w3schools.in", "tutorialspoint.dev", "javatpoint.in"}

PAYWALL_MARKERS = (
    "member-only story",
    "this story is only available to members",
    "subscribe to continue reading",
    "become a paid subscriber",
    "you've reached your free article limit",
    "\"isaccessibleforfree\":false",
    "\"isaccessibleforfree\": false",
)

CLICKBAIT_PATTERNS = re.compile(
    r"(in \d+ (minutes|mins|seconds|hours?)\b.*!|you won'?t believe|\bshocking\b|#shorts|\bmust watch\b|\bget rich\b)",
    re.IGNORECASE,
)


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def domain_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def domain_trust(url: str) -> float:
    host = domain_of(url)
    if host in LOW_QUALITY_DOMAINS:
        return 0.0
    if host in TRUSTED_DOMAINS:
        return TRUSTED_DOMAINS[host]
    bare = host.removeprefix("www.")
    if bare in TRUSTED_DOMAINS:
        return TRUSTED_DOMAINS[bare]
    if host.endswith(".medium.com"):
        return TRUSTED_DOMAINS["medium.com"]
    if host.endswith(".dev.to") or host.endswith(".hashnode.dev"):
        return 0.5
    if host.endswith(".edu") or host.endswith(".gov"):
        return 0.85
    if host.startswith("docs.") or host.startswith("learn."):
        return 0.8  # first-party documentation sites
    return 0.4


def looks_paywalled(html: str) -> bool:
    low = (html or "")[:400_000].lower()
    return any(marker in low for marker in PAYWALL_MARKERS)


def duplication_ratio(titles: list[str], threshold: float = 0.88) -> float:
    """Share of titles that are near-duplicates of an earlier title (0 = all unique)."""
    norm = [re.sub(r"[\d#|:\-–()\[\]]+", " ", t.lower()).strip() for t in titles if t]
    norm = [re.sub(r"\s+", " ", t) for t in norm]
    if len(norm) < 2:
        return 0.0
    dupes = 0
    for i, t in enumerate(norm):
        for prev in norm[max(0, i - 25):i]:
            if t == prev or SequenceMatcher(None, t, prev).ratio() >= threshold:
                dupes += 1
                break
    return dupes / len(norm)


def relevance(title: str, skill_terms: list[str]) -> float:
    low = (title or "").lower()
    if any(term.lower() in low for term in skill_terms if term):
        return 1.0
    return 0.3


def recency_factor(published_at: str | None, now: datetime | None = None) -> float:
    if not published_at:
        return 0.7
    try:
        dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return 0.7
    now = now or datetime.now(timezone.utc)
    years = max(0.0, (now - dt).days / 365.25)
    # Full marks for < 2 years, gently decays to 0.4 by ~8 years.
    return clamp(1.0 - max(0.0, years - 2) * 0.1, 0.4, 1.0)


def score_youtube_playlist(meta: dict, skill_terms: list[str], trust: str | None = None) -> float:
    """
    ``meta`` keys: title, item_count, subscriber_count, channel_video_count,
    channel_view_count, published_at, item_titles (list), avg_like_ratio (likes/views, optional).
    ``trust``: "allow" / "block" / None.
    Returns 0..1 (0 means reject).
    """
    if trust == "block":
        return 0.0
    title = meta.get("title", "")
    if CLICKBAIT_PATTERNS.search(title):
        return 0.0

    items = int(meta.get("item_count") or 0)
    if items < 3:  # not a course
        return 0.0
    # Courses of ~8-80 videos are ideal; giant dumping-ground playlists are penalised.
    if items <= 80:
        structure = clamp(0.5 + 0.5 * min(items, 8) / 8)
    else:
        structure = clamp(1.0 - (items - 80) / 300, 0.3, 1.0)

    subs = int(meta.get("subscriber_count") or 0)
    channel_rep = clamp((math.log10(subs + 1) - 3) / 3.5)  # 1k subs => 0, ~3M => 1.0

    vids = max(1, int(meta.get("channel_video_count") or 1))
    views = int(meta.get("channel_view_count") or 0)
    # Channels that upload thousands of low-view videos are typically content farms.
    views_per_video = views / vids
    depth = clamp((math.log10(views_per_video + 1) - 2) / 3.5)  # 100 views/video => 0, ~300k => 1.0
    mass_uploader = vids > 2000 and views_per_video < 1000

    dup = duplication_ratio(meta.get("item_titles") or [])
    originality = clamp(1.0 - dup * 1.5)

    like_ratio = meta.get("avg_like_ratio")
    engagement = clamp((like_ratio or 0.0) / 0.04) if like_ratio is not None else 0.5

    rel = relevance(title, skill_terms)
    rec = recency_factor(meta.get("published_at"))

    score = (
        0.22 * channel_rep
        + 0.15 * depth
        + 0.15 * structure
        + 0.18 * originality
        + 0.12 * engagement
        + 0.10 * rel
        + 0.08 * rec
    )
    if trust == "allow":
        score = score * 0.7 + 0.3  # trusted educators get a strong floor
    if dup > 0.5:
        score *= 0.5
    if mass_uploader and trust != "allow":
        score *= 0.6
    return round(clamp(score), 4)


def score_article(url: str, title: str, skill_terms: list[str], age_published: str | None = None) -> float:
    trust = domain_trust(url)
    if trust == 0.0 or CLICKBAIT_PATTERNS.search(title or ""):
        return 0.0
    score = 0.65 * trust + 0.2 * relevance(title, skill_terms) + 0.15 * recency_factor(age_published)
    return round(clamp(score), 4)


_COURSE_WORDS = re.compile(r"full course|complete (course|guide)|crash course|bootcamp|for beginners|tutorial|zero to hero|masterclass", re.I)
_NOISE_WORDS = re.compile(r"#shorts|\breaction\b|\bvs\.?\s|tier list|\bnews\b|\bdrama\b|\bpodcast\b|\bmemes?\b", re.I)


def score_youtube_keyless(title: str, kind: str, trust: str | None, skill_terms: list[str]) -> float:
    """Score a search-discovered, oEmbed-verified video/playlist (no view/like stats available)."""
    clickbait = bool(CLICKBAIT_PATTERNS.search(title or ""))
    if trust == "block" or (clickbait and trust != "allow"):
        return 0.0
    score = 0.35
    if trust == "allow":
        score += 0.35
        if clickbait:  # trusted educators get the benefit of the doubt on hype-y titles
            score -= 0.2
    if relevance(title, skill_terms) == 1.0:
        score += 0.12
    else:
        score -= 0.15
    if kind == "playlist":
        score += 0.08
    if _COURSE_WORDS.search(title or ""):
        score += 0.07
    if _NOISE_WORDS.search(title or ""):
        score -= 0.3
    return round(clamp(score), 4)
