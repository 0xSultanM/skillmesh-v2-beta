"""
"Sign in with GitHub" (OAuth App) used *only* to lift GitHub's API rate limit.

* No scopes are requested: the token can read public data only, which is all
  the analyzer needs.
* The token lives in the user's signed, HttpOnly session cookie (no database).
* Anonymous visitors get ``GITHUB_FREE_ANALYSES`` analyses (default 1), tracked
  per browser session and per IP; the allowance resets after 24 h, and until
  then sign-in is required for more.
* Signing out revokes the grant on GitHub (best effort) and clears the cookie.
"""
from __future__ import annotations

import hmac
import logging
import secrets
import time
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from .ratelimit import client_ip

log = logging.getLogger(__name__)
AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
FREE_TTL = 24 * 3600


def _cfg(key):
    return settings.SKILLMESH.get(key)


def oauth_configured() -> bool:
    return bool(_cfg("GITHUB_CLIENT_ID") and _cfg("GITHUB_CLIENT_SECRET"))


def session_token(request) -> str | None:
    return request.session.get("gh_token")


# ---------- free-use accounting ----------

def _session_used(request) -> set[str]:
    """Free analyses recorded in this session during the last 24 h."""
    if time.time() - request.session.get("gh_free_at", 0) > FREE_TTL:
        return set()
    return set(request.session.get("gh_free_used", []))


def _used(request) -> list[str]:
    used = _session_used(request)
    used |= set(cache.get(f"gh-free:{client_ip(request)}", []))
    return sorted(used)


def free_remaining(request) -> int:
    return max(0, _cfg("GITHUB_FREE_ANALYSES") - len(_used(request)))


def can_analyze(request, username: str) -> bool:
    """Signed-in users: always. Anonymous: within the free allowance; re-checking the same profile is free."""
    if session_token(request):
        return True
    used = _used(request)
    return username.lower() in used or len(used) < _cfg("GITHUB_FREE_ANALYSES")


def record_free_use(request, username: str) -> None:
    if session_token(request):
        return
    name = username.lower()
    used = _session_used(request)
    if not used:
        request.session["gh_free_at"] = time.time()
    used.add(name)
    request.session["gh_free_used"] = sorted(used)[:20]
    key = f"gh-free:{client_ip(request)}"
    ip_used = set(cache.get(key, []))
    ip_used.add(name)
    cache.set(key, sorted(ip_used)[:20], FREE_TTL)


# ---------- views ----------

@require_GET
def status(request):
    return JsonResponse({
        "signed_in": bool(session_token(request)),
        "login": request.session.get("gh_login", ""),
        "oauth_configured": oauth_configured(),
        "free_total": _cfg("GITHUB_FREE_ANALYSES"),
        "free_remaining": free_remaining(request),
    })


def _redirect_uri(request) -> str:
    return _cfg("GITHUB_OAUTH_REDIRECT_URI") or request.build_absolute_uri(reverse("github-callback"))


def _safe_next(value: str | None) -> str:
    v = value or "/"
    return v if v.startswith("/") and not v.startswith("//") and "\\" not in v else "/"


@require_GET
def login(request):
    nxt = _safe_next(request.GET.get("next"))
    if not oauth_configured():
        return HttpResponseRedirect(f"{nxt}?gh=not_configured")
    state = secrets.token_urlsafe(24)
    request.session["gh_oauth_state"] = state
    request.session["gh_oauth_next"] = nxt
    params = {"client_id": _cfg("GITHUB_CLIENT_ID"), "redirect_uri": _redirect_uri(request),
              "state": state, "scope": "", "allow_signup": "true"}
    return HttpResponseRedirect(f"{AUTHORIZE_URL}?{urlencode(params)}")


@require_GET
def callback(request):
    nxt = _safe_next(request.session.pop("gh_oauth_next", "/"))
    expected = request.session.pop("gh_oauth_state", "")
    state, code = request.GET.get("state", ""), request.GET.get("code", "")
    if request.GET.get("error") == "access_denied":  # the user pressed "Cancel" on GitHub
        return HttpResponseRedirect(f"{nxt}?gh=cancelled")
    if not expected or not state or not hmac.compare_digest(expected, state) or not code:
        return HttpResponseRedirect(f"{nxt}?gh=failed")
    try:
        r = requests.post(TOKEN_URL, timeout=10, headers={"Accept": "application/json"}, data={
            "client_id": _cfg("GITHUB_CLIENT_ID"), "client_secret": _cfg("GITHUB_CLIENT_SECRET"),
            "code": code, "redirect_uri": _redirect_uri(request)})
        token = r.json().get("access_token") if r.status_code == 200 else None
        user = requests.get("https://api.github.com/user", timeout=10, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}).json() if token else {}
    except (requests.RequestException, ValueError):
        token, user = None, {}
    if not token or not user.get("login"):
        return HttpResponseRedirect(f"{nxt}?gh=failed")
    request.session.cycle_key()
    request.session["gh_token"] = token
    request.session["gh_login"] = user["login"]
    return HttpResponseRedirect(f"{nxt}?gh=signed_in")


@require_POST
def logout(request):
    token = request.session.pop("gh_token", None)
    request.session.pop("gh_login", None)
    if token and oauth_configured():
        try:  # revoke the grant so the token can't be reused
            requests.delete(f"https://api.github.com/applications/{_cfg('GITHUB_CLIENT_ID')}/grant", timeout=8,
                            auth=(_cfg("GITHUB_CLIENT_ID"), _cfg("GITHUB_CLIENT_SECRET")),
                            json={"access_token": token}, headers={"Accept": "application/vnd.github+json"})
        except requests.RequestException:
            log.warning("Could not revoke GitHub grant")
    return JsonResponse({"ok": True})
