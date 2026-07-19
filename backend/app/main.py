import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import AsyncSessionLocal, init_db
from app.config import settings
from app.workers.background_tasks import start_scheduler, stop_scheduler
from app.routers import categories, outlets, sales, pos, inventory, manpower, complaints, tasks, alerts, chatbot, dashboard, ws, ai_actions, operations, forecasts, targets
from app.services.product_taxonomy import ensure_product_categories
from app.services.pos_demo import ensure_pos_demo_data

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    async with AsyncSessionLocal() as db:
        await ensure_product_categories(db)
        await ensure_pos_demo_data(db)
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(
    title=settings.APP_NAME,
    description="Smart retail operations assistant for outlet managers.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # restrict to known origins in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(outlets.router)
app.include_router(categories.router)
app.include_router(sales.router)
app.include_router(pos.router)
app.include_router(inventory.router)
app.include_router(manpower.router)
app.include_router(complaints.router)
app.include_router(tasks.router)
app.include_router(alerts.router)
app.include_router(operations.router)
app.include_router(forecasts.router)
app.include_router(targets.router)
app.include_router(chatbot.router)
app.include_router(ai_actions.router)
app.include_router(dashboard.router)
app.include_router(ws.router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "app": settings.APP_NAME}
