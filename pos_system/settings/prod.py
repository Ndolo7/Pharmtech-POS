from decouple import config
import dj_database_url

from .base import *

DEBUG = env_bool("DEBUG", default=False)

DATABASES = {
    "default": dj_database_url.parse(config("DATABASE_URL")),
}

EMAIL_BACKEND = "anymail.backends.brevo.EmailBackend"
