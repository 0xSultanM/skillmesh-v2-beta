import json
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from resources.models import Channel
from resources.service import store_candidates
from taxonomy.models import Skill

CURATED = Path(__file__).resolve().parents[2] / "data" / "curated.json"


class Command(BaseCommand):
    help = "Load hand-curated free resources and the YouTube channel trust list (idempotent)."

    @transaction.atomic
    def handle(self, *args, **options):
        data = json.loads(CURATED.read_text(encoding="utf-8"))

        for ch in data.get("channels", []):
            Channel.objects.update_or_create(
                title=ch["title"],
                defaults={"trust": ch.get("trust", "allow"), "note": ch.get("note", ""),
                          "channel_id": ch.get("channel_id", "")},
            )

        total, unknown = 0, []
        for slug, items in data.get("resources", {}).items():
            skill = Skill.objects.filter(slug=slug).first()
            if not skill:
                unknown.append(slug)
                continue
            for item in items:
                item.setdefault("source", "curated")
            store_candidates(skill, items)
            total += len(items)

        if unknown:
            self.stderr.write(self.style.WARNING(f"Skipped unknown skills (run seed_taxonomy first?): {unknown}"))
        self.stdout.write(self.style.SUCCESS(
            f"Seeded {total} curated resources and {len(data.get('channels', []))} channels."))
