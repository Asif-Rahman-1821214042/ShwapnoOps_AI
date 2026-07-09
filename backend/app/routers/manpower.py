import datetime as dt
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import ManpowerRoster
from app.schemas import ManpowerOut

router = APIRouter(prefix="/api/manpower", tags=["manpower"])


@router.get("", response_model=list[ManpowerOut])
async def list_roster(outlet_id: int, date: dt.date | None = None, db: AsyncSession = Depends(get_db)):
    target_date = date or dt.date.today()
    rows = (await db.execute(
        select(ManpowerRoster).where(
            ManpowerRoster.outlet_id == outlet_id, ManpowerRoster.date == target_date
        )
    )).scalars().all()

    out = []
    for r in rows:
        item = ManpowerOut.model_validate(r)
        item.coverage_pct = round(100 * r.present_staff / r.required_staff, 1) if r.required_staff else None
        out.append(item)
    return out


@router.get("/optimize")
async def optimize_shifts(outlet_id: int, db: AsyncSession = Depends(get_db)):
    """
    Simple shift-optimization recommendation: rank shifts by
    (footfall forecast per present staff) to flag where reallocating
    staff from an over-covered shift would help most.
    """
    today = dt.date.today()
    rows = (await db.execute(
        select(ManpowerRoster).where(ManpowerRoster.outlet_id == outlet_id, ManpowerRoster.date == today)
    )).scalars().all()

    recs = []
    for r in rows:
        load_per_staff = r.peak_hour_footfall_forecast / r.present_staff if r.present_staff else float("inf")
        recs.append({
            "shift": r.shift,
            "present_staff": r.present_staff,
            "required_staff": r.required_staff,
            "forecast_footfall": r.peak_hour_footfall_forecast,
            "footfall_per_staff": round(load_per_staff, 1),
        })
    recs.sort(key=lambda x: x["footfall_per_staff"], reverse=True)

    recommendation = None
    if len(recs) >= 2 and recs[0]["footfall_per_staff"] > 2 * max(recs[-1]["footfall_per_staff"], 1):
        recommendation = (
            f"Consider moving 1-2 staff from '{recs[-1]['shift']}' shift to "
            f"'{recs[0]['shift']}' shift — footfall load per staff is "
            f"{recs[0]['footfall_per_staff']:.0f} vs {recs[-1]['footfall_per_staff']:.0f}."
        )

    return {"shifts": recs, "recommendation": recommendation}
