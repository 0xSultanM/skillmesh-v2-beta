from django.core.management.base import BaseCommand
from django.utils import timezone

from analyzer.net import FetchError, safe_get
from resources.models import LearningResource
from resources.quality import looks_paywalled


class Command(BaseCommand):
    help = "Health-check cached resource links; deactivate dead or paywalled ones. Run weekly."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Include inactive resources (to revive them).")

    def handle(self, *args, **options):
        qs = LearningResource.objects.all() if options["all"] else LearningResource.objects.filter(is_active=True)
        ok = dead = 0
        for res in qs.iterator():
            status, active = None, True
            try:
                page = safe_get(res.url, max_bytes=2_000_000, timeout=10)
                status = page.status
                if status >= 400 or looks_paywalled(page.text):
                    active = False
            except FetchError as exc:
                active = False
                self.stderr.write(f"  {res.url}: {exc.code}")
            res.last_status = status
            res.last_checked = timezone.now()
            res.is_active = active
            res.save(update_fields=["last_status", "last_checked", "is_active", "updated_at"])
            ok += active
            dead += not active
            self.stdout.write(f"{'OK  ' if active else 'DEAD'} {status or '-'} {res.url}")
        self.stdout.write(self.style.SUCCESS(f"Checked {ok + dead}: {ok} ok, {dead} deactivated."))
