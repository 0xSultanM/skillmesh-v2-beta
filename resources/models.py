"""
Public learning-resource cache.

Only public, non-personal data lives here: vetted links to free tutorials,
playlists, docs and courses, plus channel trust lists used for ranking.
"""
from django.db import models

from taxonomy.models import Skill


class LearningResource(models.Model):
    KIND_CHOICES = [
        ("playlist", "YouTube playlist"),
        ("video", "Video"),
        ("article", "Article / blog"),
        ("docs", "Official docs"),
        ("course", "Free course / bootcamp"),
        ("interactive", "Interactive practice"),
    ]
    SOURCE_CHOICES = [("curated", "Curated"), ("youtube", "YouTube API"), ("web", "Web search")]
    LEVEL_CHOICES = [("beginner", "Beginner"), ("intermediate", "Intermediate"), ("advanced", "Advanced")]

    skill = models.ForeignKey(Skill, on_delete=models.CASCADE, related_name="resources")
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default="curated")
    title = models.CharField(max_length=300)
    url = models.URLField(max_length=1000)
    author = models.CharField(max_length=200, blank=True, help_text="Channel, publisher or site name.")
    description = models.TextField(blank=True)
    level = models.CharField(max_length=20, choices=LEVEL_CHOICES, default="beginner")
    est_hours = models.FloatField(null=True, blank=True)
    is_free = models.BooleanField(default=True)
    quality_score = models.FloatField(default=0.5, help_text="0..1, higher is better.")
    metadata = models.JSONField(default=dict, blank=True)

    is_active = models.BooleanField(default=True)
    last_checked = models.DateTimeField(null=True, blank=True)
    last_status = models.PositiveIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-quality_score"]
        constraints = [models.UniqueConstraint(fields=["skill", "url"], name="uniq_resource_per_skill")]
        indexes = [models.Index(fields=["skill", "is_active", "-quality_score"])]

    def __str__(self) -> str:
        return f"{self.title} ({self.get_kind_display()})"

    VIDEO_KINDS = ("playlist", "video")

    @property
    def group(self) -> str:
        return "video" if self.kind in self.VIDEO_KINDS else "content"

    def highlights(self) -> list[str]:
        """Short, factual reasons this resource was selected (shown in the details dropdown)."""
        m = self.metadata or {}
        out = []
        if self.kind in ("playlist", "video"):
            if self.kind == "video":
                out.append("Single full-length video")
            if m.get("item_count"):
                out.append(f"{m['item_count']} videos in a structured course")
            subs = m.get("subscribers")
            if subs:
                out.append(f"{subs / 1_000_000:.1f}M subscribers" if subs >= 1_000_000 else f"{subs // 1000}K subscribers")
            if m.get("trusted"):
                out.append("Trusted educator")
            if m.get("published_at"):
                out.append(f"Published {m['published_at'][:4]}")
        else:
            if self.kind == "docs":
                out.append("Official / authoritative documentation")
            if self.kind == "interactive":
                out.append("Hands-on, in-browser practice")
            if self.kind == "course":
                out.append("Structured free course")
            if m.get("domain"):
                out.append(m["domain"])
        if self.source == "curated":
            out.append("Hand-vetted by SkillMesh")
        out.append("Free")
        return out

    def as_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "kind": self.kind,
            "kind_label": self.get_kind_display(),
            "group": self.group,
            "author": self.author,
            "description": self.description,
            "highlights": self.highlights(),
            "level": self.level,
            "est_hours": self.est_hours,
            "quality": round(self.quality_score, 2),
            "source": self.source,
            "embed": self.embed(),
        }

    def embed(self) -> dict | None:
        """In-app player target for YouTube resources: {"kind": "video"|"playlist", "id": ...}."""
        if self.kind not in self.VIDEO_KINDS:
            return None
        from .youtube_keyless import parse_youtube

        yt = parse_youtube(self.url)
        return {"kind": yt["kind"], "id": yt["id"]} if yt else None


class Channel(models.Model):
    """YouTube channel trust list. Matched by channel id, or by exact title as a fallback."""

    TRUST_CHOICES = [("allow", "Trusted educator"), ("block", "Blocked (low quality / repetitive)")]

    channel_id = models.CharField(max_length=64, blank=True, db_index=True)
    title = models.CharField(max_length=200)
    trust = models.CharField(max_length=10, choices=TRUST_CHOICES, default="allow")
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["trust", "title"]

    def __str__(self) -> str:
        return f"{self.title} [{self.trust}]"


class SkillSyllabus(models.Model):
    """Essential topics for a skill, merged from public course syllabi (see resources/topics.py)."""

    skill = models.OneToOneField(Skill, on_delete=models.CASCADE, related_name="syllabus")
    topics = models.JSONField(default=list, blank=True)
    outcomes = models.JSONField(default=list, blank=True)
    courses = models.JSONField(default=list, blank=True)
    source = models.CharField(max_length=120, blank=True)
    fetched_at = models.DateTimeField()

    class Meta:
        verbose_name_plural = "skill syllabi"

    def __str__(self) -> str:
        return f"{self.skill.name}: {len(self.topics)} topics"

    def as_dict(self) -> dict:
        return {"topics": self.topics, "outcomes": self.outcomes, "courses": self.courses, "source": self.source}
