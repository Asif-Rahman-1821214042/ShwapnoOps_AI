"""
Real-time analytics worker
---------------------------
Runs the alert engine on a recurring interval so dashboards receive fresh
alerts/tasks without the manager needing to refresh or trigger a scan
manually. Implemented with APScheduler's AsyncIOScheduler for simplicity in
this reference build.

Scaling to production:
  - Swap this in-process scheduler for Celery workers (see celery_app.py)
    triggered by Celery Beat, so scans run on a separate worker pool and
    scale horizontally independent of the API process.
  - Partition scans per-outlet as separate Celery tasks (`scan_outlet.delay(outlet_id)`)
    so thousands of outlets can be processed in parallel across worker nodes.
  - Use Redis or Kafka as the broker/event bus if outlet count or event
    volume grows beyond what a single Redis instance handles comfortably.
  - Fan out WebSocket broadcast via Redis pub/sub so alerts reach dashboard
    clients connected to *any* API replica, not just the instance that ran
    the scan.
"""
import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.database import AsyncSessionLocal
from app.services.alert_engine import run_full_scan
from app.config import settings
from app.models import Outlet
from sqlalchemy import select

logger = logging.getLogger("shwapno.worker")
scheduler = AsyncIOScheduler()


async def scan_outlet(outlet_id: int):
    async with AsyncSessionLocal() as db:
        try:
            summary = await run_full_scan(db, outlet_id)
            logger.info("Outlet %s scan complete: %s", outlet_id, summary)
        except Exception:
            logger.exception("Scan failed for outlet %s", outlet_id)


async def scan_all_outlets_job():
    """Fan out one scan per outlet concurrently, each with its own DB session."""
    async with AsyncSessionLocal() as db:
        outlet_ids = [row[0] for row in (await db.execute(select(Outlet.id))).all()]
    await asyncio.gather(*(scan_outlet(oid) for oid in outlet_ids))


def start_scheduler():
    scheduler.add_job(
        scan_all_outlets_job,
        "interval",
        seconds=settings.REALTIME_ANALYTICS_INTERVAL_SECONDS,
        id="realtime_analytics_scan",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.start()
    logger.info(
        "Real-time analytics scheduler started (interval=%ss)",
        settings.REALTIME_ANALYTICS_INTERVAL_SECONDS,
    )


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
