from django.test import SimpleTestCase

from resources import quality


class QualityTests(SimpleTestCase):
    base = {
        "title": "Django Full Course for Beginners",
        "item_count": 24,
        "subscriber_count": 1_200_000,
        "channel_video_count": 400,
        "channel_view_count": 90_000_000,
        "published_at": "2025-06-01T00:00:00Z",
        "item_titles": [f"Django tutorial part {i}: {t}" for i, t in enumerate(
            ["setup", "models", "views", "templates", "forms", "auth", "admin", "deploy"])],
        "avg_like_ratio": 0.035,
    }

    def test_good_playlist_scores_well(self):
        self.assertGreater(quality.score_youtube_playlist(self.base, ["Django"]), 0.7)

    def test_repetitive_playlist_penalised(self):
        repetitive = {**self.base, "item_titles": ["Django tutorial in hindi part %d" % i for i in range(20)]}
        self.assertLess(quality.score_youtube_playlist(repetitive, ["Django"]),
                        quality.score_youtube_playlist(self.base, ["Django"]) * 0.7)

    def test_content_farm_penalised(self):
        farm = {**self.base, "subscriber_count": 3000, "channel_video_count": 9000, "channel_view_count": 400_000,
                "avg_like_ratio": 0.004}
        self.assertLess(quality.score_youtube_playlist(farm, ["Django"]), 0.5)

    def test_blocked_and_clickbait_rejected(self):
        self.assertEqual(quality.score_youtube_playlist(self.base, ["Django"], trust="block"), 0.0)
        clickbait = {**self.base, "title": "Learn Django in 10 minutes!!! #shorts"}
        self.assertEqual(quality.score_youtube_playlist(clickbait, ["Django"]), 0.0)

    def test_too_short_is_not_a_course(self):
        self.assertEqual(quality.score_youtube_playlist({**self.base, "item_count": 2}, ["Django"]), 0.0)

    def test_trusted_channel_floor(self):
        weak = {**self.base, "subscriber_count": 5000, "avg_like_ratio": None}
        self.assertGreater(quality.score_youtube_playlist(weak, ["Django"], trust="allow"),
                           quality.score_youtube_playlist(weak, ["Django"]))

    def test_article_domain_trust(self):
        self.assertGreater(quality.score_article("https://developer.mozilla.org/x", "Learn CSS grid", ["CSS"]),
                           quality.score_article("https://someblog.xyz/x", "Learn CSS grid", ["CSS"]))
        self.assertEqual(quality.score_article("https://w3schools.in/x", "CSS", ["CSS"]), 0.0)

    def test_paywall_detection(self):
        self.assertTrue(quality.looks_paywalled("<div>Member-only story</div>"))
        self.assertFalse(quality.looks_paywalled("<p>Free tutorial</p>"))
