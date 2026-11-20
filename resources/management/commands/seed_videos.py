import json
import time
from pathlib import Path

from django.core.management.base import BaseCommand

from resources.models import Channel
from resources.quality import score_youtube_keyless
from resources.service import _terms_for, store_candidates
from resources.youtube_keyless import oembed, parse_youtube
from taxonomy.models import Skill

CANDIDATES = Path(__file__).resolve().parents[2] / "data" / "video_candidates.json"


class Command(BaseCommand):
    help = ("Verify candidate course videos with YouTube oEmbed (keyless) and store the ones that are public, "
            "embeddable, from a trusted channel and on-topic. Safe to re-run.")

    def add_arguments(self, parser):
        parser.add_argument("slugs", nargs="*", help="Only these skills.")
        parser.add_argument("--any-channel", action="store_true",
                            help="Also accept untrusted channels when the title is on-topic.")

    def handle(self, *args, slugs, any_channel, **options):
        data = json.loads(CANDIDATES.read_text(encoding="utf-8"))
        trust = {c.title.strip().lower(): c.trust for c in Channel.objects.all()}
        kept = dropped = 0
        for slug, urls in data.items():
            if slug.startswith("_") or (slugs and slug not in slugs):
                continue
            skill = Skill.objects.filter(slug=slug).first()
            if not skill:
                continue
            name, terms = _terms_for(slug)
            good = []
            for url in urls:
                yt = parse_youtube(url)
                meta = oembed(yt["url"]) if yt else None
                time.sleep(0.2)
                if not meta:
                    dropped += 1
                    self.stdout.write(f"  drop {slug}: {url} (not found / not embeddable)")
                    continue
                title, channel = meta.get("title", ""), meta.get("author_name", "")
                t = trust.get(channel.strip().lower())
                score = score_youtube_keyless(title, yt["kind"], t, terms)
                on_topic = any(term.lower() in title.lower() for term in terms if term)
                if not on_topic or t == "block" or (t != "allow" and not any_channel) or score < 0.5:
                    dropped += 1
                    self.stdout.write(f"  drop {slug}: {channel} | {title[:70]}")
                    continue
                good.append({
                    "kind": yt["kind"], "source": "curated", "title": title[:300], "url": yt["url"],
                    "author": channel[:200], "quality_score": score,
                    "description": (f"Full-length {'playlist' if yt['kind'] == 'playlist' else 'video course'} by {channel}"
                                    f"{' (trusted educator)' if t == 'allow' else ''}. Verified public and embeddable: play it "
                                    "inside SkillMesh and your watch time counts toward your daily streak."),
                    "metadata": {f"{yt['kind']}_id": yt["id"], "channel_url": meta.get("author_url", ""),
                                 "thumbnail_url": meta.get("thumbnail_url", ""), "trusted": t == "allow",
                                 "discovered_via": "curated+oembed"},
                })
                self.stdout.write(f"  keep {slug}: {channel} | {title[:70]}")
            kept += len(good)
            store_candidates(skill, good)
        self.stdout.write(self.style.SUCCESS(f"Verified videos: kept {kept}, dropped {dropped}."))
