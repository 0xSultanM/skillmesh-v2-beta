import json

from django.core.management.base import BaseCommand
from django.db import transaction

from taxonomy.models import Skill
from taxonomy.normalizer import SEED_PATH, invalidate_index


class Command(BaseCommand):
    help = "Load or update the skill taxonomy from taxonomy/data/skills.json (idempotent)."

    @transaction.atomic
    def handle(self, *args, **options):
        rows = json.loads(SEED_PATH.read_text(encoding="utf-8"))
        by_slug = {}
        for row in rows:
            skill, _ = Skill.objects.update_or_create(
                slug=row["slug"],
                defaults={
                    "name": row["name"],
                    "category": row.get("category", "other"),
                    "aliases": row.get("aliases", []),
                    "learn_hours": row.get("learn_hours", 20),
                },
            )
            by_slug[skill.slug] = skill

        missing = set()
        for row in rows:
            skill = by_slug[row["slug"]]
            prereqs = [by_slug[s] for s in row.get("prerequisites", []) if s in by_slug]
            related = [by_slug[s] for s in row.get("related", []) if s in by_slug]
            missing |= {s for s in row.get("prerequisites", []) + row.get("related", []) if s not in by_slug}
            skill.prerequisites.set(prereqs)
            skill.related.set(related)

        invalidate_index()
        if missing:
            self.stderr.write(self.style.WARNING(f"Unknown slugs referenced: {sorted(missing)}"))
        self.stdout.write(self.style.SUCCESS(f"Taxonomy loaded: {len(by_slug)} skills."))
