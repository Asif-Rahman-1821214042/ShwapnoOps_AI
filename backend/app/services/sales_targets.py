import calendar
import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Outlet, OutletSalesTarget, PosTransaction, SalesRecord


def target_splits(year: int, month: int, monthly_target: float) -> dict:
    days_in_month = calendar.monthrange(year, month)[1]
    first_day = dt.date(year, month, 1)
    last_day = dt.date(year, month, days_in_month)
    weeks_in_month = len({(first_day + dt.timedelta(days=offset)).isocalendar().week for offset in range(days_in_month)})
    return {
        "monthly_target": round(monthly_target, 2),
        "weekly_target": round(monthly_target / weeks_in_month, 2),
        "daily_target": round(monthly_target / days_in_month, 2),
        "weeks_in_month": weeks_in_month,
        "days_in_month": days_in_month,
        "start_date": first_day,
        "end_date": last_day,
    }


async def upsert_monthly_target(
    db: AsyncSession,
    outlet_id: int,
    year: int,
    month: int,
    monthly_target: float,
) -> OutletSalesTarget:
    split = target_splits(year, month, monthly_target)
    existing = (await db.execute(
        select(OutletSalesTarget).where(
            OutletSalesTarget.outlet_id == outlet_id,
            OutletSalesTarget.year == year,
            OutletSalesTarget.month == month,
        )
    )).scalar_one_or_none()
    if existing:
        existing.monthly_target = split["monthly_target"]
        existing.weekly_target = split["weekly_target"]
        existing.daily_target = split["daily_target"]
        existing.weeks_in_month = split["weeks_in_month"]
        existing.days_in_month = split["days_in_month"]
        target = existing
    else:
        target = OutletSalesTarget(
            outlet_id=outlet_id,
            year=year,
            month=month,
            monthly_target=split["monthly_target"],
            weekly_target=split["weekly_target"],
            daily_target=split["daily_target"],
            weeks_in_month=split["weeks_in_month"],
            days_in_month=split["days_in_month"],
        )
        db.add(target)
    await db.commit()
    await db.refresh(target)
    return target


async def target_progress(db: AsyncSession, outlet_id: int, today: dt.date | None = None) -> dict:
    current = today or dt.date.today()
    target = (await db.execute(
        select(OutletSalesTarget).where(
            OutletSalesTarget.outlet_id == outlet_id,
            OutletSalesTarget.year == current.year,
            OutletSalesTarget.month == current.month,
        )
    )).scalar_one_or_none()

    if not target:
        raise LookupError(
            f"No sales target exists for outlet {outlet_id}, "
            f"{current.year}-{current.month:02d}"
        )

    month_start = dt.date(current.year, current.month, 1)
    week_start = current - dt.timedelta(days=current.weekday())
    month_end = dt.date(current.year, current.month, target.days_in_month)
    week_end = week_start + dt.timedelta(days=6)
    target_week_start = max(week_start, month_start)
    target_week_end = min(week_end, month_end)
    daily_target = round(float(target.monthly_target) / target.days_in_month, 2)
    days_in_current_week = (target_week_end - target_week_start).days + 1
    weekly_target = round(daily_target * max(days_in_current_week, 0), 2)

    async def sales_between(start: dt.date, end: dt.date) -> float:
        """Use historical sales records plus today's live completed POS transactions."""
        historical_end = min(end, current - dt.timedelta(days=1))
        historical_sales = 0.0
        if start <= historical_end:
            value = (await db.execute(
                select(func.coalesce(func.sum(SalesRecord.revenue), 0)).where(
                    SalesRecord.outlet_id == outlet_id,
                    SalesRecord.date >= start,
                    SalesRecord.date <= historical_end,
                )
            )).scalar_one()
            historical_sales = float(value or 0)

        if not start <= current <= end:
            return historical_sales

        pos_count, pos_sales = (await db.execute(
            select(
                func.count(PosTransaction.id),
                func.coalesce(func.sum(PosTransaction.total_amount), 0),
            ).where(
                PosTransaction.outlet_id == outlet_id,
                func.date(PosTransaction.transaction_at) == current,
                PosTransaction.order_status == "completed",
            )
        )).one()
        if pos_count:
            return historical_sales + float(pos_sales or 0)

        # Keeps the endpoint useful before an outlet has started posting POS orders.
        fallback_today = (await db.execute(
            select(func.coalesce(func.sum(SalesRecord.revenue), 0)).where(
                SalesRecord.outlet_id == outlet_id,
                SalesRecord.date == current,
            )
        )).scalar_one()
        return historical_sales + float(fallback_today or 0)

    month_sales = await sales_between(month_start, current)
    week_sales = await sales_between(week_start, current)
    today_sales = await sales_between(current, current)

    def pct(value: float, target_value: float) -> float:
        return round((value / target_value) * 100, 1) if target_value else 0

    return {
        "outlet_id": outlet_id,
        "year": target.year,
        "month": target.month,
        "monthly_target": target.monthly_target,
        "weekly_target": weekly_target,
        "daily_target": daily_target,
        "month_sales": round(month_sales, 2),
        "week_sales": round(week_sales, 2),
        "today_sales": round(today_sales, 2),
        "month_achievement_pct": pct(month_sales, target.monthly_target),
        "week_achievement_pct": pct(week_sales, weekly_target),
        "today_achievement_pct": pct(today_sales, daily_target),
        "month_gap": round(max(0, target.monthly_target - month_sales), 2),
        "week_gap": round(max(0, weekly_target - week_sales), 2),
        "today_gap": round(max(0, daily_target - today_sales), 2),
        "days_in_month": target.days_in_month,
        "weeks_in_month": target.weeks_in_month,
    }
