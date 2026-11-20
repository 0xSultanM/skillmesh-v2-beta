# SkillMesh AI

Upload a resume, add a job (URL, pasted text or a screenshot), and get an honest, evidence-based fit score plus a free learning roadmap for the gaps.

**Stack:** Python 3.12+ · Django · SQLite · Claude (optional, via the Anthropic SDK) · vanilla JS · tesseract.js (in-browser OCR)

## Quick start

```bash
python3 -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env            # macOS/Linux: cp .env.example .env
python3 manage.py migrate
python3 manage.py seed_taxonomy    # 83 skills with aliases, prerequisites, related skills
python3 manage.py seed_resources   # curated free resources + trusted YouTube channels
python3 manage.py seed_videos      # verify candidate course videos via YouTube oEmbed (keyless)
python3 manage.py refresh_topics   # mine essential topics per skill from Coursera syllabi (slow, polite)
python3 manage.py runserver
```

Open http://127.0.0.1:8000. The app works without any API keys: extraction falls back to rule-based parsing and roadmaps use the curated resources. Add keys to `.env` to turn on:

| Key | Enables |
|---|---|
| `ANTHROPIC_API_KEY` | AI extraction of resumes and job posts (structured outputs, default model `claude-opus-5-5`) |
| `YOUTUBE_API_KEY` | Live discovery of quality-scored course playlists (YouTube Data API v3) |
| `BRAVE_SEARCH_API_KEY` | Live discovery of trusted articles and docs, with paywall filtering |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | "Sign in with GitHub" (OAuth App, no scopes). Without sign-in each visitor gets **1** free GitHub analysis (`GITHUB_FREE_ANALYSES`) |
| `GITHUB_TOKEN` | Raises the GitHub analyzer limit from 60 to 5,000 API calls/hour (each analysis uses ~26) |

Run the tests with `python manage.py test`. Use `python manage.py createsuperuser` to curate skills and resources in `/admin/`.

## Privacy model

| Data | Where it lives |
|---|---|
| Resume file / text | Request memory only (`MemoryFileUploadHandler`). Never written to disk or the database, and never logged |
| Job URL content | Fetched once through an SSRF-guarded client, then discarded |
| Job screenshots | OCR runs **in the browser** (tesseract.js). The image never leaves the device |
| Text sent to the LLM | Emails, phones, street addresses and profile links are redacted first (`REDACT_PII`) |
| Saved analyses and progress | The user's `localStorage`, deletable with "Delete all local data" |
| SQLite | **Public data only:** skill taxonomy and the learning-resource cache |

API responses are sent with `Cache-Control: no-store` and a strict CSP.

## Architecture

```
skillmesh/        settings, root urls
taxonomy/         Skill model, seed data, alias normalizer (React.js -> react, k8s -> kubernetes)
resources/        LearningResource + Channel models, quality scoring, YouTube/web discovery, link checks
analyzer/         the pipeline and JSON API
  documents.py    PDF/DOCX/TXT -> text (in memory)
  net.py          SSRF-safe fetcher (public IPs only, redirect re-validation, size/time caps)
  jobpage.py      JSON-LD JobPosting -> main-content fallback; login-wall detection
  llm.py          Claude structured extraction (prompt-injection hardened, refusal fallback)
  heuristics.py   rule-based extraction (no-key mode and fallback)
  extraction.py   LLM-or-heuristic orchestration + taxonomy normalisation
  github.py       public-repo analyzer (languages, dependency manifests, tooling files; not the bio)
  scoring.py      deterministic, explainable score + per-skill levels + resume/GitHub cross-check
  roadmap.py      prerequisite-ordered, time-boxed plan with easy/medium/hard difficulty
  pdf.py          roadmap -> styled PDF with streak tracker (ReportLab, in memory)
web/              dashboard + TutMesh templates, JS (common/app/tutmesh), CSS, privacy headers
```

### API

| Endpoint | Input | Output |
|---|---|---|
| `GET /api/health/` | none | Which optional features are enabled |
| `POST /api/resume/parse/` | multipart `file`, or JSON `{text}` | `profile` (skills with evidence and applied/listed strength) |
| `POST /api/job/scrape/` | `{url}` | Job text from a public page (`422 login_required` for walled sites) |
| `POST /api/job/parse/` | `{text}` | `job` (required/preferred skills, years, level, hard requirements) |
| `POST /api/github/analyze/` | `{username}` (name or profile URL) | Skills evidenced in public repos, each with a 0-100 level |
| `POST /api/analyze/` | `{job, profile, github?, hours_per_week, include_preferred}` | `report` (with per-skill `your_level` / `required_level`, `cross_check`) + `roadmap` |
| `POST /api/roadmap/pdf/` | A roadmap export document | `{filename, data}` (base64 PDF); add `?format=pdf` for the raw file |

The UI lets users correct the extracted profile and job before analysis, which catches scraping, OCR and extraction mistakes cheaply.

### Skill levels

Each job skill gets a 0-100 **your level** and a **required level** (intern 35, junior 45, mid 60, senior 75, lead 85; nice-to-haves are 10 lower). Your level comes from the resume (used in work vs. only listed, plus years of experience) and from GitHub (repo count, code volume, recency, stars). Claims backed by both sources get a corroboration bonus. The report also shows a cross-check: skills confirmed by code, resume-only claims, and GitHub-only skills to add to the resume.

### Roadmap export & TutMesh

The roadmap exports in three ways, all from one JSON format (`format: "skillmesh.roadmap", version: 1`):

1. **JSON**: a portable file to import into **TutMesh** (`/tutmesh/`).
2. **PDF**: a styled document with stat tiles, an overview, a weekly streak tracker with tick boxes, and step cards with difficulty and links.
3. **Open in TutMesh**: sends the roadmap straight to the tracker in the same browser.

