"""
Alert Engine
------------
Async, side-effect-producing scans over inventory, manpower and complaint
data. Designed to be invoked either:
  1. Per-request (e.g. after new data is posted), or
  2. On a recurring schedule by the background worker (see workers/background_tasks.py)

Each detection function is independent and safe to run concurrently with
asyncio.gather for large multi-outlet datasets.
"""
import datetime as dt
import asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    InventoryItem, ManpowerRoster, Complaint, ComplaintStatus,
    Alert, AlertType, AlertSeverity, Task, TaskSource, Outlet,
)
from app.services.prioritization import score_task, ScoringInput, hours_between
from app.config import settings
from app.websocket_manager import manager


async def _emit(db: AsyncSession, outlet_id: int, type_: AlertType, severity: AlertSeverity, message: str):
    existing = (await db.execute(
        select(Alert).where(
            Alert.outlet_id == outlet_id,
            Alert.type == type_,
            Alert.message == message,
            Alert.acknowledged.is_(False),
        ).limit(1)
    )).scalar_one_or_none()
    if existing:
        return None

    alert = Alert(outlet_id=outlet_id, type=type_, severity=severity, message=message)
    db.add(alert)
    await db.flush()
    await manager.broadcast(outlet_id, {
        "event": "alert",
        "id": alert.id,
        "type": type_.value,
        "severity": severity.value,
        "message": message,
        "created_at": str(alert.created_at),
    })
    return alert


async def scan_stock_out_risk(db: AsyncSession, outlet_id: int | None = None) -> list[Alert]:
    stmt = select(InventoryItem)
    if outlet_id:
        stmt = stmt.where(InventoryItem.outlet_id == outlet_id)
    items = (await db.execute(stmt)).scalars().all()

    created = []
    now = dt.datetime.utcnow()
    for item in items:
        days_of_cover = (
            item.on_hand_units / item.avg_daily_sales if item.avg_daily_sales > 0 else 999
        )
        if days_of_cover <= settings.STOCK_OUT_RISK_LOOKAHEAD_DAYS or item.on_hand_units <= item.reorder_point:
            severity = AlertSeverity.CRITICAL if days_of_cover <= 1 else AlertSeverity.WARNING
            msg = (
                f"SKU {item.sku} ({item.category}) has ~{days_of_cover:.1f} days of cover "
                f"({item.on_hand_units} units on hand, avg daily sale {item.avg_daily_sales:.1f})."
            )
            alert = await _emit(db, item.outlet_id, AlertType.STOCK_OUT_RISK, severity, msg)
            if not alert:
                continue
            created.append(alert)

            deadline = now + dt.timedelta(days=days_of_cover)
            revenue_at_risk = item.avg_daily_sales * 3 * 250  # rough BDT/unit assumption for demo
            task = Task(
                outlet_id=item.outlet_id,
                title=f"Reorder / expedite delivery: {item.sku}",
                description=msg,
                source=TaskSource.STOCK,
                priority_score=score_task(ScoringInput(
                    hours_to_deadline=hours_between(now, deadline),
                    revenue_at_risk=revenue_at_risk,
                    severity_1_to_5=5 if severity == AlertSeverity.CRITICAL else 3,
                    created_hours_ago=0,
                )),
                due_at=deadline,
            )
            db.add(task)
    await db.commit()
    return created


async def scan_manpower_shortage(db: AsyncSession, outlet_id: int | None = None) -> list[Alert]:
    today = dt.date.today()
    stmt = select(ManpowerRoster).where(ManpowerRoster.date == today)
    if outlet_id:
        stmt = stmt.where(ManpowerRoster.outlet_id == outlet_id)
    rosters = (await db.execute(stmt)).scalars().all()

    created = []
    now = dt.datetime.utcnow()
    for r in rosters:
        coverage = r.present_staff / r.required_staff if r.required_staff else 1.0
        if coverage < settings.MANPOWER_SHORTAGE_THRESHOLD_PCT:
            severity = AlertSeverity.CRITICAL if coverage < 0.5 else AlertSeverity.WARNING
            msg = (
                f"{r.shift.title()} shift understaffed: {r.present_staff}/{r.required_staff} "
                f"present ({coverage*100:.0f}%). Forecast footfall {r.peak_hour_footfall_forecast}."
            )
            alert = await _emit(db, r.outlet_id, AlertType.LOW_MANPOWER, severity, msg)
            if not alert:
                continue
            created.append(alert)

            task = Task(
                outlet_id=r.outlet_id,
                title=f"Arrange backup staff for {r.shift} shift",
                description=msg,
                source=TaskSource.MANPOWER,
                priority_score=score_task(ScoringInput(
                    hours_to_deadline=4,
                    revenue_at_risk=r.peak_hour_footfall_forecast * 150,
                    severity_1_to_5=5 if severity == AlertSeverity.CRITICAL else 3,
                    created_hours_ago=0,
                )),
                due_at=now + dt.timedelta(hours=4),
            )
            db.add(task)
    await db.commit()
    return created


async def scan_complaint_spikes(db: AsyncSession, outlet_id: int | None = None) -> list[Alert]:
    since = dt.datetime.utcnow() - dt.timedelta(hours=24)
    stmt = select(Complaint).where(
        Complaint.created_at >= since, Complaint.status == ComplaintStatus.OPEN
    )
    if outlet_id:
        stmt = stmt.where(Complaint.outlet_id == outlet_id)
    complaints = (await db.execute(stmt)).scalars().all()

    by_outlet: dict[int, list[Complaint]] = {}
    for c in complaints:
        by_outlet.setdefault(c.outlet_id, []).append(c)

    created = []
    now = dt.datetime.utcnow()
    for oid, items in by_outlet.items():
        if len(items) >= 3:
            max_sev = max(c.severity for c in items)
            severity = AlertSeverity.CRITICAL if max_sev >= 4 else AlertSeverity.WARNING
            msg = f"{len(items)} open complaints in last 24h (max severity {max_sev}/5)."
            alert = await _emit(db, oid, AlertType.COMPLAINT_SPIKE, severity, msg)
            if not alert:
                continue
            created.append(alert)

            task = Task(
                outlet_id=oid,
                title="Review and triage recent customer complaints",
                description=msg,
                source=TaskSource.COMPLAINT,
                priority_score=score_task(ScoringInput(
                    hours_to_deadline=8,
                    revenue_at_risk=len(items) * 5000,
                    severity_1_to_5=max_sev,
                    created_hours_ago=0,
                )),
                due_at=now + dt.timedelta(hours=8),
            )
            db.add(task)
    await db.commit()
    return created


async def run_full_scan(db: AsyncSession, outlet_id: int | None = None) -> dict:
    """
    Run all detectors for a given scope and return a summary.
    NOTE: a single AsyncSession is not safe for concurrent coroutines, so detectors
    run sequentially here. For true parallelism across outlets, the background worker
    fans out one session per outlet via asyncio.gather (see workers/background_tasks.py).
    """
    stock = await scan_stock_out_risk(db, outlet_id)
    manpower = await scan_manpower_shortage(db, outlet_id)
    complaints = await scan_complaint_spikes(db, outlet_id)
    return {
        "stock_out_alerts": len(stock),
        "manpower_alerts": len(manpower),
        "complaint_alerts": len(complaints),
        "scanned_at": dt.datetime.utcnow().isoformat(),
    }
