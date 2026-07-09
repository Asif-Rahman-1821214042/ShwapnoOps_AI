import datetime as dt
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Outlet, SalesRecord, InventoryItem, ManpowerRoster, Complaint,
    ComplaintStatus, Alert, AlertSeverity,
)
from app.schemas import ScorecardOut


async def outlet_scorecard(db: AsyncSession, outlet: Outlet) -> ScorecardOut:
    today = dt.date.today()
    month_start = today.replace(day=1)
    year_start = today.replace(month=1, day=1)

    sales_today = (await db.execute(
        select(func.coalesce(func.sum(SalesRecord.revenue), 0.0))
        .where(SalesRecord.outlet_id == outlet.id, SalesRecord.date == today)
    )).scalar_one()

    sales_current_month = (await db.execute(
        select(func.coalesce(func.sum(SalesRecord.revenue), 0.0))
        .where(
            SalesRecord.outlet_id == outlet.id,
            SalesRecord.date >= month_start,
            SalesRecord.date <= today,
        )
    )).scalar_one()

    sales_current_year = (await db.execute(
        select(func.coalesce(func.sum(SalesRecord.revenue), 0.0))
        .where(
            SalesRecord.outlet_id == outlet.id,
            SalesRecord.date >= year_start,
            SalesRecord.date <= today,
        )
    )).scalar_one()

    inv_items = (await db.execute(
        select(InventoryItem).where(InventoryItem.outlet_id == outlet.id)
    )).scalars().all()
    if inv_items:
        healthy = sum(1 for i in inv_items if i.on_hand_units > i.reorder_point)
        stock_health_pct = round(100 * healthy / len(inv_items), 1)
    else:
        stock_health_pct = 100.0

    rosters = (await db.execute(
        select(ManpowerRoster).where(ManpowerRoster.outlet_id == outlet.id, ManpowerRoster.date == today)
    )).scalars().all()
    if rosters:
        req = sum(r.required_staff for r in rosters)
        present = sum(r.present_staff for r in rosters)
        manpower_coverage_pct = round(100 * present / req, 1) if req else 100.0
    else:
        manpower_coverage_pct = 100.0

    open_complaints = (await db.execute(
        select(func.count(Complaint.id)).where(
            Complaint.outlet_id == outlet.id, Complaint.status == ComplaintStatus.OPEN
        )
    )).scalar_one()

    critical_alerts = (await db.execute(
        select(func.count(Alert.id)).where(
            Alert.outlet_id == outlet.id,
            Alert.severity == AlertSeverity.CRITICAL,
            Alert.acknowledged.is_(False),
        )
    )).scalar_one()

    productivity_score = round(
        0.35 * stock_health_pct + 0.35 * manpower_coverage_pct
        + 0.20 * max(0, 100 - open_complaints * 10)
        + 0.10 * max(0, 100 - critical_alerts * 20),
        1,
    )

    return ScorecardOut(
        outlet_id=outlet.id,
        outlet_name=outlet.name,
        sales_today=round(sales_today, 2),
        sales_current_month=round(sales_current_month, 2),
        sales_current_year=round(sales_current_year, 2),
        stock_health_pct=stock_health_pct,
        manpower_coverage_pct=manpower_coverage_pct,
        open_complaints=open_complaints,
        critical_alerts=critical_alerts,
        productivity_score=productivity_score,
    )


async def sales_trend(db: AsyncSession, outlet_id: int, days: int = 14):
    since = dt.date.today() - dt.timedelta(days=days)
    rows = (await db.execute(
        select(SalesRecord.date, func.sum(SalesRecord.revenue), func.sum(SalesRecord.units_sold),
               func.sum(SalesRecord.footfall))
        .where(SalesRecord.outlet_id == outlet_id, SalesRecord.date >= since)
        .group_by(SalesRecord.date)
        .order_by(SalesRecord.date)
    )).all()
    return [
        {"date": str(d), "revenue": rev or 0, "units_sold": units or 0, "footfall": foot or 0}
        for d, rev, units, foot in rows
    ]
