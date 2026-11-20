from django.db import models


class Skill(models.Model):
    """A canonical, public skill in the SkillMesh taxonomy."""

    CATEGORY_CHOICES = [
        ("cs", "CS Fundamentals"),
        ("language", "Language"),
        ("frontend", "Frontend"),
        ("backend", "Backend"),
        ("database", "Database"),
        ("devops", "DevOps"),
        ("cloud", "Cloud"),
        ("data", "Data"),
        ("ml", "AI / ML"),
        ("mobile", "Mobile"),
        ("testing", "Testing"),
        ("security", "Security"),
        ("design", "Design"),
        ("process", "Process"),
        ("tools", "Tools"),
        ("soft", "Soft skill"),
        ("other", "Other"),
    ]

    slug = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=120)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default="other")
    aliases = models.JSONField(default=list, blank=True, help_text="Lower-case phrases that map to this skill.")
    learn_hours = models.PositiveIntegerField(default=20, help_text="Rough hours to reach job-ready basics.")
    prerequisites = models.ManyToManyField("self", symmetrical=False, related_name="unlocks", blank=True)
    related = models.ManyToManyField("self", symmetrical=True, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name
