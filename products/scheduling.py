from __future__ import annotations

from django.conf import settings

from .models import AutoOrderScheduleSetting

AUTO_ORDER_SCAN_TASK = "products.tasks.scan_low_stock_and_trigger_reorders"
AUTO_ORDER_PERIODIC_TASK_NAME = "scan-low-stock-and-trigger-reorders"


def _interval_minutes() -> int:
    configured = getattr(settings, "AUTO_ORDER_CHECK_INTERVAL_MINUTES", 1440)
    try:
        return max(int(configured or 1440), 1)
    except (TypeError, ValueError):
        return 1440


def sync_auto_order_periodic_task(config: AutoOrderScheduleSetting | None = None):
    """
    Keep the Celery beat database schedule in sync with the admin setting.

    - Daily override enabled: run once daily at config.daily_run_time.
    - Daily override disabled: run using AUTO_ORDER_CHECK_INTERVAL_MINUTES.
    """
    from django_celery_beat.models import CrontabSchedule, IntervalSchedule, PeriodicTask

    if config is None:
        config = AutoOrderScheduleSetting.objects.order_by("id").first()

    if config and config.use_daily_run_time:
        schedule, _ = CrontabSchedule.objects.get_or_create(
            minute=str(config.daily_run_time.minute),
            hour=str(config.daily_run_time.hour),
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
    else:
        schedule, _ = IntervalSchedule.objects.get_or_create(
            every=_interval_minutes(),
            period=IntervalSchedule.MINUTES,
        )

        defaults = {
            "task": AUTO_ORDER_SCAN_TASK,
            "enabled": True,
            "one_off": False,
            "interval": schedule,
            "crontab": None,
            "solar": None,
            "clocked": None,
        }

    periodic_task, _ = PeriodicTask.objects.update_or_create(
        name=AUTO_ORDER_PERIODIC_TASK_NAME,
        defaults=defaults,
    )
    return periodic_task
