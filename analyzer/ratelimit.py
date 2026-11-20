"""Tiny per-IP fixed-window rate limiter backed by Django's cache."""
from __future__ import annotations

import time
from functools import wraps

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse


def client_ip(request) -> str:
    # Only trust X-Forwarded-For when explicitly deployed behind a proxy.
    if getattr(settings, "TRUST_X_FORWARDED_FOR", False):
        fwd = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def rate_limit(scope: str, per_minute: int | None = None):
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            limit = per_minute or settings.SKILLMESH.get("RATE_LIMIT_PER_MIN", 20)
            window = int(time.time() // 60)
            key = f"rl:{scope}:{client_ip(request)}:{window}"
            count = cache.get_or_set(key, 0, timeout=70)
            if count >= limit:
                return JsonResponse(
                    {"error": {"code": "rate_limited", "message": "Too many requests. Please wait a minute."}},
                    status=429,
                )
            try:
                cache.incr(key)
            except ValueError:
                cache.set(key, 1, timeout=70)
            return view(request, *args, **kwargs)

        return wrapped

    return decorator
