"""
Django settings for itms_project.

All secrets/config are read from environment variables (.env) via django-environ.
See .env.example for the full list of variables this project expects.
"""
from pathlib import Path
import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
)
# Load .env if present (silently ignored if missing, e.g. in CI)
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY", default="insecure-dev-key-change-me")
DEBUG = env.bool("DJANGO_DEBUG", default=True)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["127.0.0.1", "localhost"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core.apps.CoreConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "itms_project.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "itms_project.wsgi.application"
ASGI_APPLICATION = "itms_project.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", default="itms"),
        "USER": env("POSTGRES_USER", default="postgres"),
        "PASSWORD": env("POSTGRES_PASSWORD", default=""),
        "HOST": env("POSTGRES_HOST", default="localhost"),
        "PORT": env("POSTGRES_PORT", default="5432"),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = env("DJANGO_TIME_ZONE", default="Africa/Kampala")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

# --- Evidence vault / media ---
MEDIA_ROOT = BASE_DIR / env("MEDIA_ROOT", default="media")
MEDIA_URL = "/media/"
VAULT_SUBDIR = env("VAULT_SUBDIR", default="vault")
VAULT_ROOT = MEDIA_ROOT / VAULT_SUBDIR
CROPS_SUBDIR = env("CROPS_SUBDIR", default="crops")
CROPS_ROOT = MEDIA_ROOT / CROPS_SUBDIR

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Vision pipeline config (read by core/vision modules) ---
PLATE_YOLO_WEIGHTS = env("PLATE_YOLO_WEIGHTS", default="models/license-plate-finetune-v1n.pt")
PLATE_DETECTOR_CONF_THRESHOLD = env.float("PLATE_DETECTOR_CONF_THRESHOLD", default=0.35)
OCR_MIN_CONFIDENCE = env.float("OCR_MIN_CONFIDENCE", default=0.55)
USE_PADDLEOCR = env.bool("USE_PADDLEOCR", default=True)

# --- Fuzzy matcher thresholds ---
FUZZY_EXACT_THRESHOLD = env.int("FUZZY_EXACT_THRESHOLD", default=100)
FUZZY_ACCEPT_THRESHOLD = env.int("FUZZY_ACCEPT_THRESHOLD", default=85)
FUZZY_REJECT_THRESHOLD = env.int("FUZZY_REJECT_THRESHOLD", default=75)

# --- Simulated ITMS ---
ITMS_SIMULATED_LATENCY_MS = env.int("ITMS_SIMULATED_LATENCY_MS", default=150)
ITMS_FAILURE_INJECTION_RATE = env.float("ITMS_FAILURE_INJECTION_RATE", default=0.0)

# --- ITMS Web App Live Integration (stock.itms.ug) ---
ITMS_SUBMISSION_BACKEND = env("ITMS_SUBMISSION_BACKEND", default="mock")  # "mock" or "live"
ITMS_API_BASE_URL = env("ITMS_API_BASE_URL", default="https://stock.itms.ug").rstrip("/")
ITMS_LOGIN_ENDPOINT = env("ITMS_LOGIN_ENDPOINT", default="/site/login")
ITMS_REFRESH_ENDPOINT = env("ITMS_REFRESH_ENDPOINT", default="/api/auth/refresh")
ITMS_ORDER_LOOKUP_ENDPOINT = env("ITMS_ORDER_LOOKUP_ENDPOINT", default="/api/orders/lookup")
ITMS_SERIAL_VERIFY_ENDPOINT = env("ITMS_SERIAL_VERIFY_ENDPOINT", default="/api/orders/verify-serial")
ITMS_UPLOAD_FRONT_ENDPOINT = env("ITMS_UPLOAD_FRONT_ENDPOINT", default="/api/orders/upload-front")
ITMS_UPLOAD_REAR_ENDPOINT = env("ITMS_UPLOAD_REAR_ENDPOINT", default="/api/orders/upload-rear")
ITMS_FINALIZE_ENDPOINT = env("ITMS_FINALIZE_ENDPOINT", default="/api/orders/finalize")
ITMS_USERNAME = env("ITMS_USERNAME", default="")
ITMS_PASSWORD = env("ITMS_PASSWORD", default="")
ITMS_REQUEST_TIMEOUT_SECONDS = env.int("ITMS_REQUEST_TIMEOUT_SECONDS", default=30)
ITMS_TOKEN_STORAGE_FILE = env("ITMS_TOKEN_STORAGE_FILE", default=str(VAULT_ROOT / ".itms_tokens.json"))

# Evidence vault retention lifecycle (prunes submitted photos older than N days)
VAULT_RETENTION_DAYS = env.int("VAULT_RETENTION_DAYS", default=7)

