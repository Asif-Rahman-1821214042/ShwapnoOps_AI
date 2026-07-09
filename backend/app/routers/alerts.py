from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Alert
from app.schemas import AlertOut
from app.services.alert_engine import run_full_scan

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.get("", response_model=list[AlertOut])
async def list_alerts(outlet_id: int, unacknowledged_only: bool = False, db: AsyncSession = Depends(get_db)):
    stmt = select(Alert).where(Alert.outlet_id == outlet_id)
    if unacknowledged_only:
        stmt = stmt.where(Alert.acknowledged.is_(False))
    stmt = stmt.order_by(Alert.created_at.desc())
    return (await db.execute(stmt)).scalars().all()


@router.post("/{alert_id}/ack", response_model=AlertOut)
async def acknowledge_alert(alert_id: int, db: AsyncSession = Depends(get_db)):
    alert = await db.get(Alert, alert_id)
    alert.acknowledged = True
    await db.commit()
    await db.refresh(alert)
    return alert


@router.post("/scan")
async def trigger_scan(outlet_id: int | None = None, db: AsyncSession = Depends(get_db)):
    """Manually trigger the async analytics scan (also runs automatically on a schedule)."""
    return await run_full_scan(db, outlet_id)
