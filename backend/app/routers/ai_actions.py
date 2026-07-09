import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import AiRecommendationAudit, AiRecommendationStatus
from app.schemas import (
    AiActionRequest, AiActionResponse, AiRecommendationAuditOut,
    AiRecommendationDecisionIn, AiRecommendationEscalateIn,
)
from app.services.ai_actions import generate_action_plan

router = APIRouter(prefix="/api/ai", tags=["ai-actions"])


@router.post("/actions", response_model=AiActionResponse)
async def create_action_plan(payload: AiActionRequest, db: AsyncSession = Depends(get_db)):
    return await generate_action_plan(
        db=db,
        outlet_id=payload.outlet_id,
        purpose=payload.purpose,
        instruction=payload.instruction,
    )


@router.get("/recommendations", response_model=list[AiRecommendationAuditOut])
async def list_recommendations(
    outlet_id: int,
    status: AiRecommendationStatus | None = None,
    limit: int = 30,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(AiRecommendationAudit).where(AiRecommendationAudit.outlet_id == outlet_id)
    if status:
        stmt = stmt.where(AiRecommendationAudit.status == status)
    stmt = stmt.order_by(AiRecommendationAudit.created_at.desc()).limit(limit)
    return (await db.execute(stmt)).scalars().all()


@router.post("/recommendations/{recommendation_id}/approve", response_model=AiRecommendationAuditOut)
async def approve_recommendation(
    recommendation_id: int,
    payload: AiRecommendationDecisionIn,
    db: AsyncSession = Depends(get_db),
):
    record = await db.get(AiRecommendationAudit, recommendation_id)
    if not record:
        raise HTTPException(404, "Recommendation not found")
    record.status = AiRecommendationStatus.APPROVED
    record.reviewed_by = payload.reviewed_by
    record.review_note = payload.note
    record.reviewed_at = dt.datetime.utcnow()
    await db.commit()
    await db.refresh(record)
    return record


@router.post("/recommendations/{recommendation_id}/reject", response_model=AiRecommendationAuditOut)
async def reject_recommendation(
    recommendation_id: int,
    payload: AiRecommendationDecisionIn,
    db: AsyncSession = Depends(get_db),
):
    record = await db.get(AiRecommendationAudit, recommendation_id)
    if not record:
        raise HTTPException(404, "Recommendation not found")
    record.status = AiRecommendationStatus.REJECTED
    record.reviewed_by = payload.reviewed_by
    record.review_note = payload.note
    record.reviewed_at = dt.datetime.utcnow()
    await db.commit()
    await db.refresh(record)
    return record


@router.post("/recommendations/{recommendation_id}/escalate", response_model=AiRecommendationAuditOut)
async def escalate_recommendation(
    recommendation_id: int,
    payload: AiRecommendationEscalateIn,
    db: AsyncSession = Depends(get_db),
):
    record = await db.get(AiRecommendationAudit, recommendation_id)
    if not record:
        raise HTTPException(404, "Recommendation not found")
    record.status = AiRecommendationStatus.ESCALATED
    record.escalated_to = payload.escalated_to
    record.escalation_reason = payload.reason
    record.escalated_at = dt.datetime.utcnow()
    await db.commit()
    await db.refresh(record)
    return record
