from .base import *

DEBUG = env_bool("DEBUG", default=True)
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0", "*"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}
