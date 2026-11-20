from django.contrib import admin

from .models import Channel, LearningResource, SkillSyllabus


@admin.register(LearningResource)
class LearningResourceAdmin(admin.ModelAdmin):
    list_display = ("title", "skill", "kind", "source", "quality_score", "is_active", "last_status", "last_checked")
    list_filter = ("kind", "source", "is_active", "level", "skill__category")
    search_fields = ("title", "url", "author", "skill__name")
    autocomplete_fields = ("skill",)
    list_editable = ("is_active",)


@admin.register(Channel)
class ChannelAdmin(admin.ModelAdmin):
    list_display = ("title", "channel_id", "trust", "note")
    list_filter = ("trust",)
    search_fields = ("title", "channel_id")


@admin.register(SkillSyllabus)
class SkillSyllabusAdmin(admin.ModelAdmin):
    list_display = ("skill", "source", "fetched_at")
    search_fields = ("skill__name",)
