from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import (
    DeliverySchedule, InventoryMovement, ManualIssue, PromotionCampaign,
    SeasonalEvent, StockOutEvent, StoreAuditReport,
)
from app.schemas import (
    BusinessCalendarOut,
    DeliveryScheduleOut, InventoryMovementOut, ManualIssueIn, ManualIssueOut,
    PromotionCampaignOut, SeasonalEventOut, StockOutEventOut, StoreAuditReportOut,
)
from app.services.business_calendar import business_calendar_context
from app.services.weather_context import weather_demand_context

router = APIRouter(prefix="/api/operations", tags=["operations"])


@router.get("/inventory-movements", response_model=list[InventoryMovementOut])
async def list_inventory_movements(outlet_id: int, limit: int = 50, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(InventoryMovement).where(InventoryMovement.outlet_id == outlet_id)
        .order_by(InventoryMovement.occurred_at.desc()).limit(limit)
    )).scalars().all()
    return rows


@router.get("/stock-outs", response_model=list[StockOutEventOut])
async def list_stock_outs(outlet_id: int, limit: int = 50, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(StockOutEvent).where(StockOutEvent.outlet_id == outlet_id)
        .order_by(StockOutEvent.started_at.desc()).limit(limit)
    )).scalars().all()
    return rows


@router.get("/promotions", response_model=list[PromotionCampaignOut])
async def list_promotions(outlet_id: int, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(PromotionCampaign).where(PromotionCampaign.outlet_id == outlet_id)
        .order_by(PromotionCampaign.start_date)
    )).scalars().all()
    return rows


@router.get("/deliveries", response_model=list[DeliveryScheduleOut])
async def list_deliveries(outlet_id: int, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(DeliverySchedule).where(DeliverySchedule.outlet_id == outlet_id)
        .order_by(DeliverySchedule.scheduled_date, DeliverySchedule.eta_window)
    )).scalars().all()
    return rows


@router.get("/seasonal-events", response_model=list[SeasonalEventOut])
async def list_seasonal_events(outlet_id: int, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(SeasonalEvent).where(SeasonalEvent.outlet_id == outlet_id)
        .order_by(SeasonalEvent.start_date)
    )).scalars().all()
    return rows


@router.get("/calendar-context", response_model=BusinessCalendarOut)
async def get_calendar_context(outlet_id: int, db: AsyncSession = Depends(get_db)):
    return await business_calendar_context(db, outlet_id)


@router.get("/weather-context")
async def get_weather_context(outlet_id: int, db: AsyncSession = Depends(get_db)):
    calendar = await business_calendar_context(db, outlet_id)
    return await weather_demand_context(calendar.local_date)


@router.get("/audit-reports", response_model=list[StoreAuditReportOut])
async def list_audit_reports(outlet_id: int, limit: int = 20, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(StoreAuditReport).where(StoreAuditReport.outlet_id == outlet_id)
        .order_by(StoreAuditReport.audit_date.desc()).limit(limit)
    )).scalars().all()
    return rows


@router.get("/manual-issues", response_model=list[ManualIssueOut])
async def list_manual_issues(outlet_id: int, limit: int = 50, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(ManualIssue).where(ManualIssue.outlet_id == outlet_id)
        .order_by(ManualIssue.created_at.desc()).limit(limit)
    )).scalars().all()
    return rows


@router.post("/manual-issues", response_model=ManualIssueOut)
async def create_manual_issue(payload: ManualIssueIn, db: AsyncSession = Depends(get_db)):
    issue = ManualIssue(**payload.model_dump())
    db.add(issue)
    await db.commit()
    await db.refresh(issue)
    return issue
