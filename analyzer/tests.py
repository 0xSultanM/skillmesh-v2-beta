import base64
import io
import json
from unittest import mock

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, SimpleTestCase, TestCase, override_settings

from analyzer import github, heuristics, jobpage, net, roadmap, scoring
from analyzer.extraction import normalize_job, normalize_profile
from analyzer.privacy import redact_pii
from taxonomy.normalizer import get_index, invalidate_index

JOB_TEXT = """Senior Backend Engineer
Acme Payments

About the role
You will build APIs for our fintech platform.

Requirements:
- 5+ years of professional experience with Python
- Strong experience with Django and PostgreSQL
- Docker and CI/CD pipelines
- Excellent communication skills

Nice to have:
- Kubernetes
- AWS
- Must be authorized to work in the EU

Benefits:
- Free React lunch club
"""

RESUME_TEXT = """Jane Doe
Backend Developer | jane@example.com | +1 555 123 4567

Experience
Software Engineer, Widgets Inc  Jan 2019 - Present
- Built REST APIs in Python and Django serving 2M requests/day
- Migrated MySQL databases and wrote complex SQL reports

Skills
Python, Django, MySQL, Git, Docker, Linux

Education
BSc Computer Science 2014 - 2018
"""


class NormalizerTests(SimpleTestCase):
    def setUp(self):
        invalidate_index()
        self.idx = get_index()

    def test_aliases_map_to_canonical(self):
        self.assertEqual(self.idx.normalize("ReactJS")[0], "react")
        self.assertEqual(self.idx.normalize("Postgres")[0], "postgresql")
        self.assertEqual(self.idx.normalize("C++")[0], "cpp")
        self.assertEqual(self.idx.normalize("Go")[0], "go")
        self.assertEqual(self.idx.normalize("k8s")[0], "kubernetes")

    def test_unknown_skill_is_custom(self):
        slug, name, known = self.idx.normalize("Underwater Basket Weaving")
        self.assertTrue(slug.startswith("custom-"))
        self.assertFalse(known)

    def test_find_mentions_longest_match_and_boundaries(self):
        slugs = [m.slug for m in self.idx.find_mentions("Experience with React Native and Java, not JavaScript.")]
        self.assertIn("react-native", slugs)
        self.assertNotIn("react", slugs)
        self.assertIn("java", slugs)
        self.assertIn("javascript", slugs)

    def test_no_false_positives_on_common_words(self):
        slugs = {m.slug for m in self.idx.find_mentions("Let's go and rest in spring; express your solid ideas.")}
        self.assertEqual(slugs, set())


class HeuristicTests(SimpleTestCase):
    def setUp(self):
        invalidate_index()

    def test_job_extraction_sections(self):
        job = normalize_job(heuristics.extract_job(JOB_TEXT), "heuristic")
        req = {s["slug"] for s in job["required"]}
        pref = {s["slug"] for s in job["preferred"]}
        self.assertTrue({"python", "django", "postgresql", "docker", "ci-cd", "communication"} <= req)
        self.assertTrue({"kubernetes", "aws"} <= pref)
        self.assertNotIn("react", req | pref)  # benefits section ignored
        self.assertEqual(job["years_required"], 5)
        self.assertEqual(job["seniority"], "senior")
        self.assertTrue(any("authorized to work" in h for h in job["hard_requirements"]))

    def test_profile_extraction_strength_and_years(self):
        prof = normalize_profile(heuristics.extract_profile(RESUME_TEXT), "heuristic")
        by = {s["slug"]: s for s in prof["skills"]}
        self.assertEqual(by["django"]["strength"], "applied")
        self.assertEqual(by["git"]["strength"], "listed")
        self.assertGreaterEqual(prof["years_experience"], 6)
        self.assertEqual(prof["education"], "bachelor")


class ScoringTests(SimpleTestCase):
    def setUp(self):
        invalidate_index()
        self.job = normalize_job(heuristics.extract_job(JOB_TEXT), "heuristic")
        self.profile = normalize_profile(heuristics.extract_profile(RESUME_TEXT), "heuristic")

    def test_related_skill_gives_partial_credit(self):
        result = scoring.score(self.job, self.profile)
        pg = next(r for r in result["required"] if r["slug"] == "postgresql")
        self.assertEqual(pg["status"], "partial")
        self.assertEqual(pg["via"], "MySQL")

    def test_score_range_and_breakdown(self):
        result = scoring.score(self.job, self.profile)
        self.assertTrue(0 <= result["score"] <= 100)
        self.assertEqual(len(result["breakdown"]), 5)
        self.assertAlmostEqual(sum(b["points"] for b in result["breakdown"]), result["raw_score"], delta=0.6)

    def test_empty_profile_is_capped_low(self):
        result = scoring.score(self.job, {"skills": [], "years_experience": 0})
        self.assertLessEqual(result["score"], 55)
        self.assertEqual(result["verdict"]["key"], "not_yet")

    def test_perfect_profile_scores_high(self):
        skills = [{**s, "strength": "applied", "evidence": "x"} for s in self.job["required"] + self.job["preferred"]]
        prof = {"skills": skills, "years_experience": 8, "seniority": "senior", "education": "bachelor", "domains": []}
        result = scoring.score(self.job, prof)
        self.assertGreaterEqual(result["score"], 90)
        self.assertEqual(result["verdict"]["key"], "strong")

    def test_implied_prerequisite(self):
        job = {"required": [{"slug": "javascript", "name": "JavaScript", "known": True}], "preferred": []}
        prof = {"skills": [{"slug": "react", "name": "React", "strength": "applied", "evidence": ""}]}
        row = scoring.score(job, prof)["required"][0]
        self.assertEqual(row["status"], "matched")
        self.assertEqual(row["relation"], "prerequisite")


