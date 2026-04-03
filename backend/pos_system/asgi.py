"""
ASGI config for pos_system project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/4.2/howto/deployment/asgi/
"""

import os

from decouple import config
from django.core.asgi import get_asgi_application

django_env = config("DJANGO_ENV", default="dev").lower()
settings_module = {
    "dev": "pos_system.settings.dev",
    "prod": "pos_system.settings.prod",
    "production": "pos_system.settings.prod",
}.get(django_env, "pos_system.settings.dev")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", settings_module)

application = get_asgi_application()
