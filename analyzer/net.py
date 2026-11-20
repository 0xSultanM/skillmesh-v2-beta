"""
Safe outbound HTTP for user-supplied URLs.

Guards against SSRF: only http(s) on standard ports, every hop (including
redirects) must resolve exclusively to public IP addresses, response size and
time are capped. Nothing fetched is persisted.

Known limitation: there is a small DNS-rebinding window between our resolution
check and the socket connect made by ``requests``. Deploy behind an egress
proxy / firewall that blocks private ranges for defence in depth.
"""
from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import requests
from django.conf import settings

ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_PORTS = {None, 80, 443}
MAX_REDIRECTS = 5


class FetchError(Exception):
    """Raised for any URL we refuse or fail to fetch. ``code`` is user-facing."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    content_type: str
    text: str
    headers: dict | None = None


def _cfg(key):
    return settings.SKILLMESH[key]


def is_public_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global and not (
        addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast
        or addr.is_reserved or addr.is_unspecified
    )


def validate_url(url: str) -> str:
    """Return a normalised URL or raise ``FetchError``."""
    url = (url or "").strip()
    if len(url) > 2048:
        raise FetchError("invalid_url", "URL is too long.")
    parts = urlsplit(url)
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise FetchError("invalid_url", "Only http(s) links are supported.")
    if not parts.hostname:
        raise FetchError("invalid_url", "That doesn't look like a valid link.")
    if parts.username or parts.password:
        raise FetchError("invalid_url", "Links with embedded credentials are not allowed.")
    try:
        port = parts.port
    except ValueError:
        raise FetchError("invalid_url", "Invalid port.")
    if port not in ALLOWED_PORTS:
        raise FetchError("blocked_url", "Only standard web ports are allowed.")

    host = parts.hostname
    try:
        infos = socket.getaddrinfo(host, port or (443 if parts.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise FetchError("unreachable", "Could not resolve that website.")
    ips = {info[4][0] for info in infos}
    if not ips or not all(is_public_ip(ip) for ip in ips):
        raise FetchError("blocked_url", "That address is not a public website.")
    return url


def safe_get(url: str, *, max_bytes: int | None = None, timeout: float | None = None,
             headers: dict | None = None) -> FetchResult:
    """GET a public URL, following redirects manually with validation at every hop."""
    max_bytes = max_bytes or _cfg("SCRAPE_MAX_BYTES")
    timeout = timeout or _cfg("SCRAPE_TIMEOUT")
    hdrs = {
        "User-Agent": _cfg("SCRAPE_USER_AGENT"),
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.5",
        "Accept-Language": "en;q=0.9",
    }
    hdrs.update(headers or {})

    original = url
    current = validate_url(url)
    with requests.Session() as session:
        for _ in range(MAX_REDIRECTS + 1):
            try:
                resp = session.get(current, headers=hdrs, timeout=timeout, stream=True, allow_redirects=False)
            except requests.Timeout:
                raise FetchError("timeout", "The website took too long to respond.")
            except requests.RequestException:
                raise FetchError("unreachable", "Could not connect to that website.")

            if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location")
                resp.close()
                if not location:
                    raise FetchError("unreachable", "Broken redirect.")
                current = validate_url(urljoin(current, location))
                continue

            try:
                chunks, size = [], 0
                for chunk in resp.iter_content(chunk_size=16384):
                    size += len(chunk)
                    if size > max_bytes:
                        raise FetchError("too_large", "That page is too large to analyse.")
                    chunks.append(chunk)
            finally:
                resp.close()

            raw = b"".join(chunks)
            # requests assumes ISO-8859-1 for text/* without a charset; modern pages are UTF-8.
            declared = "charset=" in (resp.headers.get("Content-Type", "").lower())
            encoding = (resp.encoding if declared else None) or "utf-8"
            try:
                text = raw.decode(encoding, errors="replace")
            except LookupError:
                text = raw.decode("utf-8", errors="replace")
            return FetchResult(
                url=original,
                final_url=current,
                status=resp.status_code,
                content_type=resp.headers.get("Content-Type", ""),
                text=text,
                headers={k.lower(): v for k, v in resp.headers.items()},
            )
    raise FetchError("unreachable", "Too many redirects.")
