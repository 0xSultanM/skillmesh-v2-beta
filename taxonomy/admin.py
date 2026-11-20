from django.contrib import admin

from .models import Skill


@admin.register(Skill)
class SkillAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "category", "learn_hours")
    list_filter = ("category",)
    search_fields = ("name", "slug", "aliases")
    filter_horizontal = ("prerequisites", "related")
    prepopulated_fields = {"slug": ("name",)}
