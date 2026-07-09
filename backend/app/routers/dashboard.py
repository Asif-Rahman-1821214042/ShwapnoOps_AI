import io
import datetime as dt
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Outlet, Task, TaskStatus
from app.schemas import ScorecardOut
from app.services.analytics import outlet_scorecard

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/scorecards", response_model=list[ScorecardOut])
async def scorecards(db: AsyncSession = Depends(get_db)):
    outlets = (await db.execute(select(Outlet))).scalars().all()
    cards = []
    for o in outlets:
        cards.append(await outlet_scorecard(db, o))
    return cards


@router.get("/scorecards/{outlet_id}", response_model=ScorecardOut)
async def scorecard_for_outlet(outlet_id: int, db: AsyncSession = Depends(get_db)):
    outlet = await db.get(Outlet, outlet_id)
    return await outlet_scorecard(db, outlet)


@router.get("/export/tasks.csv")
async def export_tasks_csv(outlet_id: int, db: AsyncSession = Depends(get_db)):
    import csv
    tasks = (await db.execute(
        select(Task).where(Task.outlet_id == outlet_id).order_by(Task.priority_score.desc())
    )).scalars().all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "title", "source", "priority_score", "status", "due_at"])
    for t in tasks:
        writer.writerow([t.id, t.title, t.source.value, t.priority_score, t.status.value, t.due_at])
    buf.seek(0)
    filename = f"outlet_{outlet_id}_tasks_{dt.date.today()}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/export/summary.pdf")
async def export_summary_pdf(outlet_id: int, db: AsyncSession = Depends(get_db)):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    outlet = await db.get(Outlet, outlet_id)
    card = await outlet_scorecard(db, outlet)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    y = height - 60

    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, y, f"ShwapnoOps AI - Daily Summary: {outlet.name}")
    y -= 30
    c.setFont("Helvetica", 11)
    c.drawString(50, y, f"Date: {dt.date.today()}")
    y -= 30

    lines = [
        f"Sales today: BDT {card.sales_today:,.2f}",
        f"Sales this month: BDT {card.sales_current_month:,.2f}",
        f"Sales this year: BDT {card.sales_current_year:,.2f}",
        f"Stock health: {card.stock_health_pct}%",
        f"Manpower coverage: {card.manpower_coverage_pct}%",
        f"Open complaints: {card.open_complaints}",
        f"Critical alerts (unacknowledged): {card.critical_alerts}",
        f"Overall productivity score: {card.productivity_score}/100",
    ]
    for line in lines:
        c.drawString(50, y, line)
        y -= 22

    c.showPage()
    c.save()
    buf.seek(0)
    filename = f"outlet_{outlet_id}_summary_{dt.date.today()}.pdf"
    return StreamingResponse(
        buf, media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
