import datetime as dt
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import SeasonalEvent
from app.schemas import BusinessCalendarOut, CalendarFestivalOut


def local_now() -> dt.datetime:
    return dt.datetime.now(ZoneInfo(settings.BUSINESS_TIMEZONE))


def _calendar_event(event: SeasonalEvent, today: dt.date) -> CalendarFestivalOut:
    return CalendarFestivalOut(
        id=event.id,
        name=event.name,
        start_date=event.start_date,
        end_date=event.end_date,
        category_focus=event.category_focus,
        uplift_pct=event.uplift_pct,
        days_until_start=max((event.start_date - today).days, 0),
        is_active_today=event.start_date <= today <= event.end_date,
    )


async def business_calendar_context(db: AsyncSession, outlet_id: int) -> BusinessCalendarOut:
    now = local_now()
    today = now.date()
    window_end = today + dt.timedelta(days=settings.FESTIVAL_LOOKAHEAD_DAYS)

    events_in_window = (await db.execute(
        select(SeasonalEvent).where(
            SeasonalEvent.outlet_id == outlet_id,
            SeasonalEvent.end_date >= today,
            SeasonalEvent.start_date <= window_end,
        ).order_by(SeasonalEvent.start_date)
    )).scalars().all()

    next_known = (await db.execute(
        select(SeasonalEvent).where(
            SeasonalEvent.outlet_id == outlet_id,
            SeasonalEvent.end_date >= today,
        ).order_by(SeasonalEvent.start_date).limit(1)
    )).scalar_one_or_none()

    festivals = [_calendar_event(event, today) for event in events_in_window]

    return BusinessCalendarOut(
        timezone=settings.BUSINESS_TIMEZONE,
        local_date=today,
        local_time=now.strftime("%H:%M:%S"),
        local_datetime=now,
        lookahead_days=settings.FESTIVAL_LOOKAHEAD_DAYS,
        next_festival=festivals[0] if festivals else None,
        next_known_festival=_calendar_event(next_known, today) if next_known else None,
        festivals_next_7_days=festivals,
    )