class RoadmapTests(SimpleTestCase):
    def setUp(self):
        invalidate_index()

    def test_prerequisites_come_first_and_are_added(self):
        job = {"required": [{"slug": "nextjs", "name": "Next.js", "known": True}], "preferred": []}
        prof = {"skills": [{"slug": "html", "name": "HTML", "strength": "applied"}]}
        rep = scoring.score(job, prof)
        plan = roadmap.build_roadmap(rep, prof, hours_per_week=10)
        order = [s["slug"] for ph in plan["phases"] for s in ph["steps"]]
        steps = roadmap.build_steps(rep, prof)
        topo = [s["slug"] for s in steps]
        self.assertIn("javascript", topo)
        self.assertIn("react", topo)
        self.assertNotIn("html", topo)  # already held
        self.assertLess(topo.index("css"), topo.index("javascript"))
        self.assertLess(topo.index("javascript"), topo.index("react"))
        self.assertLess(topo.index("react"), topo.index("nextjs"))
        self.assertEqual(set(order), set(topo))
        self.assertGreater(plan["total_weeks"], 0)

    def test_no_gaps_no_steps(self):
        job = {"required": [{"slug": "python", "name": "Python", "known": True}], "preferred": []}
        prof = {"skills": [{"slug": "python", "name": "Python", "strength": "applied"}]}
        plan = roadmap.build_roadmap(scoring.score(job, prof), prof)
        self.assertEqual(plan["step_count"], 0)


class PrivacyTests(SimpleTestCase):
    def test_redacts_contact_details(self):
        out = redact_pii("Mail jane@example.com, call +1 (555) 123-4567, linkedin.com/in/jane. Worked 2019 - 2023.")
        self.assertNotIn("jane@example.com", out)
        self.assertNotIn("555", out)
        self.assertNotIn("linkedin.com/in/jane", out)
        self.assertIn("2019 - 2023", out)


class NetSafetyTests(SimpleTestCase):
    def test_blocks_private_and_odd_urls(self):
        for url in ["http://127.0.0.1/", "http://localhost/admin", "http://169.254.169.254/latest/meta-data",
                    "http://10.0.0.5/", "ftp://example.com/", "http://user:pw@example.com/", "http://example.com:8080/"]:
            with self.subTest(url=url):
                with self.assertRaises(net.FetchError):
                    net.validate_url(url)

    def test_public_ip_check(self):
        self.assertTrue(net.is_public_ip("8.8.8.8"))
        self.assertFalse(net.is_public_ip("192.168.1.1"))
        self.assertFalse(net.is_public_ip("::1"))
        self.assertFalse(net.is_public_ip("::ffff:127.0.0.1"))


class JobPageTests(SimpleTestCase):
    def test_jsonld_jobposting(self):
        html = """<html><head><script type="application/ld+json">
        {"@context":"https://schema.org","@graph":[{"@type":"Organization","name":"X"},
         {"@type":"JobPosting","title":"Data Analyst","hiringOrganization":{"@type":"Organization","name":"DataCo"},
          "jobLocation":{"@type":"Place","address":{"addressLocality":"Berlin","addressCountry":"DE"}},
          "description":"<p>We need <b>SQL</b>, Tableau and Python.</p><ul><li>3+ years experience</li></ul>"}]}
        </script></head><body>Login</body></html>"""
        job = jobpage.extract_job_page("https://jobs.example.com/1", html)
        self.assertEqual(job.method, "json-ld")
        self.assertEqual(job.title, "Data Analyst")
        self.assertEqual(job.company, "DataCo")
        self.assertIn("Berlin", job.location)
        self.assertIn("Tableau", job.text)

    def test_login_wall_detected(self):
        html = "<html><body><form><input type='password'></form>Sign in to view this job</body></html>"
        with self.assertRaises(jobpage.LoginRequired):
            jobpage.extract_job_page("https://www.linkedin.com/authwall?x=1", html)

    def test_main_content_fallback(self):
        body = "<p>" + ("We are hiring a frontend developer with React and TypeScript. " * 10) + "</p>"
        html = f"<html><head><title>Frontend Dev</title></head><body><nav>menu</nav><main><h1>Frontend Dev</h1>{body}</main></body></html>"
        job = jobpage.extract_job_page("https://example.com/job", html)
        self.assertEqual(job.method, "main-content")
        self.assertIn("TypeScript", job.text)
        self.assertNotIn("menu", job.text)


