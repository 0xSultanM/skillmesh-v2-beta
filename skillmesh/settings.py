"""
SkillMesh AI settings.

Privacy model
-------------
* User documents (resumes, job descriptions) are processed **in memory only**:
  uploads use the in-memory upload handler, nothing is written to disk or the
  database, and request bodies are never logged.
* PostgreSQL stores **public data only**: the skill taxonomy and the cache of
  vetted learning resources (YouTube playlists, articles, free courses).
* Analysis history lives in the user's browser (localStorage) and can be wiped
  from the UI.
"""
import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-insecure-change-me")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = [h.strip() for h in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,.vercel.app").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "https://*.vercel.app").split(",") if o.strip()]

if not DEBUG and SECRET_KEY == "dev-insecure-change-me":
    raise RuntimeError("Set DJANGO_SECRET_KEY in production.")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "taxonomy",
    "resources",
    "analyzer",
    "web",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "web.middleware.PrivacyHeadersMiddleware",
]

ROOT_URLCONF = "skillmesh.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "skillmesh.wsgi.application"

# --- Database: PostgreSQL via DATABASE_URL env var -------------------------
# Set DATABASE_URL in Vercel environment variables:
#   postgresql://USER:PASSWORD@HOST:PORT/DBNAME
# Free options: Supabase, Neon, Railway, Aiven
DATABASES = {
    "default": dj_database_url.config(
        default=os.getenv("DATABASE_URL", ""),
        conn_max_age=600,
        ssl_require=not DEBUG,
    )
}

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# --- Static files: WhiteNoise serves them directly from Django -------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "web" / "static"]
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Sessions: kept in a signed, HttpOnly cookie, never in the database -----
SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 7

# --- Uploads: memory only, never touch disk -------------------------------
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
FILE_UPLOAD_HANDLERS = ["django.core.files.uploadhandler.MemoryFileUploadHandler"]
FILE_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_BYTES
DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_BYTES + 512 * 1024

# --- Security headers -----------------------------------------------------
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "no-referrer"
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SSL_REDIRECT", True)
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30

# --- Logging: never log request bodies -----------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {"django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False}},
}

# --- SkillMesh configuration ---------------------------------------------
SKILLMESH = {
    "ANTHROPIC_MODEL": os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5"),
    "LLM_ENABLED": env_bool("LLM_ENABLED", True),
    "REDACT_PII": env_bool("REDACT_PII", True),
    "YOUTUBE_API_KEY": os.getenv("YOUTUBE_API_KEY", ""),
    "BRAVE_SEARCH_API_KEY": os.getenv("BRAVE_SEARCH_API_KEY", ""),
    "GITHUB_TOKEN": os.getenv("GITHUB_TOKEN", ""),
    "GITHUB_CLIENT_ID": os.getenv("GITHUB_CLIENT_ID", ""),
    "GITHUB_CLIENT_SECRET": os.getenv("GITHUB_CLIENT_SECRET", ""),
    "GITHUB_OAUTH_REDIRECT_URI": os.getenv("GITHUB_OAUTH_REDIRECT_URI", ""),
    "GITHUB_FREE_ANALYSES": int(os.getenv("GITHUB_FREE_ANALYSES", "1")),
    "LIVE_DISCOVERY": env_bool("LIVE_DISCOVERY", True),
    "DISCOVERY_BUDGET_SECONDS": float(os.getenv("DISCOVERY_BUDGET_SECONDS", "25")),
    "SCRAPE_TIMEOUT": float(os.getenv("SCRAPE_TIMEOUT", "10")),
    "SCRAPE_MAX_BYTES": int(os.getenv("SCRAPE_MAX_BYTES", str(3 * 1024 * 1024))),
    "SCRAPE_USER_AGENT": os.getenv("SCRAPE_USER_AGENT", "SkillMeshBot/0.1 (+job-skill-analysis)"),
    "RATE_LIMIT_PER_MIN": int(os.getenv("RATE_LIMIT_PER_MIN", "20")),
}
