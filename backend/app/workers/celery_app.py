"""
Production scale-out path (reference scaffold, not wired into the demo run).

For high outlet counts / high event volume, replace the in-process
APScheduler loop in background_tasks.py with Celery workers + Celery Beat:

    celery_app.py          <- this file: broker/backend config
    tasks.py                <- @celery_app.task wrappers around the same
                                async service functions (run via asyncio.run
                                or an async-friendly Celery pool such as
                                `celery[gevent]` / `celery-pool-asyncio`)
    beat_schedule            <- periodic schedule, one task per outlet or
                                sharded by region, distributed across N workers

Run with:
    celery -A app.workers.celery_app worker --loglevel=info --concurrency=8
    celery -A app.workers.celery_app beat --loglevel=info

This decouples analytics processing from the FastAPI request/response cycle
entirely, so a spike in outlets or data volume scales by adding worker
nodes rather than by adding API replicas.
"""
from celery import Celery
from app.config import settings

celery_app = Celery(
    "shwapno_ops",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Dhaka",
    enable_utc=True,
    beat_schedule={
        "realtime-analytics-scan": {
            "task": "app.workers.celery_tasks.scan_all_outlets",
            "schedule": settings.REALTIME_ANALYTICS_INTERVAL_SECONDS,
        },
    },
)