TutMesh is browser-only (localStorage). It tracks step and resource completion, notes, a daily study log with current/best streaks, and a 16-week activity heatmap. It can re-export JSON (with progress) or a PDF. Imported files are strictly validated and sanitised, and only http(s) links are rendered.

### Resource discovery without API keys

| Source | How | Notes |
|---|---|---|
| Curated docs/courses | `seed_resources` | Hand-vetted, with descriptions |
| Verified course videos | `seed_videos` | Candidate list checked with YouTube **oEmbed** (official, keyless): must exist, be public and embeddable, come from a trusted channel and mention the skill. Unverified entries are dropped |
| Live videos | Web search for `site:youtube.com <skill> full course` + oEmbed verification | Uses Brave if `BRAVE_SEARCH_API_KEY` is set, else DuckDuckGo's HTML endpoint (keyless). Keyless search throttles bursts, so it is paced, cached (SQLite + 6 h in-process), pauses for 10 min when throttled, and analysis gives it a time budget (`DISCOVERY_BUDGET_SECONDS`) |
| Live articles | Same web search, ranked by domain trust, verified by fetch (dead or paywalled pages dropped) | |
| YouTube Data API | `YOUTUBE_API_KEY` | Richest signals (subscribers, duplication, likes). Used instead of the keyless path when set |

YouTube's own pages are never scraped. Pre-warm the cache politely with `python manage.py refresh_resources --missing-only --pause 12`.

### In-app player, content preview and automatic streaks

- **Play here**: YouTube videos and playlists open in a popup (IFrame Player API on youtube-nocookie.com). Video rows show thumbnails for a quick preview.
- **Preview here**: articles and docs open in a full-tab popup with a large close button (top-right, or Esc).
  - **Original site**: the real page in a sandboxed frame, offered only when the site allows framing (no `X-Frame-Options`, and `frame-ancestors` absent or permissive).
  - **Reader view**: the server extracts the article into plain-text blocks, which are rendered in a script-free sandboxed frame. This is the fallback for sites that refuse framing (MDN, javascript.info, Docker docs, ...).
  - Only URLs from the resource cache or trusted documentation domains can be previewed, so the preview isn't an open proxy.
- **Automatic tracking**: a day earns a streak at **10 min of video or 5 min of reading**.
  - Watch time counts only while a video is *playing* and the tab is visible.
  - In reader view, time counts while you scroll or move the mouse (45 s idle timeout).
  - In original-site view the browser hides activity inside the frame, so time counts while the preview is open and visible, with a "Still reading?" check every 3 minutes. Clicking inside the page also counts.
  - Data lives in `localStorage` (`sm:activity`). Time spent on other websites can't be measured, so it doesn't count.

### Topics tab (from course syllabi)

Each roadmap step has a **Topics** tab: the essential sub-topics of the skill, in learning order, mined from public **Coursera** course pages.
- Course discovery uses the `/courses?query=` listing, which robots.txt allows. `/search` is disallowed, so we don't use it.
- Each course page's syllabus is read from its schema.org `Course` data (`syllabusSections`, `teaches`), or from the visible module list when that's missing.
- Modules from 2–4 relevant courses are merged when similar and ranked by how many courses teach them. Generic modules ("Introduction", "Final Project") and modules about a different skill (e.g. Kubernetes in a Docker list) are dropped.
- Each topic shows its duration, a short description and its source courses. In TutMesh, topics are a checklist, and the PDF includes them as tick boxes.
- Results are cached per skill in SQLite (`SkillSyllabus`). Refresh with `python manage.py refresh_topics` (use `--empty-only` to retry gaps).
- **Udemy is not used**: it blocks automated access (HTTP 403) and its terms forbid scraping.

### Dark mode

A toggle in the top bar switches themes. Until the user picks one, the site follows the OS setting; the choice is then remembered in `localStorage`. `theme.js` loads in `<head>` so the theme applies before first paint, with no flash of the wrong theme.

### Scoring

```
0.55 × required-skill coverage   (applied = 1.0, listed only = 0.75, implied by a held skill = 0.85, related = 0.45)
0.15 × nice-to-have coverage
0.15 × experience fit             (years vs. required, seniority gap)
0.10 × domain fit
0.05 × education fit
```

The score is capped at 55 when fewer than about 40% of required skills are evidenced, and at 65 when experience is under half of what's required. Hard requirements (visa, clearance, licences) are surfaced for the user to check rather than guessed.

### Resource quality

YouTube playlists are scored on channel reputation, views-per-video (which catches content farms), course structure (video count), **title duplication** (repetitive or re-uploaded content is penalised), like ratio, relevance and recency, with a trusted-educator allow list and a block list. Articles are ranked by domain trust, and paywalled pages are dropped. The LLM never produces links: everything shown comes from the vetted SQLite cache. When nothing vetted exists, the user gets clearly labelled search links.

## Operations

```bash
python manage.py refresh_resources --limit 40   # weekly cron; ~120 YouTube quota units per skill
python manage.py check_links                    # weekly cron; deactivates dead/paywalled links
```

For production, set `DJANGO_DEBUG=false`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, run `collectstatic`, and serve behind a reverse proxy with an egress firewall that blocks private ranges (defence in depth for the scraper).

## Roadmap / next steps

- Background job queue for live discovery (currently inline on first request for an uncached skill)
- On-device "private mode" LLM
- Multi-job comparison and resume-improvement suggestions
- Optional cloud sync for TutMesh progress (opt-in account)
- Expand the taxonomy beyond tech roles (ESCO / O\*NET import)
