from .base import *
from decouple import config
import dj_database_url

DEBUG = False

DATABASES = {
    "default": dj_database_url.parse(config("DATABASE_URL")),
}

STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
