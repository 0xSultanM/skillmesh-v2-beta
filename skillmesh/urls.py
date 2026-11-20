from django.contrib import admin
from django.urls import include, path

from analyzer import github_auth

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("analyzer.urls")),
    path("auth/github/login/", github_auth.login, name="github-login"),
    path("auth/github/callback/", github_auth.callback, name="github-callback"),
    path("auth/github/logout/", github_auth.logout, name="github-logout"),
    path("", include("web.urls")),
]
