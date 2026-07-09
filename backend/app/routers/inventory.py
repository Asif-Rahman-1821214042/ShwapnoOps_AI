from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import InventoryItem
from app.schemas import InventoryOut

router = APIRouter(prefix="/api/inventory", tags=["inventory"])


@router.get("", response_model=list[InventoryOut])
async def list_inventory(outlet_id: int, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(InventoryItem).where(InventoryItem.outlet_id == outlet_id)
    )).scalars().all()

    out = []
    for r in rows:
        doc = r.on_hand_units / r.avg_daily_sales if r.avg_daily_sales > 0 else None
        if doc is None:
            risk = "unknown"
        elif doc <= 1:
            risk = "critical"
        elif doc <= 3:
            risk = "high"
        elif doc <= 7:
            risk = "medium"
        else:
            risk = "low"
        item = InventoryOut.model_validate(r)
        item.days_of_cover = round(doc, 1) if doc is not None else None
        item.risk_level = risk
        out.append(item)
    return out
