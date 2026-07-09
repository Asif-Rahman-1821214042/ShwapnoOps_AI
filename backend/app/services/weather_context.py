import datetime as dt
from collections import Counter
from zoneinfo import ZoneInfo

import httpx

from app.config import settings


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _business_today() -> dt.date:
    timezone = ZoneInfo(settings.BUSINESS_TIMEZONE)
    return dt.datetime.now(timezone).date()


def _summarize_weather(days: list[dict]) -> dict:
    if not days:
        return {
            "avg_temperature": None,
            "avg_feels_like": None,
            "avg_humidity": None,
            "total_precipitation": 0,
            "max_precipitation_probability": 0,
            "rainy_days": 0,
            "hot_days": 0,
            "high_humidity_days": 0,
            "dominant_conditions": [],
        }

    count = len(days)
    conditions = Counter(str(day.get("conditions") or "Unknown") for day in days)
    return {
        "avg_temperature": round(sum(_as_float(day.get("temperature")) for day in days) / count, 1),
        "avg_feels_like": round(sum(_as_float(day.get("feels_like")) for day in days) / count, 1),
        "avg_humidity": round(sum(_as_float(day.get("humidity")) for day in days) / count, 1),
        "total_precipitation": round(sum(_as_float(day.get("precipitation")) for day in days), 1),
        "max_precipitation_probability": round(max(_as_float(day.get("precipitation_probability")) for day in days), 1),
        "rainy_days": sum(
            1
            for day in days
            if _as_float(day.get("precipitation")) > 1
            or _as_float(day.get("precipitation_probability")) >= 60
            or "rain" in str(day.get("conditions") or "").lower()
        ),
        "hot_days": sum(1 for day in days if _as_float(day.get("feels_like")) >= 32),
        "high_humidity_days": sum(1 for day in days if _as_float(day.get("humidity")) >= 80),
        "dominant_conditions": [name for name, _ in conditions.most_common(3)],
    }


def _compact_day(day: dict) -> dict:
    return {
        "date": day.get("record_date"),
        "district": day.get("district"),
        "temperature": _as_float(day.get("temperature")),
        "max_temperature": _as_float(day.get("max_temperature")),
        "min_temperature": _as_float(day.get("min_temperature")),
        "feels_like": _as_float(day.get("feels_like")),
        "humidity": _as_float(day.get("humidity")),
        "precipitation": _as_float(day.get("precipitation")),
        "precipitation_probability": _as_float(day.get("precipitation_probability")),
        "wind_speed": _as_float(day.get("wind_speed")),
        "cloud_cover": _as_float(day.get("cloud_cover")),
        "conditions": day.get("conditions") or "Unknown",
        "moisture_risk": _as_float(day.get("moisture_risk")),
        "temperature_risk": _as_float(day.get("temperature_risk")),
        "risk_factor": _as_float(day.get("risk_factor")),
    }


async def weather_demand_context(start_date: dt.date | None = None) -> dict:
    forecast_start = start_date or _business_today()
    payload = {
        "district": settings.WEATHER_DISTRICT,
        "start_date": forecast_start.isoformat(),
        "plan_duration": settings.WEATHER_PLAN_DURATION_DAYS,
    }
    base_context = {
        "district": settings.WEATHER_DISTRICT,
        "start_date": forecast_start.isoformat(),
        "forecast_days": settings.WEATHER_FORECAST_DAYS,
        "source_url": settings.WEATHER_API_URL,
        "request_payload": payload,
        "daily": [],
        "summary": _summarize_weather([]),
    }

    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            response = await client.post(settings.WEATHER_API_URL, json=payload)
            response.raise_for_status()
            raw_days = response.json().get("data", [])
    except Exception as exc:
        base_context["error"] = str(exc)
        return base_context

    daily = [_compact_day(day) for day in raw_days[: settings.WEATHER_FORECAST_DAYS]]
    base_context["daily"] = daily
    base_context["summary"] = _summarize_weather(daily)
    return base_context
