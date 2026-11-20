"""
GitHub analyzer: infers skill levels from a user's *public repositories*
(not their bio).

Signals per repository
* languages by bytes of code          (GET /repos/{o}/{r}/languages)
* dependency manifests                 (package.json, requirements.txt, pyproject.toml, go.mod, ...)
* tooling / config files in the tree   (Dockerfile, CI workflows, *.tf, test folders, ...)

API budget: 2 + 2 x repos calls (repos capped at ``MAX_REPOS``). Unauthenticated
GitHub allows 60 calls/hour per server IP, so set ``GITHUB_TOKEN`` in production.
Manifests are read from raw.githubusercontent.com, which doesn't count against
the API quota. Results are public data and cached for an hour per username.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import quote

import requests
from django.conf import settings
from django.core.cache import cache

from taxonomy.normalizer import get_index

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
MAX_REPOS = 12
MAX_MANIFESTS_PER_REPO = 5
MAX_RAW_BYTES = 300_000
TIMEOUT = 10
CACHE_SECONDS = 3600

USERNAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")

LANG_MAP = {
    "python": "python", "jupyter notebook": "python", "javascript": "javascript", "typescript": "typescript",
    "java": "java", "kotlin": "kotlin", "c#": "csharp", "c++": "cpp", "go": "go", "rust": "rust", "php": "php",
    "ruby": "ruby", "swift": "swift", "r": "r-lang", "shell": "bash", "powershell": "bash", "html": "html",
    "css": "css", "scss": "css", "sass": "css", "less": "css", "vue": "vue", "dart": "flutter",
    "dockerfile": "docker", "hcl": "terraform", "plpgsql": "sql", "tsql": "sql", "plsql": "sql",
}

# Dependency names (lower-case) -> taxonomy slug. Exact taxonomy aliases are matched too.
DEP_MAP = {
    # JS / TS
    "react": "react", "react-dom": "react", "next": "nextjs", "vue": "vue", "nuxt": "vue",
    "@angular/core": "angular", "express": "nodejs", "@nestjs/core": "nodejs", "fastify": "nodejs", "koa": "nodejs",
    "typescript": "typescript", "redux": "redux", "@reduxjs/toolkit": "redux", "zustand": "redux", "mobx": "redux",
    "tailwindcss": "tailwind", "sass": "css", "styled-components": "css",
    "jest": "testing", "vitest": "testing", "mocha": "testing", "@testing-library/react": "testing",
    "cypress": "e2e-testing", "@playwright/test": "e2e-testing", "playwright": "e2e-testing", "selenium-webdriver": "e2e-testing",
    "graphql": "graphql", "@apollo/client": "graphql", "apollo-server": "graphql",
    "mongoose": "mongodb", "mongodb": "mongodb", "pg": "postgresql", "mysql2": "mysql", "redis": "redis", "ioredis": "redis",
    "prisma": "orm", "@prisma/client": "orm", "typeorm": "orm", "sequelize": "orm", "drizzle-orm": "orm",
    "jsonwebtoken": "authentication", "passport": "authentication", "next-auth": "authentication",
    "react-native": "react-native", "expo": "react-native", "d3": "data-visualization", "chart.js": "data-visualization",
    "kafkajs": "message-queues", "amqplib": "message-queues", "bullmq": "message-queues",
    "openai": "llm-engineering", "@anthropic-ai/sdk": "llm-engineering", "langchain": "llm-engineering",
    "aws-sdk": "aws", "@aws-sdk/client-s3": "aws", "firebase": "nosql",
    # Python
    "django": "django", "djangorestframework": "django", "flask": "flask", "fastapi": "fastapi",
    "pytest": "testing", "pytest-django": "testing", "unittest2": "testing",
    "pandas": "pandas", "numpy": "pandas", "scikit-learn": "machine-learning", "sklearn": "machine-learning",
    "xgboost": "machine-learning", "lightgbm": "machine-learning",
    "torch": "deep-learning", "pytorch": "deep-learning", "tensorflow": "deep-learning", "keras": "deep-learning",
    "transformers": "nlp", "spacy": "nlp", "nltk": "nlp",
    "anthropic": "llm-engineering", "llama-index": "llm-engineering", "langchain-core": "llm-engineering",
    "matplotlib": "data-visualization", "seaborn": "data-visualization", "plotly": "data-visualization", "streamlit": "data-visualization",
    "sqlalchemy": "orm", "psycopg2": "postgresql", "psycopg2-binary": "postgresql", "psycopg": "postgresql",
    "mysqlclient": "mysql", "pymysql": "mysql", "pymongo": "mongodb", "celery": "message-queues", "kafka-python": "message-queues",
    "boto3": "aws", "google-cloud-storage": "gcp", "azure-storage-blob": "azure",
    "apache-airflow": "data-engineering", "pyspark": "data-engineering", "dbt-core": "data-engineering",
    "mlflow": "mlops", "selenium": "e2e-testing", "pyjwt": "authentication",
    # JVM / others
    "spring-boot-starter-web": "spring", "spring-boot-starter": "spring", "junit": "testing", "junit-jupiter": "testing",
    "hibernate-core": "orm", "github.com/gin-gonic/gin": "go", "rails": "rails", "rspec": "testing",
    "laravel/framework": "laravel", "phpunit/phpunit": "testing", "flutter": "flutter",
}

MANIFEST_FILES = {"package.json", "requirements.txt", "pyproject.toml", "pipfile", "go.mod", "gemfile",
                  "pom.xml", "build.gradle", "build.gradle.kts", "composer.json", "pubspec.yaml", "cargo.toml"}

PATH_SIGNALS = [
    (re.compile(r"(^|/)(dockerfile|docker-compose\.ya?ml|compose\.ya?ml)$", re.I), "docker"),
    (re.compile(r"(^|/)(\.github/workflows/.+\.ya?ml|\.gitlab-ci\.yml|jenkinsfile|\.circleci/config\.yml)$", re.I), "ci-cd"),
    (re.compile(r"\.tf$", re.I), "terraform"),
    (re.compile(r"(^|/)(k8s|kubernetes|helm|charts)/|(^|/)chart\.yaml$", re.I), "kubernetes"),
    (re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]+\.py$|\.(test|spec)\.[jt]sx?$", re.I), "testing"),
    (re.compile(r"(^|/)cypress/|(^|/)playwright\.config\.[jt]s$", re.I), "e2e-testing"),
    (re.compile(r"\.sql$", re.I), "sql"),
    (re.compile(r"(^|/)tailwind\.config\.[cm]?[jt]s$", re.I), "tailwind"),
    (re.compile(r"(^|/)next\.config\.[cm]?[jt]s$", re.I), "nextjs"),
    (re.compile(r"\.(graphql|gql)$", re.I), "graphql"),
    (re.compile(r"(^|/)(openapi|swagger)\.(ya?ml|json)$", re.I), "rest-api"),
    (re.compile(r"(^|/)androidmanifest\.xml$", re.I), "android"),
    (re.compile(r"\.xcodeproj/", re.I), "ios"),
    (re.compile(r"(^|/)manage\.py$", re.I), "django"),
]


class GitHubError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def parse_username(raw: str) -> str:
    s = (raw or "").strip().lstrip("@")
    m = re.match(r"^(?:https?://)?(?:www\.)?github\.com/([^/?#]+)/?(?:[?#].*)?$", s, re.I)
    if m:
        s = m.group(1)
    if not USERNAME_RE.match(s):
        raise GitHubError("invalid_username", "Enter a GitHub username or profile link, e.g. github.com/octocat.")
    return s


def _headers(token: str | None = None) -> dict:
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": settings.SKILLMESH.get("SCRAPE_USER_AGENT", "SkillMeshBot"),
    }
    token = token or settings.SKILLMESH.get("GITHUB_TOKEN")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _api(path: str, params: dict | None = None, token: str | None = None):
    try:
        r = requests.get(f"{API}{path}", params=params, headers=_headers(token), timeout=TIMEOUT)
    except requests.RequestException:
        raise GitHubError("unreachable", "Couldn't reach GitHub. Try again in a moment.")
    if r.status_code == 404:
        raise GitHubError("not_found", "That GitHub user (or repository) wasn't found.")
    if r.status_code in (403, 429) and r.headers.get("X-RateLimit-Remaining") == "0":
        raise GitHubError("rate_limited", "GitHub's rate limit was reached. Sign in with GitHub to continue.")
    if r.status_code == 401 and token:
        raise GitHubError("token_invalid", "Your GitHub sign-in expired. Please sign in again.")
    if r.status_code == 409:  # empty repository
        return None
    if r.status_code != 200:
        raise GitHubError("github_error", f"GitHub returned HTTP {r.status_code}.")
    return r.json()


def _raw(owner: str, repo: str, ref: str, path: str) -> str | None:
    url = f"{RAW}/{quote(owner)}/{quote(repo)}/{quote(ref)}/{quote(path)}"
    try:
        r = requests.get(url, headers={"User-Agent": _headers()["User-Agent"]}, timeout=TIMEOUT, stream=True)
        if r.status_code != 200:
            return None
        data = r.raw.read(MAX_RAW_BYTES + 1, decode_content=True)
        r.close()
        if len(data) > MAX_RAW_BYTES:
            return None
        return data.decode("utf-8", errors="replace")
    except requests.RequestException:
        return None


# ---------- manifest parsers (return dependency names) ----------

def _deps_package_json(text: str) -> set[str]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return set()
    out = set()
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        if isinstance(data.get(key), dict):
            out |= set(data[key])
    return out


def _deps_requirements(text: str) -> set[str]:
    out = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "git+", "http")):
            continue
        name = re.split(r"[<>=!~\[;\s]", line, 1)[0]
        if name:
            out.add(name)
    return out


def _deps_generic(text: str) -> set[str]:
    """Loose extraction for TOML / Gemfile / go.mod / XML / gradle / pubspec: quoted or bare package tokens."""
    tokens = set(re.findall(r"[\"']([A-Za-z0-9@/_.\-]{2,80})[\"']", text))
    tokens |= set(re.findall(r"<artifactId>([^<]{2,80})</artifactId>", text))
    tokens |= set(re.findall(r"^\s*([A-Za-z0-9_.\-/]{2,80})\s*[:=]", text, re.M))
    tokens |= set(re.findall(r"^\s*(?:require\s+)?([a-z0-9.\-]+\.[a-z]{2,}/[\w.\-/]+)\s+v", text, re.M))
    return {t.split("==")[0].split(">=")[0] for t in tokens}


def deps_from_manifest(filename: str, text: str) -> set[str]:
    name = filename.lower().rsplit("/", 1)[-1]
    if name == "package.json":
        return _deps_package_json(text)
    if name == "requirements.txt":
        return _deps_requirements(text)
    return _deps_generic(text)


def skills_from_deps(deps: set[str]) -> set[str]:
    idx = get_index()
    out = set()
    for d in deps:
        key = d.lower().strip()
        slug = DEP_MAP.get(key) or idx.alias_to_slug.get(key)
        if slug and slug in idx.skills:
            out.add(slug)
    return out


def skills_from_paths(paths: list[str]) -> set[str]:
    out = set()
    for p in paths:
        for pat, slug in PATH_SIGNALS:
            if slug not in out and pat.search(p):
                out.add(slug)
    return out


# ---------- analysis ----------

def _analyze_repo(owner: str, repo: dict, token: str | None = None) -> dict:
    name, ref = repo["name"], repo.get("default_branch") or "main"
    result = {"name": name, "url": repo.get("html_url"), "pushed_at": repo.get("pushed_at"),
              "stars": repo.get("stargazers_count", 0), "languages": {}, "skills": set()}
    try:
        result["languages"] = _api(f"/repos/{quote(owner)}/{quote(name)}/languages", token=token) or {}
    except GitHubError as exc:
        if exc.code == "rate_limited":
            raise
    try:
        tree = _api(f"/repos/{quote(owner)}/{quote(name)}/git/trees/{quote(ref)}", {"recursive": "1"}, token=token) or {}
    except GitHubError as exc:
        if exc.code == "rate_limited":
            raise
        tree = {}
    paths = [t["path"] for t in tree.get("tree", []) if t.get("type") in ("blob", "tree")][:20_000]
    result["skills"] |= skills_from_paths(paths)

    manifests = [p for p in paths if p.rsplit("/", 1)[-1].lower() in MANIFEST_FILES
                 and p.count("/") <= 2 and "node_modules" not in p and "vendor/" not in p]
    manifests.sort(key=lambda p: p.count("/"))
    for path in manifests[:MAX_MANIFESTS_PER_REPO]:
        text = _raw(owner, name, ref, path)
        if text:
            result["skills"] |= skills_from_deps(deps_from_manifest(path, text))
    return result


def _months_since(iso: str | None) -> float:
    if not iso:
        return 999
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - dt).days / 30.4


def skill_level(repo_count: int, code_bytes: int, months_since_push: float, stars: int) -> int:
    """Heuristic 0-100 proficiency from public-code evidence."""
    level = 25 + 15 * min(repo_count, 4)            # 40 / 55 / 70 / 85
    if code_bytes > 500_000:
        level += 10
    elif code_bytes > 50_000:
        level += 5
    if months_since_push <= 6:
        level += 5
    elif months_since_push > 24:
        level -= 10
    if stars >= 10:
        level += 5
    return max(20, min(92, level))


def cached(raw_username: str) -> dict | None:
    try:
        return cache.get(f"gh:{parse_username(raw_username).lower()}")
    except GitHubError:
        return None


def analyze_user(raw_username: str, token: str | None = None) -> dict:
    username = parse_username(raw_username)
    cache_key = f"gh:{username.lower()}"
    if (cached := cache.get(cache_key)) is not None:
        return cached

    user = _api(f"/users/{quote(username)}", token=token)
    if not user or user.get("type") not in ("User", "Organization"):
        raise GitHubError("not_found", "That GitHub user wasn't found.")
    repos = _api(f"/users/{quote(username)}/repos", {"type": "owner", "sort": "pushed", "per_page": 100}, token=token) or []
    candidates = [r for r in repos if not r.get("fork") and r.get("size", 0) > 0]
    candidates.sort(key=lambda r: r.get("pushed_at") or "", reverse=True)
    candidates = candidates[:MAX_REPOS]
    if not candidates:
        raise GitHubError("no_repos", "No public, non-fork repositories with code were found for this user.")

    with ThreadPoolExecutor(max_workers=6) as pool:
        analyzed = list(pool.map(lambda r: _analyze_repo(username, r, token), candidates))

    idx = get_index()
    agg: dict[str, dict] = defaultdict(lambda: {"repos": [], "bytes": 0, "last_push": None, "stars": 0})
    lang_bytes: dict[str, int] = defaultdict(int)
    for repo in analyzed:
        repo_skills = set(repo["skills"])
        for lang, nbytes in repo["languages"].items():
            lang_bytes[lang] += nbytes
            slug = LANG_MAP.get(lang.lower())
            if slug and nbytes >= 1500:  # ignore trivial snippets
                repo_skills.add(slug)
                agg[slug]["bytes"] += nbytes
        for slug in repo_skills:
            if slug not in idx.skills:
                continue
            a = agg[slug]
            a["repos"].append(repo["name"])
            a["stars"] += repo["stars"]
            if repo["pushed_at"] and (not a["last_push"] or repo["pushed_at"] > a["last_push"]):
                a["last_push"] = repo["pushed_at"]

    skills = []
    for slug, a in agg.items():
        if not a["repos"]:
            continue
        months = _months_since(a["last_push"])
        level = skill_level(len(a["repos"]), a["bytes"], months, a["stars"])
        when = datetime.fromisoformat(a["last_push"].replace("Z", "+00:00")).strftime("%b %Y") if a["last_push"] else "unknown"
        shown = ", ".join(a["repos"][:3]) + (f" +{len(a['repos']) - 3} more" if len(a["repos"]) > 3 else "")
        skills.append({
            "slug": slug, "name": idx.skills[slug].name, "known": True, "strength": "applied",
            "level": level, "repo_count": len(a["repos"]), "repos": a["repos"][:8],
            "evidence": f"Used in {len(a['repos'])} public repo{'s' if len(a['repos']) != 1 else ''} ({shown}) · last push {when}",
        })
    skills.sort(key=lambda s: (-s["level"], s["name"]))

    total = sum(lang_bytes.values()) or 1
    top_langs = sorted(lang_bytes.items(), key=lambda kv: -kv[1])[:6]
    result = {
        "username": user.get("login", username),
        "profile_url": user.get("html_url", f"https://github.com/{username}"),
        "avatar_url": user.get("avatar_url", ""),
        "public_repos": user.get("public_repos", len(repos)),
        "repos_analyzed": len(analyzed),
        "total_stars": sum(r["stars"] for r in analyzed),
        "top_languages": [{"name": k, "percent": round(v * 100 / total, 1)} for k, v in top_langs],
        "repos": [{"name": r["name"], "url": r["url"], "stars": r["stars"], "pushed_at": r["pushed_at"]} for r in analyzed],
        "skills": skills,
    }
    cache.set(cache_key, result, CACHE_SECONDS)
    return result
