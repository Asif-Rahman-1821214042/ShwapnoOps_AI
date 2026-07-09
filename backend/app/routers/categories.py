from fastapi import APIRouter, Depends
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import InventoryItem, ProductCategory
from app.schemas import ProductCategoryOut

router = APIRouter(prefix="/api/categories", tags=["categories"])


@router.get("", response_model=list[ProductCategoryOut])
async def list_product_categories(
    active_only: bool = True,
    db: AsyncSession = Depends(get_db),
):
    query = select(ProductCategory)
    if active_only:
        query = query.where(ProductCategory.is_active.is_(True))
    return (await db.execute(query.order_by(ProductCategory.name))).scalars().all()


@router.get("/summary")
async def product_category_summary(
    outlet_id: int,
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(
        select(
            ProductCategory.id,
            ProductCategory.name,
            ProductCategory.slug,
            ProductCategory.is_active,
            func.count(func.distinct(InventoryItem.sku)).label("product_count"),
        )
        .outerjoin(
            InventoryItem,
            and_(
                InventoryItem.category_id == ProductCategory.id,
                InventoryItem.outlet_id == outlet_id,
            ),
        )
        .group_by(ProductCategory.id)
        .order_by(ProductCategory.name)
    )).all()
    return [
        {
            "id": row.id,
            "name": row.name,
            "slug": row.slug,
            "is_active": row.is_active,
            "product_count": row.product_count,
        }
        for row in rows
    ]
