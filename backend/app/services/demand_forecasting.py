import datetime as dt
import asyncio
import json
import re
from collections import defaultdict

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    CampaignStatus, DeliverySchedule, DeliveryStatus, DemandForecast, ForecastRisk,
    InventoryItem, PromotionCampaign, SalesRecord, SeasonalEvent,
)
from app.services.weather_context import weather_demand_context


MODEL_NAME = f"gemini:{settings.GEMINI_MODEL or 'not-configured'}"
MODEL_VERSION = "2.0"


class GeminiForecastError(RuntimeError):
    pass


def _extract_json(text: str) -> dict | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None


def _risk_level(gap_units: int, days_cover: float | None) -> ForecastRisk:
    if gap_units >= 40 or (days_cover is not None and days_cover <= 1):
        return ForecastRisk.CRITICAL
    if gap_units >= 20 or (days_cover is not None and days_cover <= 3):
        return ForecastRisk.HIGH
    if gap_units > 0 or (days_cover is not None and days_cover <= 7):
        return ForecastRisk.MEDIUM
    return ForecastRisk.LOW


def _weighted_recent_average(records: list[SalesRecord]) -> float:
    recent = sorted(records, key=lambda row: row.date)[-14:]
    if not recent:
        return 0
    weighted_sum = 0.0
    weight_total = 0.0
    for index, row in enumerate(recent, start=1):
        weight = 1 + index / len(recent)
        weighted_sum += row.units_sold * weight
        weight_total += weight
    return weighted_sum / weight_total if weight_total else 0


