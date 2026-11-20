"""
Extract a job posting from a public web page.

Cascade (cheapest and most reliable first):
1. schema.org ``JobPosting`` JSON-LD (most public job boards ship it)
2. Main-content heuristics (largest text block after stripping chrome)

Pages behind a login wall are detected and reported so the UI can ask the
user to paste the description instead.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

LOGIN_URL_HINTS = ("login", "signin", "sign-in", "authwall", "/auth", "checkpoint", "accounts.google")
LOGIN_TEXT_HINTS = ("sign in to view", "log in to view", "join to view", "sign in to continue",
                    "please log in", "you must be logged in", "create an account to")
MIN_JOB_TEXT = 300


class LoginRequired(Exception):
    pass


class NoJobFound(Exception):
    pass


@dataclass
class JobPage:
    title: str = ""
    company: str = ""
    location: str = ""
    text: str = ""
    method: str = ""
    extra: dict = field(default_factory=dict)


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for li in soup.find_all("li"):
        li.insert_before("\n• ")
    for tag in soup.find_all(["p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "section"]):
        tag.insert_after("\n")
    text = soup.get_text()
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def _iter_jsonld(soup: BeautifulSoup):
    for tag in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        raw = tag.string or tag.get_text() or ""
        try:
            data = json.loads(raw.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                yield node
                if "@graph" in node:
                    stack.append(node["@graph"])


def _is_job_posting(node: dict) -> bool:
    t = node.get("@type")
    types = t if isinstance(t, list) else [t]
    return any(isinstance(x, str) and x.lower() == "jobposting" for x in types)


def _name(v) -> str:
    if isinstance(v, dict):
        return str(v.get("name", "") or "")
    if isinstance(v, list) and v:
        return _name(v[0])
    return str(v or "")


def _location(v) -> str:
    if isinstance(v, list):
        return "; ".join(filter(None, (_location(x) for x in v)))
    if isinstance(v, dict):
        addr = v.get("address", v)
        if isinstance(addr, dict):
            parts = [addr.get("addressLocality"), addr.get("addressRegion"), _name(addr.get("addressCountry"))]
            return ", ".join(p for p in parts if p)
    return str(v or "") if isinstance(v, str) else ""


def from_jsonld(soup: BeautifulSoup) -> JobPage | None:
    for node in _iter_jsonld(soup):
        if not _is_job_posting(node):
            continue
        desc = html_to_text(node.get("description", ""))
        sections = [desc]
        for key, label in (("qualifications", "Qualifications"), ("skills", "Skills"),
                           ("experienceRequirements", "Experience"), ("educationRequirements", "Education"),
                           ("responsibilities", "Responsibilities")):
            val = node.get(key)
            if isinstance(val, dict):
                val = val.get("description") or val.get("name") or ""
            if isinstance(val, list):
                val = "\n".join(_name(v) if isinstance(v, dict) else str(v) for v in val)
            if val:
                sections.append(f"{label}:\n{html_to_text(str(val))}")
        text = "\n\n".join(s for s in sections if s).strip()
        if len(text) < 40:
            continue
        loc = _location(node.get("jobLocation"))
        if node.get("jobLocationType") == "TELECOMMUTE":
            loc = (loc + " (Remote)").strip()
        return JobPage(
            title=str(node.get("title", "")).strip(),
            company=_name(node.get("hiringOrganization")).strip(),
            location=loc,
            text=text,
            method="json-ld",
            extra={"employment_type": node.get("employmentType"), "date_posted": node.get("datePosted")},
        )
    return None


def from_main_content(soup: BeautifulSoup) -> JobPage | None:
    for tag in soup(["script", "style", "noscript", "svg", "nav", "header", "footer", "aside", "form", "iframe"]):
        tag.decompose()
    candidates = soup.find_all(["main", "article"]) or []
    candidates += [d for d in soup.find_all(["div", "section"])
                   if re.search(r"job|description|posting|vacancy|content", " ".join(d.get("class", [])) + " " + (d.get("id") or ""), re.I)]
    best, best_len = None, 0
    for c in candidates or [soup.body or soup]:
        text = html_to_text(str(c))
        if len(text) > best_len:
            best, best_len = text, len(text)
    if not best or best_len < MIN_JOB_TEXT:
        return None
    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(" ", strip=True)
    elif soup.title:
        title = soup.title.get_text(" ", strip=True)
    return JobPage(title=title[:200], text=best[:30_000], method="main-content")


def detect_login_wall(final_url: str, html: str, soup: BeautifulSoup) -> bool:
    url = final_url.lower()
    if any(h in url for h in LOGIN_URL_HINTS):
        return True
    visible = soup.get_text(" ", strip=True).lower()
    has_password = bool(soup.find("input", attrs={"type": "password"}))
    if has_password and len(visible) < 3000:
        return True
    return any(h in visible for h in LOGIN_TEXT_HINTS) and len(visible) < 5000


def extract_job_page(final_url: str, html: str) -> JobPage:
    soup = BeautifulSoup(html or "", "html.parser")
    job = from_jsonld(soup)
    if job:
        return job
    if detect_login_wall(final_url, html, soup):
        raise LoginRequired()
    job = from_main_content(soup)
    if not job:
        raise NoJobFound()
    return job
