from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False, future=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


async def get_db():
    """FastAPI dependency yielding an async DB session per-request."""
    async with AsyncSessionLocal() as session:
        yield session


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # create_all does not add columns to an existing development database.
        # Add the normalized product-category FK in place so current data can be
        # backfilled at application startup without requiring a destructive reset.
        product_tables = (
            "sales_records",
            "inventory_items",
            "inventory_movements",
            "stock_out_events",
            "promotion_campaigns",
            "delivery_schedules",
            "demand_forecasts",
        )

        def add_category_foreign_keys(sync_conn):
            from sqlalchemy import inspect

            inspector = inspect(sync_conn)
            for table in product_tables:
                columns = {column["name"] for column in inspector.get_columns(table)}
                if "category_id" not in columns:
                    sync_conn.exec_driver_sql(
                        f"ALTER TABLE {table} ADD COLUMN category_id "
                        "INTEGER REFERENCES product_categories(id)"
                    )
                sync_conn.exec_driver_sql(
                    f"CREATE INDEX IF NOT EXISTS ix_{table}_category_id "
                    f"ON {table} (category_id)"
                )

        await conn.run_sync(add_category_foreign_keys)
