import os
from pathlib import Path

from decouple import Csv, config

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def env_bool(name: str, default: bool = False) -> bool:
    value = str(config(name, default=str(default))).strip().lower()
    return value in {"1", "true", "t", "yes", "y", "on"}


SECRET_KEY = config("SECRET_KEY", default="django-insecure-change-me-in-production")
DEBUG = False
ALLOWED_HOSTS = config(
    "ALLOWED_HOSTS",
    default="localhost,127.0.0.1",
    cast=Csv(),
)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "django_htmx",
    "widget_tweaks",
    "django_filters",
    "anymail",
    # Local apps
    "accounts",
    "products",
    "sales",
    "reports",
    "branches",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]

ROOT_URLCONF = "pos_system.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
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

WSGI_APPLICATION = "pos_system.wsgi.application"
ASGI_APPLICATION = "pos_system.asgi.application"

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Nairobi"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = os.path.join(BASE_DIR, "staticfiles")
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "/media/"
MEDIA_ROOT = os.path.join(BASE_DIR, "media")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

# Auth redirects
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/accounts/login/"

# Email settings
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default="noreply@pharmtech.local")
EMAIL_BACKEND = config("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
ANYMAIL = {
    "BREVO_API_KEY": config("BREVO_API_KEY", default=""),
}

# Automated reorder settings
SITE_BASE_URL = config("SITE_BASE_URL", default="http://localhost:8000")
AUTO_ORDER_LINK_EXPIRY_SECONDS = max(60, config("AUTO_ORDER_LINK_EXPIRY_SECONDS", default=3600, cast=int))
AUTO_ORDER_CHECK_INTERVAL_MINUTES = max(1, config("AUTO_ORDER_CHECK_INTERVAL_MINUTES", default=15, cast=int))
AUTO_ORDER_EMAIL_FROM = config("AUTO_ORDER_EMAIL_FROM", default=DEFAULT_FROM_EMAIL)

# Celery
CELERY_BROKER_URL = config("CELERY_BROKER_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = config("CELERY_RESULT_BACKEND", default=CELERY_BROKER_URL)
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULE = {
    "scan-low-stock-and-trigger-reorders": {
        "task": "products.tasks.scan_low_stock_and_trigger_reorders",
        "schedule": float(AUTO_ORDER_CHECK_INTERVAL_MINUTES * 60),
    },
}
