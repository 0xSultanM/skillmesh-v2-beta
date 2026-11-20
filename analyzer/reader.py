"""
Reader mode for the split-view content panel.

Many documentation sites forbid being framed (X-Frame-Options / CSP), so instead
of an <iframe> we fetch the article server-side (SSRF-safe), extract the main
content and return it as plain-text blocks (heading / paragraph / list item /
code / quote). The browser renders them with textContent, so no third-party
HTML or scripts ever execute inside SkillMesh. Attribution and a link to the
original are always shown.

To avoid acting as an open proxy, only URLs from our vetted resource cache or
from trusted documentation domains can be read.
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag
from django.core.cache import cache

from resources.models import LearningResource
from resources.quality import domain_trust, looks_paywalled

from .net import FetchError, safe_get

BLOCK_TAGS = ["h1", "h2", "h3", "h4", "p", "li", "pre", "blockquote", "dt", "dd"]
CONTAINER_SKIP = {"pre", "li", "blockquote"}
NOISE_RE = re.compile(r"(^|[-_ ])(nav|navbar|menu|sidebar|footer|header|cookie|consent|banner|advert|ads|promo|share|social|"
                      r"comments?|related|breadcrumbs?|newsletter|subscribe|signup|popup|modal|skip)([-_ ]|$)", re.I)
MAX_BLOCKS = 600
MAX_CHARS = 150_000
CACHE_SECONDS = 3600


class ReaderError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def reader_allowed(url: str) -> bool:
    if LearningResource.objects.filter(url=url, is_active=True).exists():
        return True
    return domain_trust(url) >= 0.8


def _text(el: Tag, keep_newlines: bool = False) -> str:
    if keep_newlines:
        return el.get_text().strip("\n")[:8000]
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()


def _pick_root(soup: BeautifulSoup) -> Tag:
    candidates = soup.find_all(["article", "main"]) + soup.find_all(attrs={"role": "main"})
    candidates += [d for d in soup.find_all(["div", "section"])
                   if re.search(r"(content|article|post|entry|markdown|docs?-?body|prose)", " ".join(d.get("class", [])) + " " + (d.get("id") or ""), re.I)]
    best, best_len = None, 0
    for c in candidates:
        n = sum(len(p.get_text(" ", strip=True)) for p in c.find_all(["p", "li", "pre"]))
        if n > best_len:
            best, best_len = c, n
    return best or soup.body or soup


def extract_article(final_url: str, html: str) -> dict:
    soup = BeautifulSoup(html or "", "html.parser")
    title = ""
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        title = og["content"]
    elif soup.title:
        title = soup.title.get_text(" ", strip=True)
    site = soup.find("meta", attrs={"property": "og:site_name"})
    site = site["content"] if site and site.get("content") else (urlsplit(final_url).hostname or "").removeprefix("www.")

    for t in soup(["script", "style", "noscript", "svg", "nav", "footer", "aside", "form", "iframe", "button", "template"]):
        t.decompose()
    for t in soup.find_all(attrs={"role": ["navigation", "banner", "contentinfo", "complementary"]}):
        t.decompose()
    for t in soup.find_all(True):
        if getattr(t, "decomposed", False) or t.attrs is None or t.name in ("article", "main", "body", "html"):
            continue
        marker = " ".join(t.get("class", [])) + " " + (t.get("id") or "")
        if not marker.strip() or not NOISE_RE.search(marker):
            continue
        # Utility-CSS class names (e.g. "max-w-sidebar") can tag layout wrappers: keep anything
        # that contains the main content or a lot of prose.
        if t.find(["article", "main"]) or len(t.get_text(" ", strip=True)) > 2500:
            continue
        t.decompose()

    root = _pick_root(soup)
    blocks, total = [], 0
    for el in root.find_all(BLOCK_TAGS):
        if any(p.name in CONTAINER_SKIP for p in el.parents if p is not root) and el.name not in CONTAINER_SKIP:
            continue  # text already captured by an enclosing li/pre/blockquote
        if el.name in ("li", "blockquote") and any(p.name == el.name for p in el.parents if p is not root):
            continue
        if el.name == "pre":
            txt, kind = _text(el, keep_newlines=True), "code"
        else:
            txt = _text(el)
            kind = {"h1": "h2", "h2": "h2", "h3": "h3", "h4": "h3", "li": "li", "blockquote": "quote",
                    "dt": "h3", "dd": "p"}.get(el.name, "p")
        if not txt or (kind == "p" and len(txt) < 2):
            continue
        if blocks and blocks[-1]["text"] == txt:
            continue
        block = {"type": kind, "text": txt}
        if kind == "li":
            block["ordered"] = el.parent is not None and el.parent.name == "ol"
        if kind in ("p", "li"):
            a = el.find("a", href=True)
            if a and len(_text(a)) > 0.6 * len(txt):  # paragraph that is essentially a link
                href = urljoin(final_url, a["href"])
                if href.startswith(("http://", "https://")):
                    block["href"] = href
        blocks.append(block)
        total += len(txt)
        if len(blocks) >= MAX_BLOCKS or total >= MAX_CHARS:
            break

    words = sum(len(b["text"].split()) for b in blocks if b["type"] != "code")
    return {"title": title.strip()[:300], "site": site[:120], "url": final_url, "blocks": blocks,
            "words": words, "minutes": max(1, round(words / 200))}


def framable(url: str, headers: dict | None) -> bool:
    """Can this page be shown inside our <iframe>? (X-Frame-Options / CSP frame-ancestors)"""
    if not url.startswith("https://"):  # http would be blocked as mixed content
        return False
    h = {k.lower(): v for k, v in (headers or {}).items()}
    if h.get("x-frame-options", "").strip():
        return False
    csp = h.get("content-security-policy", "")
    m = re.search(r"frame-ancestors\s+([^;]+)", csp, re.I)
    if m:
        sources = m.group(1).split()
        return "*" in sources or "https:" in sources
    return True


def read(url: str) -> dict:
    """
    Prepare a resource for the in-app content popup.

    Returns ``{url, final_url, title, site, framable, article, reader_error}``: the popup
    shows the original page in a frame when the site allows it, and otherwise (or on
    request) the extracted reader view. Raises ReaderError only when neither works.
    """
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ReaderError("invalid_url", "Only web links can be opened in the reader.")
    if not reader_allowed(url):
        raise ReaderError("not_allowed", "This page isn't in our vetted library, so open it in a new tab instead.")
    key = "reader2:" + hashlib.sha256(url.encode()).hexdigest()
    if (hit := cache.get(key)) is not None:
        return hit
    try:
        page = safe_get(url, max_bytes=3_000_000, timeout=12)
    except FetchError as exc:
        raise ReaderError(exc.code, exc.message)
    if page.status >= 400:
        raise ReaderError("unreachable", f"The site returned HTTP {page.status}.")
    if looks_paywalled(page.text):
        raise ReaderError("paywalled", "This article is behind a paywall.")
    can_frame = framable(page.final_url, page.headers)
    article, reader_error = None, None
    if "html" in (page.content_type or "html"):
        art = extract_article(page.final_url, page.text)
        if art["words"] >= 60:
            article = art
        else:
            reader_error = "This page is interactive, so there's no reader view."
    if not article and not can_frame:
        raise ReaderError("unreadable", "This site can't be shown inside SkillMesh. Open it in a new tab.")
    result = {"url": url, "final_url": page.final_url, "framable": can_frame,
              "title": (article or {}).get("title", ""), "site": (article or {}).get("site", "") or
              (urlsplit(page.final_url).hostname or "").removeprefix("www."),
              "article": article, "reader_error": reader_error}
    cache.set(key, result, CACHE_SECONDS)
    return result
