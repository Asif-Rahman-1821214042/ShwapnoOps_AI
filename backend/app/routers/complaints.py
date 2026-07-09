from fastapi import APIRouter, Depends, BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, AsyncSessionLocal
from app.models import Complaint
from app.schemas import ComplaintIn, ComplaintOut
from app.services.alert_engine import scan_complaint_spikes

router = APIRouter(prefix="/api/complaints", tags=["complaints"])


@router.get("", response_model=list[ComplaintOut])
async def list_complaints(outlet_id: int, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(Complaint).where(Complaint.outlet_id == outlet_id)
        .order_by(Complaint.created_at.desc())
    )).scalars().all()
    return rows


async def _rescan(outlet_id: int):
    async with AsyncSessionLocal() as db:
        await scan_complaint_spikes(db, outlet_id)


@router.post("", response_model=ComplaintOut)
async def create_complaint(payload: ComplaintIn, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    c = Complaint(**payload.model_dump())
    db.add(c)
    await db.commit()
    await db.refresh(c)
    # Re-scan asynchronously so a complaint spike alert/task is raised without blocking this response.
    background_tasks.add_task(_rescan, payload.outlet_id)
    return c