@override_settings(SKILLMESH={**settings.SKILLMESH, "LLM_ENABLED": False, "LIVE_DISCOVERY": False})
class ApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_taxonomy", stdout=io.StringIO())
        call_command("seed_resources", stdout=io.StringIO())

    def setUp(self):
        invalidate_index()
        self.c = Client()

    def post(self, url, payload):
        return self.c.post(url, data=json.dumps(payload), content_type="application/json")

    def test_full_flow_without_llm(self):
        r = self.c.post("/api/resume/parse/", {"file": SimpleUploadedFile("cv.txt", RESUME_TEXT.encode())})
        self.assertEqual(r.status_code, 200, r.content)
        profile = r.json()["profile"]
        self.assertEqual(profile["source"], "heuristic")

        r = self.post("/api/job/parse/", {"text": JOB_TEXT})
        self.assertEqual(r.status_code, 200, r.content)
        job = r.json()["job"]

        r = self.post("/api/analyze/", {"job": job, "profile": profile, "hours_per_week": 8})
        self.assertEqual(r.status_code, 200, r.content)
        data = r.json()
        self.assertIn("score", data["report"])
        steps = [s for ph in data["roadmap"]["phases"] for s in ph["steps"]]
        self.assertTrue(steps)
        k8s = next(s for s in steps if s["slug"] == "kubernetes")
        self.assertTrue(any("kubernetes.io" in res["url"] for res in k8s["resources"]["content"]))
        self.assertEqual(k8s["resources"]["video"][0]["kind"], "search")  # no vetted playlist cached offline
        self.assertIn(k8s["difficulty"], ("easy", "medium", "hard"))
        self.assertTrue(k8s["resources"]["content"][0]["description"])
        row = data["report"]["required"][0]
        self.assertIn("your_level", row)
        self.assertIn("required_level", row)
        self.assertEqual(r["Cache-Control"], "no-store, max-age=0")

    def test_custom_skill_roundtrip(self):
        job = {"required": [{"slug": "", "name": "Quantum Basket Weaving"}], "preferred": []}
        r = self.post("/api/analyze/", {"job": job, "profile": {"skills": []}})
        self.assertEqual(r.status_code, 200)
        step = r.json()["roadmap"]["phases"][0]["steps"][0]
        self.assertTrue(step["slug"].startswith("custom-"))
        self.assertEqual(step["resources"]["video"][0]["kind"], "search")
        self.assertEqual(step["resources"]["content"][0]["kind"], "search")

    def test_rejects_bad_upload(self):
        r = self.c.post("/api/resume/parse/", {"file": SimpleUploadedFile("cv.exe", b"MZ" + b"\0" * 200)})
        self.assertEqual(r.status_code, 400)

    def test_scrape_blocks_ssrf(self):
        r = self.post("/api/job/scrape/", {"url": "http://127.0.0.1:8000/admin/"})
        self.assertEqual(r.status_code, 422)

    def test_scrape_uses_safe_fetch(self):
        html = '<script type="application/ld+json">{"@type":"JobPosting","title":"QA Engineer","description":"' + "Cypress and Jest testing. " * 10 + '"}</script>'
        fake = net.FetchResult(url="https://x.com/j", final_url="https://x.com/j", status=200, content_type="text/html", text=html)
        with mock.patch("analyzer.views.safe_get", return_value=fake):
            r = self.post("/api/job/scrape/", {"url": "x.com/j"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("QA Engineer", r.json()["text"])

    def test_llm_failure_falls_back(self):
        from analyzer import llm
        with mock.patch.object(llm, "extract_job", side_effect=llm.LLMUnavailable("x")):
            r = self.post("/api/job/parse/", {"text": JOB_TEXT})
        self.assertEqual(r.json()["job"]["source"], "heuristic")

    def test_index_page_renders(self):
        r = self.c.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Analyze my fit")


class LevelTests(SimpleTestCase):
    def setUp(self):
        invalidate_index()

    def _job(self, seniority="mid"):
        return {"seniority": seniority, "required": [{"slug": "django", "name": "Django", "known": True}], "preferred": []}

    def test_required_level_tracks_seniority(self):
        prof = {"skills": []}
        junior = scoring.score(self._job("junior"), prof)["required"][0]["required_level"]
        senior = scoring.score(self._job("senior"), prof)["required"][0]["required_level"]
        self.assertLess(junior, senior)

    def test_applied_beats_listed_and_github_corroborates(self):
        listed = {"skills": [{"slug": "django", "name": "Django", "strength": "listed", "sources": ["resume"]}]}
        applied = {"skills": [{"slug": "django", "name": "Django", "strength": "applied", "sources": ["resume"]}], "years_experience": 2}
        both = {"skills": [{"slug": "django", "name": "Django", "strength": "applied", "sources": ["resume", "github"],
                            "github_level": 70}], "years_experience": 2}
        lv = lambda p: scoring.score(self._job(), p)["required"][0]["your_level"]
        self.assertLess(lv(listed), lv(applied))
        self.assertLess(lv(applied), lv(both))

    def test_cross_check_buckets(self):
        job = {"seniority": "mid", "preferred": [], "required": [
            {"slug": "django", "name": "Django", "known": True},
            {"slug": "docker", "name": "Docker", "known": True},
            {"slug": "react", "name": "React", "known": True}]}
        prof = {"skills": [
            {"slug": "django", "name": "Django", "strength": "applied", "sources": ["resume", "github"], "github_level": 60},
            {"slug": "docker", "name": "Docker", "strength": "applied", "sources": ["github"], "github_level": 55},
            {"slug": "react", "name": "React", "strength": "listed", "sources": ["resume"]}]}
        rep = scoring.score(job, prof)
        self.assertEqual(rep["cross_check"], {"confirmed": ["Django"], "resume_only": ["React"], "github_only": ["Docker"]})
        self.assertTrue(any("GitHub shows Docker" in w for w in rep["quick_wins"]))

    def test_difficulty(self):
        self.assertEqual(roadmap.difficulty({"hours": 8, "category": "frontend"}), "easy")
        self.assertEqual(roadmap.difficulty({"hours": 80, "category": "ml"}), "hard")
        self.assertEqual(roadmap.difficulty({"hours": 25, "category": "backend"}), "medium")
        self.assertEqual(roadmap.difficulty({"hours": 10, "category": "backend", "partial": True}), "easy")


class GitHubParsingTests(SimpleTestCase):
    def setUp(self):
        invalidate_index()

    def test_username_parsing(self):
        self.assertEqual(github.parse_username("https://github.com/octocat/"), "octocat")
        self.assertEqual(github.parse_username("@octo-cat"), "octo-cat")
        for bad in ["", "https://evil.com/octocat", "a" * 40, "-bad", "octo/cat"]:
            with self.subTest(bad=bad), self.assertRaises(github.GitHubError):
                github.parse_username(bad)

    def test_manifests_and_paths(self):
        pkg = '{"dependencies": {"react": "^18", "next": "14"}, "devDependencies": {"jest": "29", "tailwindcss": "3"}}'
        self.assertEqual(github.skills_from_deps(github.deps_from_manifest("web/package.json", pkg)),
                         {"react", "nextjs", "testing", "tailwind"})
        req = "Django==5.0\npsycopg2-binary>=2.9  # db\npytest\n-r base.txt\n"
        self.assertEqual(github.skills_from_deps(github.deps_from_manifest("requirements.txt", req)),
                         {"django", "postgresql", "testing"})
        paths = ["Dockerfile", ".github/workflows/ci.yml", "infra/main.tf", "src/app.test.ts", "k8s/deploy.yaml"]
        self.assertEqual(github.skills_from_paths(paths), {"docker", "ci-cd", "terraform", "testing", "kubernetes"})

    def test_skill_level_monotonic(self):
        self.assertLess(github.skill_level(1, 1000, 3, 0), github.skill_level(4, 600_000, 3, 20))
        self.assertLess(github.skill_level(2, 10_000, 30, 0), github.skill_level(2, 10_000, 3, 0))

    def test_analyze_user_with_mocked_api(self):
        from django.core.cache import cache
        cache.clear()

        def fake_api(path, params=None, token=None):
            if path == "/users/dev1":
                return {"login": "dev1", "type": "User", "public_repos": 3, "html_url": "https://github.com/dev1"}
            if path == "/users/dev1/repos":
                return [
                    {"name": "shop-api", "fork": False, "size": 500, "default_branch": "main", "stargazers_count": 12,
                     "pushed_at": "2026-10-01T00:00:00Z", "html_url": "https://github.com/dev1/shop-api"},
                    {"name": "forked", "fork": True, "size": 900, "default_branch": "main", "stargazers_count": 0,
                     "pushed_at": "2026-10-01T00:00:00Z", "html_url": "x"},
                ]
            if path.endswith("/languages"):
                return {"Python": 120_000, "HTML": 3000}
            if "/git/trees/" in path:
                return {"tree": [{"path": "requirements.txt", "type": "blob"}, {"path": "Dockerfile", "type": "blob"}]}
            raise AssertionError(path)

        with mock.patch.object(github, "_api", side_effect=fake_api), \
             mock.patch.object(github, "_raw", return_value="django\npytest\n"):
            res = github.analyze_user("dev1")
        slugs = {s["slug"]: s for s in res["skills"]}
        self.assertEqual(res["repos_analyzed"], 1)  # fork skipped
        self.assertTrue({"python", "django", "docker", "testing", "html"} <= set(slugs))
        self.assertIn("shop-api", slugs["django"]["evidence"])
        self.assertEqual(res["top_languages"][0]["name"], "Python")


class PdfTests(SimpleTestCase):
    def _doc(self):
        step = {"slug": "react", "name": "React <script>", "category": "frontend", "priority": "required",
                "reason": "Required by the job", "hours": 40, "difficulty": "hard", "start_week": 1, "end_week": 4,
                "project": "Build an app \u2014 with \u201cquotes\u201d and emoji \U0001F680",
                "resources": {"video": [{"title": "Search", "url": "https://www.youtube.com/results?search_query=react", "kind": "search"}],
                              "content": [{"title": "Learn React", "url": "https://react.dev/learn", "kind": "docs",
                                           "description": "Official docs & more", "est_hours": 20},
                                          {"title": "Evil", "url": "javascript:alert(1)"}]}}
        return {"format": "skillmesh.roadmap", "version": 1, "job": {"title": "Frontend Dev", "company": "Acme"},
                "summary": {"score": 62, "verdict": "Stretch"},
                "roadmap": {"hours_per_week": 10, "total_hours": 40, "total_weeks": 4,
                            "phases": [{"key": "required", "title": "Phase 2: Close the required gaps", "description": "x",
                                        "weeks": [1, 4], "hours": 40, "steps": [step]}]}}

    def test_builds_pdf(self):
        from analyzer.pdf import build_roadmap_pdf
        data, name = build_roadmap_pdf(self._doc())
        self.assertTrue(data.startswith(b"%PDF"))
        self.assertGreater(len(data), 3000)
        self.assertEqual(name, "skillmesh-roadmap-frontend-dev.pdf")
        self.assertNotIn(b"javascript:alert", data)

    def test_rejects_non_roadmap(self):
        from analyzer.pdf import RoadmapPDFError, build_roadmap_pdf
        with self.assertRaises(RoadmapPDFError):
            build_roadmap_pdf({"format": "other"})


@override_settings(SKILLMESH={**settings.SKILLMESH, "LLM_ENABLED": False, "LIVE_DISCOVERY": False})
class NewEndpointTests(TestCase):
    def setUp(self):
        self.c = Client()

    def test_pdf_endpoint(self):
        r = self.c.post("/api/roadmap/pdf/", data=json.dumps(PdfTests()._doc()), content_type="application/json")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(base64.b64decode(body["data"]).startswith(b"%PDF"))
        self.assertEqual(body["filename"], "skillmesh-roadmap-frontend-dev.pdf")
        raw = self.c.post("/api/roadmap/pdf/?format=pdf", data=json.dumps(PdfTests()._doc()), content_type="application/json")
        self.assertEqual(raw["Content-Type"], "application/pdf")

    def test_github_endpoint_validation(self):
        r = self.c.post("/api/github/analyze/", data=json.dumps({"username": "https://evil.com/x"}), content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_analyze_merges_github(self):
        job = {"seniority": "mid", "required": [{"slug": "docker", "name": "Docker"}, {"slug": "python", "name": "Python"}], "preferred": []}
        payload = {"job": job, "profile": {"skills": [{"slug": "python", "name": "Python", "strength": "listed"}]},
                   "github": {"skills": [{"slug": "docker", "name": "Docker", "level": 70, "evidence": "Used in 2 repos"},
                                         {"slug": "python", "name": "Python", "level": 80, "evidence": "Used in 5 repos"}]}}
        r = self.c.post("/api/analyze/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(r.status_code, 200)
        rows = {x["slug"]: x for x in r.json()["report"]["required"]}
        self.assertEqual(rows["docker"]["sources"], ["github"])
        self.assertEqual(rows["python"]["sources"], ["resume", "github"])
        self.assertGreaterEqual(rows["python"]["your_level"], 80)

    def test_tutmesh_page(self):
        r = self.c.get("/tutmesh/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Import roadmap")



class KeylessDiscoveryTests(SimpleTestCase):
    def test_parse_youtube_urls(self):
        from resources.youtube_keyless import parse_youtube
        self.assertEqual(parse_youtube("https://www.youtube.com/watch?v=fqMOX6JJhGo")["kind"], "video")
        self.assertEqual(parse_youtube("https://youtu.be/fqMOX6JJhGo")["id"], "fqMOX6JJhGo")
        pl = parse_youtube("https://www.youtube.com/watch?v=abcdefgh&list=PL_Dk6MPQLXEfYnMfGCoDnOVDONzN2NRiX")
        self.assertEqual((pl["kind"], pl["url"]), ("playlist", "https://www.youtube.com/playlist?list=PL_Dk6MPQLXEfYnMfGCoDnOVDONzN2NRiX"))
        self.assertEqual(parse_youtube("https://www.youtube.com/watch?v=abcdefg&list=RDabcdefg")["kind"], "video")  # mixes ignored
        self.assertIsNone(parse_youtube("https://evil.com/watch?v=fqMOX6JJhGo"))
        self.assertIsNone(parse_youtube("https://www.youtube.com/@freecodecamp"))

    def test_ddg_html_parsing(self):
        from resources.websearch import parse_ddg_html
        page = ('<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3Dabcdefghij&amp;rut=x">Docker <b>Full</b> Course</a>'
                '<a class="result__snippet" href="#">Learn &amp; build</a>'
                '<a rel="nofollow" class="result__a" href="https://duckduckgo.com/y.js?ad=1">Ad</a>')
        res = parse_ddg_html(page)
        self.assertEqual(res, [{"url": "https://www.youtube.com/watch?v=abcdefghij", "title": "Docker Full Course", "snippet": "Learn & build"}])

    def test_keyless_scoring(self):
        from resources.quality import score_youtube_keyless as s
        self.assertGreater(s("Docker Full Course for Beginners", "playlist", "allow", ["Docker"]), 0.8)
        self.assertGreaterEqual(s("Docker Full Course for Beginners", "video", None, ["Docker"]), 0.5)
        self.assertLess(s("My morning routine vlog", "video", None, ["Docker"]), 0.5)
        self.assertEqual(s("Docker in 5 minutes!!!", "video", None, ["Docker"]), 0.0)
        self.assertGreater(s("Azure course - pass in 3 hours!", "video", "allow", ["Azure"]), 0.5)
        self.assertEqual(s("Docker Full Course", "video", "block", ["Docker"]), 0.0)

    def test_discover_videos_verifies_with_oembed(self):
        from resources import youtube_keyless as yk
        results = [{"url": "https://www.youtube.com/watch?v=goodvideo01", "title": "", "snippet": ""},
                   {"url": "https://www.youtube.com/watch?v=deadvideo01", "title": "", "snippet": ""},
                   {"url": "https://www.youtube.com/shorts/shortvid01", "title": "", "snippet": ""}]
        meta = {"https://www.youtube.com/watch?v=goodvideo01": {"title": "Docker Full Course", "author_name": "SomeChannel"}}
        with mock.patch.object(yk, "search", return_value=results), \
             mock.patch.object(yk, "oembed", side_effect=lambda u: meta.get(u)), \
             mock.patch.object(yk, "_trust_lookup", return_value={}):
            out = yk.discover_videos("Docker", ["Docker"])
        self.assertEqual([o["url"] for o in out], ["https://www.youtube.com/watch?v=goodvideo01"])
        self.assertEqual(out[0]["metadata"]["video_id"], "goodvideo01")

    def test_search_unavailable_propagates(self):
        from resources import youtube_keyless as yk
        from resources.websearch import SearchUnavailable
        with mock.patch.object(yk, "search", side_effect=SearchUnavailable("throttled")):
            with self.assertRaises(SearchUnavailable):
                yk.discover_videos("Docker", ["Docker"])


class ReaderTests(SimpleTestCase):
    def test_extracts_blocks_and_skips_chrome(self):
        from analyzer.reader import extract_article
        html = """<html><head><meta property="og:title" content="Learn HTTP"><meta property="og:site_name" content="MDN"></head>
        <body><nav class="site-nav">Home Docs</nav>
        <div class="layout max-w-sidebar"><article><h1>HTTP</h1><p>HTTP is a protocol for <b>fetching</b> resources.</p>
        <ul><li>Requests</li><li>Responses</li></ul><pre>GET / HTTP/1.1\nHost: x</pre>
        <div class="cookie-banner">Accept cookies</div><script>alert(1)</script></article></div>
        <footer>Footer stuff</footer></body></html>"""
        art = extract_article("https://developer.mozilla.org/x", html)
        types = [b["type"] for b in art["blocks"]]
        texts = " ".join(b["text"] for b in art["blocks"])
        self.assertEqual(art["title"], "Learn HTTP")
        self.assertEqual(art["site"], "MDN")
        self.assertEqual(types, ["h2", "p", "li", "li", "code"])
        self.assertIn("GET / HTTP/1.1\nHost: x", texts)
        for junk in ("Home Docs", "Accept cookies", "alert", "Footer"):
            self.assertNotIn(junk, texts)

    def test_allowlist(self):
        from analyzer.reader import ReaderError, read
        with mock.patch("analyzer.reader.reader_allowed", return_value=False):
            with self.assertRaises(ReaderError) as cm:
                read("https://random-blog.example/post")
        self.assertEqual(cm.exception.code, "not_allowed")


@override_settings(SKILLMESH={**settings.SKILLMESH, "GITHUB_FREE_ANALYSES": 1, "GITHUB_CLIENT_ID": "", "GITHUB_CLIENT_SECRET": ""})
class GitHubFreeLimitTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.c = Client()
        self.fake = {"username": "x", "skills": [], "repos_analyzed": 1, "public_repos": 1, "total_stars": 0,
                     "top_languages": [], "repos": [], "profile_url": "https://github.com/x"}

    def call(self, name):
        return self.c.post("/api/github/analyze/", data=json.dumps({"username": name}), content_type="application/json")

    def test_one_free_then_login_required(self):
        with mock.patch.object(github, "analyze_user", return_value=self.fake) as m:
            self.assertEqual(self.call("alice").status_code, 200)
            self.assertEqual(self.call("alice").status_code, 200)  # same profile again is fine
            r = self.call("bob")
            self.assertEqual(r.status_code, 401)
            self.assertEqual(r.json()["error"]["code"], "login_required")
            self.assertEqual(m.call_count, 2)
        status = self.c.get("/api/github/status/").json()
        self.assertEqual((status["signed_in"], status["free_remaining"], status["oauth_configured"]), (False, 0, False))

    def test_limit_also_tracked_per_ip(self):
        with mock.patch.object(github, "analyze_user", return_value=self.fake):
            self.assertEqual(self.call("alice").status_code, 200)
            fresh = Client()  # new browser session, same IP
            r = fresh.post("/api/github/analyze/", data=json.dumps({"username": "carol"}), content_type="application/json")
            self.assertEqual(r.status_code, 401)

    def test_free_analysis_resets_after_a_day(self):
        from django.core.cache import cache
        with mock.patch.object(github, "analyze_user", return_value=self.fake):
            self.assertEqual(self.call("alice").status_code, 200)
            self.assertEqual(self.call("bob").status_code, 401)
            session = self.c.session
            session["gh_free_at"] -= 24 * 3600 + 1  # a day later: session allowance and the IP cache entry have expired
            session.save()
            self.c.cookies["sessionid"] = session.session_key
            cache.clear()
            self.assertEqual(self.c.get("/api/github/status/").json()["free_remaining"], 1)
            self.assertEqual(self.call("bob").status_code, 200)
            self.assertEqual(self.call("alice").status_code, 401)

    def test_signed_in_users_unlimited_and_token_used(self):
        session = self.c.session
        session["gh_token"] = "gho_test"
        session["gh_login"] = "me"
        session.save()
        self.c.cookies["sessionid"] = session.session_key
        with mock.patch.object(github, "analyze_user", return_value=self.fake) as m:
            for name in ("a1", "b2", "c3"):
                self.assertEqual(self.call(name).status_code, 200)
        self.assertEqual(m.call_args.kwargs["token"], "gho_test")

    def test_login_redirect_when_not_configured(self):
        r = self.c.get("/auth/github/login/?next=//evil.com")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], "/?gh=not_configured")

    @override_settings(SKILLMESH={**settings.SKILLMESH, "GITHUB_CLIENT_ID": "cid", "GITHUB_CLIENT_SECRET": "sec"})
    def test_oauth_state_checked(self):
        r = self.c.get("/auth/github/login/?next=/")
        self.assertIn("github.com/login/oauth/authorize", r["Location"])
        self.assertIn("client_id=cid", r["Location"])
        bad = self.c.get("/auth/github/callback/?code=x&state=wrong")
        self.assertEqual(bad["Location"], "/?gh=failed")

    @override_settings(SKILLMESH={**settings.SKILLMESH, "GITHUB_CLIENT_ID": "cid", "GITHUB_CLIENT_SECRET": "sec"})
    def test_oauth_sign_in_completes_without_scopes(self):
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(self.c.get("/auth/github/login/?next=/")["Location"]).query, keep_blank_values=True)
        self.assertEqual(q["scope"], [""])  # public data only: no permissions requested
        token = mock.Mock(status_code=200, **{"json.return_value": {"access_token": "gho_test"}})
        user = mock.Mock(status_code=200, **{"json.return_value": {"login": "octocat"}})
        with mock.patch("analyzer.github_auth.requests.post", return_value=token) as post, \
                mock.patch("analyzer.github_auth.requests.get", return_value=user):
            r = self.c.get(f"/auth/github/callback/?code=abc&state={q['state'][0]}")
        self.assertEqual(r["Location"], "/?gh=signed_in")
        self.assertEqual(post.call_args.kwargs["data"]["code"], "abc")
        status = self.c.get("/api/github/status/").json()
        self.assertEqual((status["signed_in"], status["login"]), (True, "octocat"))
        self.assertNotIn("gho_test", json.dumps(status))  # the token never reaches the page
        with mock.patch.object(github, "analyze_user", return_value=self.fake) as m:
            self.assertEqual(self.call("octocat").status_code, 200)
        self.assertEqual(m.call_args.kwargs["token"], "gho_test")

    @override_settings(SKILLMESH={**settings.SKILLMESH, "GITHUB_CLIENT_ID": "cid", "GITHUB_CLIENT_SECRET": "sec"})
    def test_oauth_cancel_is_reported(self):
        self.c.get("/auth/github/login/?next=/")
        r = self.c.get("/auth/github/callback/?error=access_denied&state=whatever")
        self.assertEqual(r["Location"], "/?gh=cancelled")
        self.assertFalse(self.c.get("/api/github/status/").json()["signed_in"])


class RegressionTests(SimpleTestCase):
    def setUp(self):
        invalidate_index()

    def test_spark_verb_is_not_a_skill(self):
        slugs = {m.slug for m in get_index().find_mentions("in-person work helps spark innovation")}
        self.assertNotIn("data-engineering", slugs)
        slugs = {m.slug for m in get_index().find_mentions("Experience with Apache Spark and PySpark")}
        self.assertIn("data-engineering", slugs)

    def test_title_skips_screenshot_chrome(self):
        text = "careers portal - Job details\nData Analyst\nNorthwind Retail - Berlin\nRequirements:\n- SQL"
        self.assertEqual(heuristics.extract_job(text)["title"], "Data Analyst")



class FrameAndTopicsTests(SimpleTestCase):
    def setUp(self):
        invalidate_index()

    def test_framable_rules(self):
        from analyzer.reader import framable
        self.assertTrue(framable("https://react.dev/learn", {"content-type": "text/html"}))
        self.assertFalse(framable("https://x.dev/", {"X-Frame-Options": "DENY"}))
        self.assertFalse(framable("https://x.dev/", {"content-security-policy": "default-src 'self'; frame-ancestors 'self'"}))
        self.assertTrue(framable("https://x.dev/", {"content-security-policy": "frame-ancestors *"}))
        self.assertFalse(framable("http://insecure.dev/", {}))  # mixed content

    def test_parse_course_jsonld(self):
        from resources.topics import parse_course
        html = """<script type="application/ld+json">{"@context":"https://schema.org","@type":"Course","name":"Docker for Beginners",
        "provider":{"@type":"Organization","name":"KodeKloud"},"teaches":["Build images"," Use Compose"],
        "about":["Docker (Software)"],"syllabusSections":[{"@type":"Syllabus","name":"Module 1: Docker Commands",
        "description":"Learn the CLI. More text here.","timeRequired":"PT1H5M"}]}</script>"""
        c = parse_course(html, "https://www.coursera.org/learn/x")
        self.assertEqual(c["provider"], "KodeKloud")
        self.assertEqual(c["sections"][0], {"name": "Docker Commands", "description": "Learn the CLI. More text here.", "minutes": 65})
        self.assertEqual(c["teaches"], ["Build images", "Use Compose"])

    def test_parse_course_html_fallback(self):
        from resources.topics import parse_course
        html = """<h1>GraphQL Fundamentals</h1><h2>What you&#x27;ll learn</h2><ul><li>Design schemas</li></ul>
        <p>There are 2 modules in this course</p>
        <div><h3>Understanding GraphQL</h3><span>Module 1 • 3 hours to complete</span></div>
        <div><h3>Schema Language</h3><span>Module 2 • 45 minutes to complete</span></div>
        <h3>Instructors</h3><h3>Someone</h3>"""
        c = parse_course(html, "https://www.coursera.org/learn/g")
        self.assertEqual([s["name"] for s in c["sections"]], ["Understanding GraphQL", "Schema Language"])
        self.assertEqual([s["minutes"] for s in c["sections"]], [180, 45])
        self.assertEqual(c["title"], "GraphQL Fundamentals")

    def test_merge_ranks_and_filters(self):
        from resources.topics import merge_topics
        courses = [
            {"title": "A", "url": "https://c/a", "provider": "X", "sections": [
                {"name": "Introduction", "description": "", "minutes": 5},
                {"name": "Docker Images", "description": "Images. Layers.", "minutes": 60},
                {"name": "Kubernetes Basics", "description": "", "minutes": 60},
                {"name": "Docker Compose", "description": "", "minutes": 30}]},
            {"title": "B", "url": "https://c/b", "provider": "Y", "sections": [
                {"name": "Docker images", "description": "", "minutes": 40},
                {"name": "Final Project", "description": "", "minutes": 90}]},
        ]
        topics = merge_topics(courses, ["Docker"], "docker")
        names = [t["name"] for t in topics]
        self.assertEqual(names, ["Docker Images", "Docker Compose"])  # generic + other-skill modules removed
        self.assertEqual(topics[0]["courses"], 2)
        self.assertEqual(len(topics[0]["sources"]), 2)

    def test_pdf_includes_topics(self):
        from analyzer.pdf import _normalize
        doc = PdfTests()._doc()
        doc["roadmap"]["phases"][0]["steps"][0]["resources"]["topics"] = {"topics": [{"name": "Hooks"}, {"name": "State"}]}
        self.assertEqual(_normalize(doc)["phases"][0]["steps"][0]["topics"], ["Hooks", "State"])


class TopicQueryTests(SimpleTestCase):
    def test_query_variants(self):
        from resources.topics import _queries
        self.assertEqual(_queries("Git & Version Control", ["Git & Version Control", "git", "github"]), ["Git & Version Control", "Git"])
        self.assertEqual(_queries("State Management (Redux)", ["State Management (Redux)", "redux"]), ["State Management (Redux)", "State Management", "redux"])
