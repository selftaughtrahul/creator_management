import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=True)

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")
DEBUG = os.environ.get("DEBUG", "1") == "1"

if not DEBUG and SECRET_KEY == "dev-only-change-me":
    raise RuntimeError("Set SECRET_KEY in the environment before running with DEBUG=0.")

ALLOWED_HOSTS = [
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "snout-getting-reoccupy.ngrok-free.dev",
]
# ngrok terminates HTTPS and forwards plain HTTP, so the browser origin is https
# while Django would otherwise only trust http://<host>.
CSRF_TRUSTED_ORIGINS = [
    "https://snout-getting-reoccupy.ngrok-free.dev",
]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "django.contrib.humanize",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    "accounts",
    "networks",
    "analytics",
    "creators",
    "campaigns",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "accounts.context_processors.app_context",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"
DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
SITE_ID = 1

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

LOGIN_REDIRECT_URL = "/dashboard/"
LOGIN_URL = "/accounts/login/"
LOGOUT_REDIRECT_URL = "/"

ACCOUNT_ADAPTER = "accounts.adapter.AccountAdapter"
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_SIGNUP_FORM_CLASS = "accounts.forms.NameSignupForm"
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION = True
ACCOUNT_EMAIL_SUBJECT_PREFIX = ""
ACCOUNT_DEFAULT_HTTP_PROTOCOL = "http"
ACCOUNT_LOGOUT_REDIRECT_URL = "/"
ACCOUNT_PHONE_VERIFICATION_ENABLED = False
ACCOUNT_PRESERVE_USERNAME_CASING = False

SOCIALACCOUNT_AUTO_SIGNUP = True
SOCIALACCOUNT_EMAIL_VERIFICATION = "none"
SOCIALACCOUNT_LOGIN_ON_GET = False

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
YOUTUBE_CLIENT_ID = os.environ.get("YOUTUBE_CLIENT_ID", "") or GOOGLE_CLIENT_ID
YOUTUBE_CLIENT_SECRET = os.environ.get("YOUTUBE_CLIENT_SECRET", "") or GOOGLE_CLIENT_SECRET
YOUTUBE_REDIRECT_URI = os.environ.get("YOUTUBE_REDIRECT_URI", "")
YOUTUBE_SYNC_EAGER = os.environ.get("YOUTUBE_SYNC_EAGER", "0") == "1"
YOUTUBE_MAX_VIDEOS = int(os.environ.get("YOUTUBE_MAX_VIDEOS", "2000") or "2000")
YOUTUBE_INCREMENTAL_VIDEOS = int(os.environ.get("YOUTUBE_INCREMENTAL_VIDEOS", "100") or "100")
YOUTUBE_COMMENT_LIMIT = int(os.environ.get("YOUTUBE_COMMENT_LIMIT", "100") or "100")
YOUTUBE_COMMENT_VIDEO_LIMIT = int(os.environ.get("YOUTUBE_COMMENT_VIDEO_LIMIT", "15") or "15")
YOUTUBE_FULL_COMMENT_VIDEO_LIMIT = int(os.environ.get("YOUTUBE_FULL_COMMENT_VIDEO_LIMIT", "40") or "40")
YOUTUBE_TRANSCRIPT_VIDEO_LIMIT = int(os.environ.get("YOUTUBE_TRANSCRIPT_VIDEO_LIMIT", "10") or "10")
YOUTUBE_FULL_TRANSCRIPT_VIDEO_LIMIT = int(os.environ.get("YOUTUBE_FULL_TRANSCRIPT_VIDEO_LIMIT", "20") or "20")
YOUTUBE_ANALYTICS_DAYS = int(os.environ.get("YOUTUBE_ANALYTICS_DAYS", "28") or "28")
YOUTUBE_VIDEO_ANALYTICS_LIMIT = int(os.environ.get("YOUTUBE_VIDEO_ANALYTICS_LIMIT", "25") or "25")
YOUTUBE_RETENTION_VIDEO_LIMIT = int(os.environ.get("YOUTUBE_RETENTION_VIDEO_LIMIT", "5") or "5")
YOUTUBE_HTTP_TIMEOUT = float(os.environ.get("YOUTUBE_HTTP_TIMEOUT", "25") or "25")
YOUTUBE_MAX_RETRIES = int(os.environ.get("YOUTUBE_MAX_RETRIES", "3") or "3")
YOUTUBE_JOB_MAX_ATTEMPTS = int(os.environ.get("YOUTUBE_JOB_MAX_ATTEMPTS", "3") or "3")
YOUTUBE_JOB_BATCH_SIZE = int(os.environ.get("YOUTUBE_JOB_BATCH_SIZE", "5") or "5")
YOUTUBE_STALE_MINUTES = int(os.environ.get("YOUTUBE_STALE_MINUTES", "30") or "30")
YOUTUBE_TRANSCRIPT_PROVIDER_URL = os.environ.get("YOUTUBE_TRANSCRIPT_PROVIDER_URL", "")
YOUTUBE_TRANSCRIPT_PROVIDER_TOKEN = os.environ.get("YOUTUBE_TRANSCRIPT_PROVIDER_TOKEN", "")
META_APP_ID = os.environ.get("META_APP_ID", "")
META_APP_SECRET = os.environ.get("META_APP_SECRET", "")
META_GRAPH_VERSION = os.environ.get("META_GRAPH_VERSION", "v23.0")
META_LOGIN_CONFIG_ID = os.environ.get("META_LOGIN_CONFIG_ID", "")
# Exact callback saved in the Meta app. Empty falls back to the request host.
FACEBOOK_REDIRECT_URI = os.environ.get("FACEBOOK_REDIRECT_URI", "")
INSTAGRAM_APP_ID = os.environ.get("INSTAGRAM_APP_ID", "")
INSTAGRAM_APP_SECRET = os.environ.get("INSTAGRAM_APP_SECRET", "")
INSTAGRAM_REDIRECT_URI = os.environ.get(
    "INSTAGRAM_REDIRECT_URI",
    "https://localhost:8000/connections/instagram/callback/",
)
TWITTER_CLIENT_ID = os.environ.get("TWITTER_CLIENT_ID", "")
TWITTER_CLIENT_SECRET = os.environ.get("TWITTER_CLIENT_SECRET", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
JOB_BATCH_SIZE = int(os.environ.get("JOB_BATCH_SIZE", "10") or "10")
JOB_PLATFORM_DELAY_SECONDS = float(os.environ.get("JOB_PLATFORM_DELAY_SECONDS", "1") or "0")

SOCIALACCOUNT_PROVIDERS = {}
if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
    SOCIALACCOUNT_PROVIDERS["google"] = {
        "APPS": [
            {
                "client_id": GOOGLE_CLIENT_ID,
                "secret": GOOGLE_CLIENT_SECRET,
                "key": "",
            }
        ],
        "SCOPE": ["profile", "email"],
        "AUTH_PARAMS": {"access_type": "online"},
        "OAUTH_PKCE_ENABLED": True,
    }

EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "Awisee <noreply@localhost>")
if EMAIL_HOST:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
    EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
    EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
    EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "1").strip().lower() in {"1", "true", "yes", "on"}
elif DEBUG:
    EMAIL_BACKEND = "accounts.mail.DatabaseEmailBackend"
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "studio": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "studio"},
    },
    "loggers": {
        "creators": {"handlers": ["console"], "level": "INFO"},
        "campaigns": {"handlers": ["console"], "level": "INFO"},
        "networks": {"handlers": ["console"], "level": "INFO"},
    },
}
