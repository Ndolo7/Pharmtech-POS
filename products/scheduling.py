from __future__ import annotations

from datetime import time as dt_time

from django.conf import settings

from .models import AutoOrderScheduleSetting

AUTO_ORDER_SCAN_TASK = "products.tasks.scan_low_stock_and_trigger_reorders"
AUTO_ORDER_PERIODIC_TASK_NAME = "scan-low-stock-and-trigger-reorders"
AUTO_ORDER_SUNDAY_PERIODIC_TASK_NAME = "scan-low-stock-and-trigger-reorders-sunday-11am"

def sync_auto_order_periodic_task(config: AutoOrderScheduleSetting | None = None):
    """
    Keep the Celery beat database schedule in sync with admin timings.

    Reorders should only be initiated by appointed-time checks, so the main
    scan now always runs on a daily crontab at `daily_run_time`.
    """
    from django_celery_beat.models import CrontabSchedule, PeriodicTask

    if config is None:
        config = AutoOrderScheduleSetting.objects.order_by("id").first()

    daily_run_time = config.daily_run_time if config else dt_time(8, 0)
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute=str(daily_run_time.minute),
        hour=str(daily_run_time.hour),
        day_of_week="*",
        day_of_month="*",
        month_of_year="*",
        timezone=getattr(settings, "TIME_ZONE", "UTC"),
    )

    defaults = {
        "task": AUTO_ORDER_SCAN_TASK,
        "enabled": True,
        "one_off": False,
        "crontab": schedule,
        "interval": None,
        "solar": None,
        "clocked": None,
    }

    periodic_task, _ = PeriodicTask.objects.update_or_create(
        name=AUTO_ORDER_PERIODIC_TASK_NAME,
        defaults=defaults,
    )

    sunday_run_time = config.sunday_run_time if config else dt_time(13, 0)

    # Always keep a dedicated Sunday schedule for supplier weekly reviews.
    sunday_schedule, _ = CrontabSchedule.objects.get_or_create(
        minute=str(sunday_run_time.minute),
        hour=str(sunday_run_time.hour),
        day_of_week="0",
        day_of_month="*",
        month_of_year="*",
        timezone=getattr(settings, "TIME_ZONE", "UTC"),
    )
    PeriodicTask.objects.update_or_create(
        name=AUTO_ORDER_SUNDAY_PERIODIC_TASK_NAME,
        defaults={
            "task": AUTO_ORDER_SCAN_TASK,
            "enabled": True,
            "one_off": False,
            "crontab": sunday_schedule,
            "interval": None,
            "solar": None,
            "clocked": None,
        },
    )
    return periodic_task
