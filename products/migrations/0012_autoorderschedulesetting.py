from datetime import time

from django.conf import settings
from django.db import migrations, models


def create_auto_order_schedule_setting_and_periodic_task(apps, schema_editor):
    AutoOrderScheduleSetting = apps.get_model("products", "AutoOrderScheduleSetting")
    AutoOrderScheduleSetting.objects.get_or_create(
        pk=1,
        defaults={
            "use_daily_run_time": False,
            "daily_run_time": time(8, 0),
        },
    )

    from django_celery_beat.models import IntervalSchedule, PeriodicTask

    interval_minutes = max(int(getattr(settings, "AUTO_ORDER_CHECK_INTERVAL_MINUTES", 15) or 15), 1)
    interval_schedule, _ = IntervalSchedule.objects.get_or_create(
        every=interval_minutes,
        period=IntervalSchedule.MINUTES,
    )

    PeriodicTask.objects.update_or_create(
        name="scan-low-stock-and-trigger-reorders",
        defaults={
            "task": "products.tasks.scan_low_stock_and_trigger_reorders",
            "enabled": True,
            "one_off": False,
            "interval": interval_schedule,
            "crontab": None,
            "solar": None,
            "clocked": None,
        },
    )


def noop_reverse(apps, schema_editor):
    return


class Migration(migrations.Migration):

    dependencies = [
        ("django_celery_beat", "0019_alter_periodictasks_options"),
        ("products", "0011_autoreorderrequest_origin"),
    ]

    operations = [
        migrations.CreateModel(
            name="AutoOrderScheduleSetting",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "use_daily_run_time",
                    models.BooleanField(
                        default=False,
                        help_text="When enabled, auto stock checks run once daily at the selected time.",
                    ),
                ),
                (
                    "daily_run_time",
                    models.TimeField(
                        default=time(8, 0),
                        help_text="Daily run time (Africa/Nairobi) used when daily override is enabled.",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Auto Order Schedule Setting",
                "verbose_name_plural": "Auto Order Schedule Setting",
            },
        ),
        migrations.RunPython(
            create_auto_order_schedule_setting_and_periodic_task,
            noop_reverse,
        ),
    ]
