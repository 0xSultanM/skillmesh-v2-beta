from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("result/", views.result, name="result"),
    path("tutmesh/", views.tutmesh, name="tutmesh"),
    path("history/", views.history, name="history"),
    path("privacy/", views.privacy, name="privacy"),
]
