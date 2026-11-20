from django.urls import path

from . import github_auth, views

urlpatterns = [
    path("health/", views.health, name="api-health"),
    path("resume/parse/", views.parse_resume, name="api-resume-parse"),
    path("job/scrape/", views.scrape_job, name="api-job-scrape"),
    path("job/parse/", views.parse_job, name="api-job-parse"),
    path("github/analyze/", views.analyze_github, name="api-github-analyze"),
    path("github/status/", github_auth.status, name="api-github-status"),
    path("reader/", views.reader, name="api-reader"),
    path("analyze/", views.analyze, name="api-analyze"),
    path("roadmap/pdf/", views.roadmap_pdf, name="api-roadmap-pdf"),
]
