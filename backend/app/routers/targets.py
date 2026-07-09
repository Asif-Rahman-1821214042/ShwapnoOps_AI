import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import OutletSalesTarget
from app.schemas import OutletSalesTargetIn, OutletSalesTargetOut, SalesTargetProgressOut
from app.services.sales_targets import target_progress, upsert_monthly_target

router = APIRouter(prefix="/api/targets", tags=["targets"])


@router.get("", response_model=list[OutletSalesTargetOut])
async def list_targets(
    outlet_id: int,
    year: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(OutletSalesTarget).where(OutletSalesTarget.outlet_id == outlet_id)
    if year:
        stmt = stmt.where(OutletSalesTarget.year == year)
    stmt = stmt.order_by(OutletSalesTarget.year.desc(), OutletSalesTarget.month.desc())
    return (await db.execute(stmt)).scalars().all()


@router.post("", response_model=OutletSalesTargetOut)
async def set_target(payload: OutletSalesTargetIn, db: AsyncSession = Depends(get_db)):
    return await upsert_monthly_target(
        db,
        outlet_id=payload.outlet_id,
        year=payload.year,
        month=payload.month,
        monthly_target=payload.monthly_target,
    )


@router.get("/progress", response_model=SalesTargetProgressOut)
async def get_target_progress(outlet_id: int, db: AsyncSession = Depends(get_db)):
    try:
        return await target_progress(db, outlet_id, dt.date.today())
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
