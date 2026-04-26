from datetime import time

from django.conf import settings
from django.db import migrations, models


def sync_sunday_periodic_task(apps, schema_editor):
    AutoOrderScheduleSetting = apps.get_model("products", "AutoOrderScheduleSetting")

    try:
        from django_celery_beat.models import CrontabSchedule, PeriodicTask
    except Exception:
        return

    config = AutoOrderScheduleSetting.objects.order_by("id").first()
    sunday_time = getattr(config, "sunday_run_time", None) or time(13, 0)
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute=str(sunday_time.minute),
        hour=str(sunday_time.hour),
        day_of_week="0",
        day_of_month="*",
        month_of_year="*",
        timezone=getattr(settings, "TIME_ZONE", "UTC"),
    )
    PeriodicTask.objects.update_or_create(
        name="scan-low-stock-and-trigger-reorders-sunday-11am",
        defaults={
            "task": "products.tasks.scan_low_stock_and_trigger_reorders",
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
        ("products", "0013_purchase_supplier_confirmation_and_sunday_schedule"),
    ]

    operations = [
        migrations.AddField(
            model_name="autoorderschedulesetting",
            name="sunday_run_time",
            field=models.TimeField(
                default=time(13, 0),
                help_text="Sunday supplier review run time (Africa/Nairobi).",
            ),
        ),
        migrations.RunPython(
            sync_sunday_periodic_task,
            noop_reverse,
        ),
    ]
