import time

from django.core.management.base import BaseCommand

from resources.service import discover_for_skill
from taxonomy.models import Skill


class Command(BaseCommand):
    help = (
        "Discover and cache YouTube playlists / articles for skills. Intended for a weekly cron. "
        "Each skill costs ~120 YouTube quota units (default quota 10k/day), so use --limit."
    )

    def add_arguments(self, parser):
        parser.add_argument("slugs", nargs="*", help="Skill slugs (default: all).")
        parser.add_argument("--limit", type=int, default=50, help="Max skills to process this run.")
        parser.add_argument("--category", help="Only skills in this category.")
        parser.add_argument("--pause", type=float, default=8.0,
                            help="Seconds between skills (keyless search throttles bursts).")
        parser.add_argument("--missing-only", action="store_true", help="Skip skills that already have a video.")

    def handle(self, *args, slugs, limit, category, pause, missing_only, **options):
        qs = Skill.objects.all()
        if slugs:
            qs = qs.filter(slug__in=slugs)
        if category:
            qs = qs.filter(category=category)
        if missing_only:
            qs = qs.exclude(resources__kind__in=["playlist", "video"], resources__is_active=True)
        for i, skill in enumerate(qs.distinct()[:limit]):
            if i:
                time.sleep(pause)
            report = discover_for_skill(skill.slug)
            self.stdout.write(f"{skill.slug}: {report}")
