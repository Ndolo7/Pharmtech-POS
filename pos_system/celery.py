import os

from celery import Celery
from decouple import config


django_env = config("DJANGO_ENV", default="dev").lower()

os.environ.setdefault("DJANGO_SETTINGS_MODULE", f"pos_system.settings.{django_env}")

app = Celery("pos_system")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@app.task(bind=True)
def debug_task(self):
    print(f"Request: {self.request!r}")
