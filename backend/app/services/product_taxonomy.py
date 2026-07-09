import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    DeliverySchedule, DemandForecast, InventoryItem, InventoryMovement,
    ProductCategory, PromotionCampaign, SalesRecord, StockOutEvent,
)


SKU_TABLES = (
    SalesRecord,
    InventoryItem,
    InventoryMovement,
    StockOutEvent,
    PromotionCampaign,
    DeliverySchedule,
    DemandForecast,
)


async def ensure_product_categories(db: AsyncSession) -> int:
    existing = {
        row.name: row
        for row in (await db.execute(select(ProductCategory))).scalars().all()
    }
    existing_by_id = {row.id: row for row in existing.values()}

    updated = 0
    for model in SKU_TABLES:
        rows = (await db.execute(select(model))).scalars().all()
        for row in rows:
            category = existing_by_id.get(row.category_id)
            category_name = category.name if category else row.category
            category = category or existing.get(category_name)
            if category_name and category is None:
                category = ProductCategory(
                    name=category_name,
                    slug=re.sub(r"[^a-z0-9]+", "-", category_name.lower()).strip("-"),
                    description=f"Products in the {category_name} category",
                )
                db.add(category)
                await db.flush()
                existing[category_name] = category
                existing_by_id[category.id] = category
            if category and (row.category != category_name or row.category_id != category.id):
                row.category = category_name
                row.category_id = category.id
                updated += 1
    await db.commit()
    return updated
