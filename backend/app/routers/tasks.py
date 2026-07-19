from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Task, TaskStatus, TaskSource
from app.schemas import TaskOut, TaskUpdate
from app.services.prioritization import score_task, ScoringInput, hours_between
from app.services.task_reprioritizer import reprioritize_tasks
import datetime as dt

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("", response_model=list[TaskOut])
async def list_tasks(
    outlet_id: int,
    status: TaskStatus | None = None,
    completed_today: bool = False,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Task).where(Task.outlet_id == outlet_id)
    if status:
        stmt = stmt.where(Task.status == status)
    if completed_today:
        today = dt.date.today()
        start = dt.datetime.combine(today, dt.time.min)
        end = start + dt.timedelta(days=1)
        stmt = stmt.where(
            Task.status == TaskStatus.DONE,
            Task.completed_at >= start,
            Task.completed_at < end,
        )
    stmt = stmt.order_by(Task.priority_score.desc())
    return (await db.execute(stmt)).scalars().all()


@router.post("", response_model=TaskOut)
async def create_manual_task(
    outlet_id: int, title: str, description: str = "", severity: int = 2,
    revenue_at_risk: float = 0, hours_to_deadline: float | None = None,
    db: AsyncSession = Depends(get_db),
):
    now = dt.datetime.utcnow()
    due = now + dt.timedelta(hours=hours_to_deadline) if hours_to_deadline else None
    task = Task(
        outlet_id=outlet_id, title=title, description=description, source=TaskSource.MANUAL,
        priority_score=score_task(ScoringInput(
            hours_to_deadline=hours_to_deadline, revenue_at_risk=revenue_at_risk,
            severity_1_to_5=severity, created_hours_ago=0,
        )),
        due_at=due,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


@router.post("/prioritize")
async def prioritize_task_queue(outlet_id: int, db: AsyncSession = Depends(get_db)):
    return await reprioritize_tasks(db, outlet_id)


@router.patch("/{task_id}", response_model=TaskOut)
async def update_task(task_id: int, payload: TaskUpdate, db: AsyncSession = Depends(get_db)):
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    previous_status = task.status
    task.status = payload.status
    if payload.status == TaskStatus.DONE and previous_status != TaskStatus.DONE:
        task.completed_at = dt.datetime.utcnow()
    elif payload.status != TaskStatus.DONE:
        task.completed_at = None
    await db.commit()
    await db.refresh(task)
    return task
