from django.conf import settings
from django.db import migrations, models


def create_sunday_auto_order_periodic_task(apps, schema_editor):
    from django_celery_beat.models import CrontabSchedule, PeriodicTask

    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute="0",
        hour="11",
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
        ("django_celery_beat", "0019_alter_periodictasks_options"),
        ("products", "0012_autoorderschedulesetting"),
    ]

    operations = [
        migrations.AddField(
            model_name="purchase",
            name="supplier_confirmation_emailed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(
            create_sunday_auto_order_periodic_task,
            noop_reverse,
        ),
    ]