async def generate_demand_forecast(
    db: AsyncSession,
    outlet_id: int,
    horizon_days: int | None = None,
) -> list[DemandForecast]:
    horizon = horizon_days or settings.WEATHER_FORECAST_DAYS
    today = dt.date.today()
    end_date = today + dt.timedelta(days=horizon - 1)

    inventory = (await db.execute(
        select(InventoryItem).where(InventoryItem.outlet_id == outlet_id)
    )).scalars().all()
    sales = (await db.execute(
        select(SalesRecord).where(SalesRecord.outlet_id == outlet_id)
    )).scalars().all()
    promotions = (await db.execute(
        select(PromotionCampaign).where(
            PromotionCampaign.outlet_id == outlet_id,
            PromotionCampaign.status.in_([CampaignStatus.ACTIVE, CampaignStatus.PLANNED]),
        )
    )).scalars().all()
    seasons = (await db.execute(
        select(SeasonalEvent).where(
            SeasonalEvent.outlet_id == outlet_id,
            SeasonalEvent.end_date >= today,
            SeasonalEvent.start_date <= end_date,
        )
    )).scalars().all()
    deliveries = (await db.execute(
        select(DeliverySchedule).where(
            DeliverySchedule.outlet_id == outlet_id,
            DeliverySchedule.scheduled_date >= today,
            DeliverySchedule.scheduled_date <= end_date,
            DeliverySchedule.status.in_([DeliveryStatus.SCHEDULED, DeliveryStatus.IN_TRANSIT]),
        )
    )).scalars().all()
    weather = await weather_demand_context(today)
    category_inventory: dict[str, dict] = {}
    for item in inventory:
        summary = category_inventory.setdefault(item.category, {
            "category": item.category,
            "category_id": item.category_id,
            "skus": [],
            "on_hand_units": 0,
            "reorder_point": 0,
            "avg_daily_sales": 0.0,
            "next_delivery_dates": [],
        })
        summary["skus"].append(item.sku)
        summary["on_hand_units"] += item.on_hand_units
        summary["reorder_point"] += item.reorder_point
        summary["avg_daily_sales"] += item.avg_daily_sales
        if item.next_delivery_date:
            summary["next_delivery_dates"].append(str(item.next_delivery_date))

    sales_by_category_date: dict[tuple[str, dt.date], dict] = {}
    for row in sales:
        key = (row.category, row.date)
        summary = sales_by_category_date.setdefault(key, {
            "category": row.category,
            "date": row.date,
            "units_sold": 0,
            "revenue_bdt": 0.0,
            "is_festival_period": False,
        })
        summary["units_sold"] += row.units_sold
        summary["revenue_bdt"] += row.revenue
        summary["is_festival_period"] = summary["is_festival_period"] or row.is_festival_period

    inbound_by_category: dict[str, int] = defaultdict(int)
    for row in deliveries:
        inbound_by_category[row.category] += row.quantity

    if not settings.GEMINI_API_KEY or not settings.GEMINI_MODEL:
        raise GeminiForecastError("Gemini API key or model is not configured")
    try:
        from google import genai
    except Exception as exc:
        raise GeminiForecastError("Google Gemini SDK is unavailable") from exc

    context = {
        "outlet_id": outlet_id,
        "forecast_start": today,
        "forecast_end": end_date,
        "horizon_days": horizon,
        "category_inventory": list(category_inventory.values()),
        "sales_history": [
            {
                **row,
                "revenue_bdt": round(row["revenue_bdt"], 2),
            }
            for row in sales_by_category_date.values()
            if row["date"] >= today - dt.timedelta(days=60)
        ],
        "promotions": [
            {
                "sku": row.sku,
                "category": row.category,
                "start_date": row.start_date,
                "end_date": row.end_date,
                "discount_pct": row.discount_pct,
                "expected_uplift_pct": row.expected_uplift_pct,
                "status": row.status.value,
            }
            for row in promotions
        ],
        "seasonal_events": [
            {
                "name": row.name,
                "start_date": row.start_date,
                "end_date": row.end_date,
                "category_focus": row.category_focus,
                "uplift_pct": row.uplift_pct,
            }
            for row in seasons
        ],
        "inbound_deliveries": [
            {
                "category": row.category,
                "quantity": row.quantity,
                "scheduled_date": row.scheduled_date,
                "status": row.status.value,
            }
            for row in deliveries
        ],
        "weather": weather,
    }
    prompt = (
        "You are Gemini acting as a retail demand forecasting model for a Shwapno outlet in Bangladesh. "
        "Use only the supplied outlet JSON. Forecast aggregated daily unit demand for EVERY product category "
        "on EVERY date from forecast_start through forecast_end. Consider category sales trends, weather, "
        "promotions, festivals/seasonality, category inventory, and inbound deliveries. Never invent a category. "
        "Return strict JSON only with this shape: "
        "{\"forecasts\":[{\"category\":\"existing category\",\"daily_units\":[12.5,13,14,12,15,16,14],"
        "\"confidence_pct\":80,\"trend_uplift_pct\":2,"
        "\"weather_uplift_pct\":1,\"promotion_uplift_pct\":0,\"seasonal_uplift_pct\":0,"
        "\"reason\":\"brief evidence-based reason\"}]}. "
        "daily_units must contain exactly horizon_days numbers in chronological order beginning on forecast_start. "
        "All numeric values must be non-negative except trend_uplift_pct, which may be negative. "
        "confidence_pct must be between 0 and 100.\n\n"
        f"Outlet forecasting context JSON: {json.dumps(context, default=str)}"
    )
    try:
        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        response = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=settings.GEMINI_MODEL,
                contents=prompt,
            ),
            # Structured multi-category forecasts are substantially larger than chat
            # replies and need a wider response window.
            timeout=max(settings.GEMINI_TIMEOUT_SECONDS, 180.0),
        )
    except Exception as exc:
        raise GeminiForecastError(f"Gemini request failed: {type(exc).__name__}") from exc

    parsed = _extract_json(response.text or "")
    rows = parsed.get("forecasts") if isinstance(parsed, dict) else None
    if not isinstance(rows, list):
        raise GeminiForecastError("Gemini did not return valid forecast JSON")

    expected_keys = {
        (category, today + dt.timedelta(days=offset))
        for category in category_inventory
        for offset in range(horizon)
    }
    gemini_rows = {}
    for row in rows:
        try:
            category = str(row["category"])
            daily_units = row["daily_units"]
        except (KeyError, TypeError, ValueError):
            continue
        if category not in category_inventory or not isinstance(daily_units, list) or len(daily_units) != horizon:
            continue
        for offset, value in enumerate(daily_units):
            try:
                predicted = max(0.0, float(value))
            except (TypeError, ValueError):
                continue
            key = (category, today + dt.timedelta(days=offset))
            gemini_rows[key] = (row, predicted)
    missing = expected_keys - set(gemini_rows)
    if missing:
        raise GeminiForecastError(
            f"Gemini response omitted {len(missing)} required category/date forecasts"
        )

    await db.execute(delete(DemandForecast).where(
        DemandForecast.outlet_id == outlet_id,
        DemandForecast.forecast_date >= today,
        DemandForecast.forecast_date <= end_date,
    ))

    forecasts = []
    for (category, forecast_date), (gemini_row, predicted) in sorted(gemini_rows.items()):
        category_data = category_inventory[category]
        recent_daily_units = [
            row["units_sold"]
            for row in sorted(sales_by_category_date.values(), key=lambda value: value["date"])
            if row["category"] == category
        ][-14:]
        offset = (forecast_date - today).days
        if recent_daily_units:
            weights = range(1, len(recent_daily_units) + 1)
            baseline = sum(value * weight for value, weight in zip(recent_daily_units, weights)) / sum(weights)
        else:
            baseline = category_data["avg_daily_sales"]
        recommended_stock = int(round(predicted * max(2, min(7, horizon - offset))))
        inbound = inbound_by_category[category]
        gap = max(0, recommended_stock - category_data["on_hand_units"] - inbound)
        projected_daily = predicted or category_data["avg_daily_sales"]
        projected_cover = (
            (category_data["on_hand_units"] + inbound) / projected_daily
            if projected_daily > 0 else None
        )

        forecast = DemandForecast(
            outlet_id=outlet_id,
            sku=f"CATEGORY-{category_data['category_id']}",
            category=category,
            category_id=category_data["category_id"],
            forecast_date=forecast_date,
            predicted_units=round(predicted, 1),
            baseline_units=round(baseline, 1),
            weather_uplift_pct=round(max(0.0, float(gemini_row.get("weather_uplift_pct", 0))), 1),
            promotion_uplift_pct=round(max(0.0, float(gemini_row.get("promotion_uplift_pct", 0))), 1),
            seasonal_uplift_pct=round(max(0.0, float(gemini_row.get("seasonal_uplift_pct", 0))), 1),
            trend_uplift_pct=round(float(gemini_row.get("trend_uplift_pct", 0)), 1),
            recommended_stock_units=recommended_stock,
            current_on_hand_units=category_data["on_hand_units"],
            inbound_units=inbound,
            projected_gap_units=gap,
            risk_level=_risk_level(gap, projected_cover),
            confidence_pct=round(min(100.0, max(0.0, float(gemini_row.get("confidence_pct", 70)))), 1),
            model_name=MODEL_NAME,
            model_version=MODEL_VERSION,
            drivers={
                "history_days": len(recent_daily_units),
                "forecast_level": "category",
                "sku_count": len(category_data["skus"]),
                "weather_summary": weather.get("summary"),
                "generated_by": "gemini",
                "gemini_model": settings.GEMINI_MODEL,
                "reason": str(gemini_row.get("reason", ""))[:500],
            },
        )
        db.add(forecast)
        forecasts.append(forecast)

    await db.commit()
    for forecast in forecasts:
        await db.refresh(forecast)
    return forecasts
