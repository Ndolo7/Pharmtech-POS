import os

from celery import Celery
from decouple import config


django_env = config("DJANGO_ENV", default="dev").lower()
settings_module = {
    "dev": "pos_system.settings.dev",
    "prod": "pos_system.settings.prod",
    "production": "pos_system.settings.prod",
}.get(django_env, "pos_system.settings.dev")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", settings_module)

app = Celery("pos_system")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@app.task(bind=True)
def debug_task(self):
    print(f"Request: {self.request!r}")
