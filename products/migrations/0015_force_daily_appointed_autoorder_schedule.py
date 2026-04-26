from django.conf import settings
from django.db import migrations, models


def force_daily_schedule_and_sync(apps, schema_editor):
    AutoOrderScheduleSetting = apps.get_model("products", "AutoOrderScheduleSetting")
    AutoOrderScheduleSetting.objects.all().update(use_daily_run_time=True)
    config = AutoOrderScheduleSetting.objects.order_by("id").first()

    try:
        from django_celery_beat.models import CrontabSchedule, PeriodicTask
    except Exception:
        return

    if config:
        daily_hour = str(config.daily_run_time.hour)
        daily_minute = str(config.daily_run_time.minute)
        sunday_hour = str(config.sunday_run_time.hour)
        sunday_minute = str(config.sunday_run_time.minute)
    else:
        daily_hour = "8"
        daily_minute = "0"
        sunday_hour = "11"
        sunday_minute = "0"

    timezone_name = getattr(settings, "TIME_ZONE", "UTC")
    daily_schedule, _ = CrontabSchedule.objects.get_or_create(
        minute=daily_minute,
        hour=daily_hour,
        day_of_week="*",
        day_of_month="*",
        month_of_year="*",
        timezone=timezone_name,
    )
    sunday_schedule, _ = CrontabSchedule.objects.get_or_create(
        minute=sunday_minute,
        hour=sunday_hour,
        day_of_week="0",
        day_of_month="*",
        month_of_year="*",
        timezone=timezone_name,
    )

    PeriodicTask.objects.update_or_create(
        name="scan-low-stock-and-trigger-reorders",
        defaults={
            "task": "products.tasks.scan_low_stock_and_trigger_reorders",
            "enabled": True,
            "one_off": False,
            "crontab": daily_schedule,
            "interval": None,
            "solar": None,
            "clocked": None,
        },
    )
    PeriodicTask.objects.update_or_create(
        name="scan-low-stock-and-trigger-reorders-sunday-11am",
        defaults={
            "task": "products.tasks.scan_low_stock_and_trigger_reorders",
            "enabled": True,
            "one_off": False,
            "crontab": sunday_schedule,
            "interval": None,
            "solar": None,
            "clocked": None,
        },
    )


def noop_reverse(apps, schema_editor):
    return


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0014_autoorderschedulesetting_sunday_run_time"),
    ]

    operations = [
        migrations.AlterField(
            model_name="autoorderschedulesetting",
            name="use_daily_run_time",
            field=models.BooleanField(
                default=True,
                help_text="Deprecated setting kept for backward compatibility.",
            ),
        ),
        migrations.RunPython(
            force_daily_schedule_and_sync,
            noop_reverse,
        ),
    ]
