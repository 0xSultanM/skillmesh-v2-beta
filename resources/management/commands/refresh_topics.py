import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from resources.models import SkillSyllabus
from resources.service import _terms_for
from resources.topics import discover_topics
from taxonomy.models import Skill


class Command(BaseCommand):
    help = "Mine essential topics for skills from public Coursera course syllabi and cache them (polite pacing)."

    def add_arguments(self, parser):
        parser.add_argument("slugs", nargs="*")
        parser.add_argument("--missing-only", action="store_true")
        parser.add_argument("--empty-only", action="store_true", help="Retry skills whose cached topic list is empty.")
        parser.add_argument("--pause", type=float, default=6.0)

    def handle(self, *args, slugs, missing_only, pause, **options):
        qs = Skill.objects.all()
        if slugs:
            qs = qs.filter(slug__in=slugs)
        if missing_only:
            qs = qs.exclude(syllabus__isnull=False)
        if options.get("empty_only"):
            qs = qs.filter(syllabus__topics=[])
        for i, skill in enumerate(qs):
            if i:
                time.sleep(pause)
            name, terms = _terms_for(skill.slug)
            try:
                data = discover_topics(name, terms, skill.slug)
            except Exception as exc:  # keep going
                self.stderr.write(f"{skill.slug}: error {exc}")
                continue
            SkillSyllabus.objects.update_or_create(skill=skill, defaults={
                "topics": data.get("topics", []), "outcomes": data.get("outcomes", []),
                "courses": data.get("courses", []), "source": data.get("source", ""), "fetched_at": timezone.now()})
            self.stdout.write(f"{skill.slug}: {len(data.get('topics', []))} topics from {len(data.get('courses', []))} courses")
