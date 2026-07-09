from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Outlet
from app.schemas import OutletOut

router = APIRouter(prefix="/api/outlets", tags=["outlets"])


@router.get("", response_model=list[OutletOut])
async def list_outlets(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Outlet))).scalars().all()
    return rows


@router.get("/{outlet_id}", response_model=OutletOut)
async def get_outlet(outlet_id: int, db: AsyncSession = Depends(get_db)):
    return await db.get(Outlet, outlet_id)
