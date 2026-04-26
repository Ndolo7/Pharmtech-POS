import datetime
from django.conf import settings
from django.db import migrations, models


def set_existing_default_sunday_time_and_sync(apps, schema_editor):
    AutoOrderScheduleSetting = apps.get_model("products", "AutoOrderScheduleSetting")
    legacy_default = datetime.time(13, 0)
    new_default = datetime.time(11, 0)

    AutoOrderScheduleSetting.objects.filter(sunday_run_time=legacy_default).update(
        sunday_run_time=new_default
    )

    config = AutoOrderScheduleSetting.objects.order_by("id").first()
    sunday_time = getattr(config, "sunday_run_time", None) or new_default

    try:
        from django_celery_beat.models import CrontabSchedule, PeriodicTask
    except Exception:
        return

    sunday_schedule, _ = CrontabSchedule.objects.get_or_create(
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
        ('products', '0015_force_daily_appointed_autoorder_schedule'),
    ]

    operations = [
        migrations.AlterField(
            model_name="autoorderschedulesetting",
            name="sunday_run_time",
            field=models.TimeField(
                default=datetime.time(11, 0),
                help_text="Sunday supplier review run time (Africa/Nairobi).",
            ),
        ),
        migrations.RunPython(
            set_existing_default_sunday_time_and_sync,
            noop_reverse,
        ),
    ]
