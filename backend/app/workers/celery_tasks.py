"""Celery task wrappers around the async alert engine (reference scaffold)."""
import asyncio
from app.workers.celery_app import celery_app
from app.workers.background_tasks import scan_outlet, scan_all_outlets_job


@celery_app.task(name="app.workers.celery_tasks.scan_all_outlets")
def scan_all_outlets():
    asyncio.run(scan_all_outlets_job())


@celery_app.task(name="app.workers.celery_tasks.scan_one_outlet")
def scan_one_outlet(outlet_id: int):
    asyncio.run(scan_outlet(outlet_id))
