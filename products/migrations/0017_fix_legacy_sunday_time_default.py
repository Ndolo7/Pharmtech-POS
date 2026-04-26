import datetime

from django.conf import settings
from django.db import migrations


LEGACY_SUNDAY_TIME = datetime.time(13, 0)
NEW_SUNDAY_TIME = datetime.time(11, 0)
SUNDAY_TASK_NAME = "scan-low-stock-and-trigger-reorders-sunday-11am"
SUNDAY_TASK = "products.tasks.scan_low_stock_and_trigger_reorders"


def forward_fix_legacy_sunday_time(apps, schema_editor):
    AutoOrderScheduleSetting = apps.get_model("products", "AutoOrderScheduleSetting")
    AutoOrderScheduleSetting.objects.filter(sunday_run_time=LEGACY_SUNDAY_TIME).update(
        sunday_run_time=NEW_SUNDAY_TIME
    )

    try:
        from django_celery_beat.models import CrontabSchedule, PeriodicTask
    except Exception:
        return

    config = AutoOrderScheduleSetting.objects.order_by("id").first()
    sunday_time = getattr(config, "sunday_run_time", None) or NEW_SUNDAY_TIME

    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute=str(sunday_time.minute),
        hour=str(sunday_time.hour),
        day_of_week="0",
        day_of_month="*",
        month_of_year="*",
        timezone=getattr(settings, "TIME_ZONE", "UTC"),
    )
    PeriodicTask.objects.update_or_create(
        name=SUNDAY_TASK_NAME,
        defaults={
            "task": SUNDAY_TASK,
            "enabled": True,
            "one_off": False,
            "crontab": schedule,
            "interval": None,
            "solar": None,
            "clocked": None,
        },
    )


def noop_reverse(apps, schema_editor):
    return


class Migration(migrations.Migration):
    dependencies = [
        ("products", "0016_alter_autoorderschedulesetting_sunday_run_time"),
    ]

    operations = [
        migrations.RunPython(forward_fix_legacy_sunday_time, noop_reverse),
    ]
