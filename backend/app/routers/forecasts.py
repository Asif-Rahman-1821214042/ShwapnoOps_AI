from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import DemandForecast
from app.schemas import DemandForecastOut, ForecastRunResponse
from app.services.demand_forecasting import GeminiForecastError, MODEL_NAME, generate_demand_forecast

router = APIRouter(prefix="/api/forecasts", tags=["forecasts"])


@router.get("/demand", response_model=list[DemandForecastOut])
async def list_demand_forecasts(
    outlet_id: int,
    limit: int = 30,
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(
        select(DemandForecast)
        .where(
            DemandForecast.outlet_id == outlet_id,
            DemandForecast.sku.like("CATEGORY-%"),
        )
        .order_by(DemandForecast.risk_level.desc(), DemandForecast.projected_gap_units.desc())
        .limit(limit)
    )).scalars().all()
    return rows


@router.post("/demand/run", response_model=ForecastRunResponse)
async def run_demand_forecast(
    outlet_id: int,
    horizon_days: int = 7,
    db: AsyncSession = Depends(get_db),
):
    try:
        forecasts = await generate_demand_forecast(db, outlet_id, horizon_days)
    except GeminiForecastError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    sorted_forecasts = sorted(
        forecasts,
        key=lambda row: (row.projected_gap_units, row.predicted_units),
        reverse=True,
    )
    return ForecastRunResponse(
        outlet_id=outlet_id,
        horizon_days=horizon_days,
        model_name=MODEL_NAME,
        generated_count=len(forecasts),
        forecasts=sorted_forecasts[:30],
    )
