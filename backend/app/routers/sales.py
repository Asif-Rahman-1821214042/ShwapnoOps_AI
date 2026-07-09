import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import ProductCategory, SalesRecord
from app.schemas import SalesOut
from app.services.analytics import sales_trend

router = APIRouter(prefix="/api/sales", tags=["sales"])


@router.get("", response_model=list[SalesOut])
async def list_sales(outlet_id: int, limit: int = 100, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(SalesRecord).where(SalesRecord.outlet_id == outlet_id)
        .order_by(SalesRecord.date.desc()).limit(limit)
    )).scalars().all()
    return rows


@router.get("/trend")
async def get_sales_trend(outlet_id: int, days: int = Query(14, le=90), db: AsyncSession = Depends(get_db)):
    return await sales_trend(db, outlet_id, days)


@router.get("/by-category")
async def sales_by_category(outlet_id: int, db: AsyncSession = Depends(get_db)):
    """Category revenue and units for today, month-to-date, and year-to-date."""
    today = dt.date.today()
    month_start = today.replace(day=1)
    year_start = today.replace(month=1, day=1)

    rows = (await db.execute(
        select(
            ProductCategory.id.label("category_id"),
            ProductCategory.name.label("category"),
            func.coalesce(func.sum(case(
                (SalesRecord.date == today, SalesRecord.revenue), else_=0.0
            )), 0.0).label("today_revenue"),
            func.coalesce(func.sum(case(
                (SalesRecord.date >= month_start, SalesRecord.revenue), else_=0.0
            )), 0.0).label("month_revenue"),
            func.coalesce(func.sum(case(
                (SalesRecord.date >= year_start, SalesRecord.revenue), else_=0.0
            )), 0.0).label("year_revenue"),
            func.coalesce(func.sum(case(
                (SalesRecord.date == today, SalesRecord.units_sold), else_=0
            )), 0).label("today_units"),
            func.coalesce(func.sum(case(
                (SalesRecord.date >= month_start, SalesRecord.units_sold), else_=0
            )), 0).label("month_units"),
            func.coalesce(func.sum(case(
                (SalesRecord.date >= year_start, SalesRecord.units_sold), else_=0
            )), 0).label("year_units"),
        )
        .outerjoin(
            SalesRecord,
            and_(
                SalesRecord.category_id == ProductCategory.id,
                SalesRecord.outlet_id == outlet_id,
                SalesRecord.date <= today,
            ),
        )
        .group_by(ProductCategory.id)
        .order_by(func.sum(SalesRecord.revenue).desc())
    )).all()
    return [
        {
            "category_id": row.category_id,
            "category": row.category,
            "today_revenue": round(row.today_revenue, 2),
            "month_revenue": round(row.month_revenue, 2),
            "year_revenue": round(row.year_revenue, 2),
            "today_units": row.today_units,
            "month_units": row.month_units,
            "year_units": row.year_units,
        }
        for row in rows
    ]
