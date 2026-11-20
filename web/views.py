from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie


@ensure_csrf_cookie
def index(request):
    return render(request, "web/index.html")


@ensure_csrf_cookie
def result(request):
    """Analysis result page - shows fit summary only."""
    return render(request, "web/result.html")


@ensure_csrf_cookie
def tutmesh(request):
    """Browser-only roadmap tracker. Roadmaps and progress live in localStorage."""
    return render(request, "web/tutmesh.html")


@ensure_csrf_cookie
def history(request):
    """Browser-only history of saved analyses."""
    return render(request, "web/history.html")


def privacy(request):
    return render(request, "web/privacy.html")
