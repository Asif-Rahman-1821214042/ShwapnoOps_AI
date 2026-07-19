import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AttendanceStatus, Employee, EmployeeAttendance, PromotionCampaign, SalesRecord


def attendance_status_value(status) -> str:
    return status.value if hasattr(status, "value") else str(status).lower()


async def attendance_summary(
    db: AsyncSession,
    outlet_id: int,
    attendance_date: dt.date,
    exception_limit: int = 5,
) -> dict:
    rows = (await db.execute(
        select(EmployeeAttendance, Employee).join(Employee).where(
            Employee.outlet_id == outlet_id,
            EmployeeAttendance.attendance_date == attendance_date,
        ).order_by(Employee.name)
    )).all()

    counts = {status.value: 0 for status in AttendanceStatus}
    exceptions = []
    total_working_hours = 0.0
    total = len(rows)
    for attendance, employee in rows:
        status = attendance_status_value(attendance.status)
        counts[status] = counts.get(status, 0) + 1
        total_working_hours += attendance.working_hours or 0.0
        if status != AttendanceStatus.PRESENT.value and len(exceptions) < exception_limit:
            exceptions.append({
                "employee_code": employee.employee_code,
                "name": employee.name,
                "designation": employee.designation,
                "status": status,
                "remarks": attendance.remarks,
            })

    available = counts.get(AttendanceStatus.PRESENT.value, 0) + counts.get(AttendanceStatus.LATE.value, 0)
    unavailable = (
        counts.get(AttendanceStatus.ABSENT.value, 0)
        + counts.get(AttendanceStatus.LEAVE.value, 0)
        + counts.get(AttendanceStatus.HALF_DAY.value, 0)
    )
    return {
        "date": attendance_date,
        "total_employees": total,
        "available_staff": available,
        "unavailable_staff": unavailable,
        "present": counts.get(AttendanceStatus.PRESENT.value, 0),
        "late": counts.get(AttendanceStatus.LATE.value, 0),
        "absent": counts.get(AttendanceStatus.ABSENT.value, 0),
        "leave": counts.get(AttendanceStatus.LEAVE.value, 0),
        "half_day": counts.get(AttendanceStatus.HALF_DAY.value, 0),
        "attendance_pct": round(100 * available / total, 1) if total else None,
        "total_working_hours": round(total_working_hours, 2),
        "exceptions": exceptions,
    }


async def predict_peak_context(db: AsyncSession, outlet_id: int, target_date: dt.date) -> dict:
    since = target_date - dt.timedelta(days=28)
    sales_rows = (await db.execute(
        select(
            SalesRecord.date,
            func.coalesce(func.max(SalesRecord.footfall), 0),
        )
        .where(SalesRecord.outlet_id == outlet_id, SalesRecord.date >= since)
        .group_by(SalesRecord.date)
        .order_by(SalesRecord.date)
    )).all()
    recent_footfalls = [int(footfall or 0) for _, footfall in sales_rows if footfall]
    same_weekday = [
        int(footfall or 0)
        for day, footfall in sales_rows
        if day.weekday() == target_date.weekday() and footfall
    ]
    recent_avg = sum(recent_footfalls[-14:]) / max(len(recent_footfalls[-14:]), 1) if recent_footfalls else 0
    weekday_avg = sum(same_weekday) / max(len(same_weekday), 1) if same_weekday else recent_avg

    active_promotions = (await db.execute(
        select(func.count(PromotionCampaign.id)).where(
            PromotionCampaign.outlet_id == outlet_id,
            PromotionCampaign.start_date <= target_date,
            PromotionCampaign.end_date >= target_date,
        )
    )).scalar_one()

    weekend = target_date.weekday() in (4, 5)
    promo_multiplier = 1 + min(active_promotions, 3) * 0.08
    weekend_multiplier = 1.12 if weekend else 1.0
    predicted_daily_footfall = int(round((0.65 * weekday_avg + 0.35 * recent_avg) * promo_multiplier * weekend_multiplier))

    if weekend:
        peak_window = "17:00-20:00"
        peak_demand_share = 0.36
    elif active_promotions:
        peak_window = "16:00-19:00"
        peak_demand_share = 0.38
    else:
        peak_window = "18:00-21:00"
        peak_demand_share = 0.34

    return {
        "date": target_date,
        "peak_window": peak_window,
        "peak_demand_share": peak_demand_share,
        "predicted_daily_footfall": predicted_daily_footfall,
        "recent_avg_footfall": round(recent_avg, 1),
        "weekday_avg_footfall": round(weekday_avg, 1),
        "active_promotions": active_promotions,
        "method": "recent max daily footfall + same weekday trend + promotion/weekend uplift",
    }
