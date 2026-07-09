import datetime as dt
import json
import re
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    Alert, AlertSeverity, Complaint, ComplaintStatus, DeliverySchedule, DeliveryStatus,
    InventoryItem, ManpowerRoster, ManualIssue, ManualIssueStatus, StoreAuditReport,
    Task, TaskSource, TaskStatus,
)
from app.services.business_calendar import business_calendar_context
from app.services.prioritization import ScoringInput, hours_between, score_task


def _source_severity(task: Task) -> int:
    return {
        TaskSource.STOCK: 5,
        TaskSource.MANPOWER: 4,
        TaskSource.COMPLAINT: 4,
        TaskSource.AUDIT: 3,
        TaskSource.PROMOTION: 3,
        TaskSource.MANUAL: 3,
    }.get(task.source, 2)


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None


async def _gemini_rank_tasks(tasks: list[Task], local_rows: list[dict], context: dict) -> dict | None:
    if not settings.GEMINI_API_KEY or not settings.GEMINI_MODEL or not tasks:
        return None

    try:
        from google import genai
    except Exception:
        return None

    task_payload = [
        {
            "id": task.id,
            "title": task.title,
            "description": task.description,
            "source": task.source.value,
            "status": task.status.value,
            "due_at": task.due_at,
            "created_at": task.created_at,
            "local_score": next((row["new_score"] for row in local_rows if row["id"] == task.id), task.priority_score),
        }
        for task in tasks
    ]

    prompt = (
        "You are Gemini ranking an operations task queue for a Shwapno retail outlet manager. "
        "Use only the JSON context. Rank the provided existing task IDs; do not create new tasks. "
        "Return strict JSON only, no markdown, in this shape: "
        "{\"tasks\":[{\"id\":123,\"score\":87.5,\"reason\":\"short operational reason\"}]}. "
        "Scores must be numbers from 0 to 100. Higher score means the task should appear earlier. "
        "Consider stock-out risk, manpower coverage, complaints, alerts, delayed deliveries, audits, "
        "manual issues, current date/time, and festival context.\n\n"
        f"Operational context JSON: {json.dumps(context, default=str)}\n"
        f"Candidate task JSON: {json.dumps(task_payload, default=str)}"
    )

    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    try:
        response = await client.aio.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=prompt,
        )
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {str(exc)[:180]}"}

    parsed = _extract_json(response.text or "")
    if not parsed or not isinstance(parsed.get("tasks"), list):
        return {"error": "Gemini did not return valid ranking JSON"}

    allowed_ids = {task.id for task in tasks}
    ranked = []
    seen = set()
    for row in parsed["tasks"]:
        try:
            task_id = int(row["id"])
            score = float(row["score"])
        except (KeyError, TypeError, ValueError):
            continue
        if task_id not in allowed_ids or task_id in seen:
            continue
        ranked.append({
            "id": task_id,
            "score": round(min(100.0, max(0.0, score)), 1),
            "reason": str(row.get("reason", ""))[:240],
        })
        seen.add(task_id)

    if not ranked:
        return {"error": "Gemini ranking JSON did not contain usable task scores"}
    return {"model": settings.GEMINI_MODEL, "tasks": ranked}


async def reprioritize_tasks(db: AsyncSession, outlet_id: int) -> dict:
    calendar = await business_calendar_context(db, outlet_id)
    today = calendar.local_date
    now = calendar.local_datetime.replace(tzinfo=None)

    tasks = (await db.execute(
        select(Task).where(Task.outlet_id == outlet_id, Task.status == TaskStatus.PENDING)
    )).scalars().all()

    inventory = (await db.execute(
        select(InventoryItem).where(InventoryItem.outlet_id == outlet_id)
    )).scalars().all()
    roster = (await db.execute(
        select(ManpowerRoster).where(ManpowerRoster.outlet_id == outlet_id, ManpowerRoster.date == today)
    )).scalars().all()
    complaints = (await db.execute(
        select(Complaint).where(Complaint.outlet_id == outlet_id, Complaint.status == ComplaintStatus.OPEN)
    )).scalars().all()
    alerts = (await db.execute(
        select(Alert).where(Alert.outlet_id == outlet_id, Alert.acknowledged.is_(False))
    )).scalars().all()
    deliveries = (await db.execute(
        select(DeliverySchedule).where(
            DeliverySchedule.outlet_id == outlet_id,
            DeliverySchedule.scheduled_date >= today,
        )
    )).scalars().all()
    manual_issues = (await db.execute(
        select(ManualIssue).where(
            ManualIssue.outlet_id == outlet_id,
            ManualIssue.status != ManualIssueStatus.RESOLVED,
        )
    )).scalars().all()
    audits = (await db.execute(
        select(StoreAuditReport).where(StoreAuditReport.outlet_id == outlet_id)
        .order_by(StoreAuditReport.audit_date.desc()).limit(1)
    )).scalars().all()

    risky_items = []
    for item in inventory:
        days = item.on_hand_units / item.avg_daily_sales if item.avg_daily_sales > 0 else None
        if item.on_hand_units <= item.reorder_point or (days is not None and days <= 3):
            risky_items.append((item, days))

    min_days_cover = min((days for _, days in risky_items if days is not None), default=None)
    max_stock_revenue = max((item.avg_daily_sales * 3 * 250 for item, _ in risky_items), default=0)
    lowest_coverage = min((r.present_staff / r.required_staff for r in roster if r.required_staff), default=1)
    max_footfall = max((r.peak_hour_footfall_forecast for r in roster), default=0)
    max_complaint_severity = max((c.severity for c in complaints), default=1)
    critical_alert_count = sum(1 for a in alerts if a.severity == AlertSeverity.CRITICAL)
    delayed_delivery_count = sum(1 for d in deliveries if d.status == DeliveryStatus.DELAYED)
    max_manual_severity = max((i.severity for i in manual_issues), default=1)
    latest_audit_score = audits[0].score_pct if audits else 100
    festival_multiplier = 1.2 if calendar.next_festival else 1.0
    signals = {
        "risky_skus": len(risky_items),
        "lowest_manpower_coverage_pct": round(lowest_coverage * 100, 1),
        "open_complaints": len(complaints),
        "critical_alerts": critical_alert_count,
        "delayed_deliveries": delayed_delivery_count,
        "active_festival_in_7_days": bool(calendar.next_festival),
    }
    gemini_context = {
        "business_calendar": calendar.model_dump(),
        "signals": signals,
        "risky_inventory": [
            {
                "sku": item.sku,
                "category": item.category,
                "on_hand_units": item.on_hand_units,
                "reorder_point": item.reorder_point,
                "avg_daily_sales": item.avg_daily_sales,
                "days_of_cover": round(days, 1) if days is not None else None,
                "next_delivery_date": item.next_delivery_date,
            }
            for item, days in risky_items[:8]
        ],
        "roster": [
            {
                "shift": row.shift,
                "required_staff": row.required_staff,
                "present_staff": row.present_staff,
                "coverage_pct": round(100 * row.present_staff / row.required_staff, 1) if row.required_staff else 100,
                "peak_hour_footfall_forecast": row.peak_hour_footfall_forecast,
            }
            for row in roster
        ],
        "open_complaints": [
            {
                "category": row.category,
                "description": row.description,
                "severity": row.severity,
                "created_at": row.created_at,
            }
            for row in complaints[:8]
        ],
        "active_alerts": [
            {
                "type": row.type.value,
                "severity": row.severity.value,
                "message": row.message,
                "created_at": row.created_at,
            }
            for row in alerts[:8]
        ],
        "deliveries": [
            {
                "supplier": row.supplier,
                "sku": row.sku,
                "status": row.status.value,
                "scheduled_date": row.scheduled_date,
                "eta_window": row.eta_window,
                "note": row.note,
            }
            for row in deliveries[:8]
        ],
        "manual_issues": [
            {
                "title": row.title,
                "category": row.category,
                "severity": row.severity,
                "status": row.status.value,
            }
            for row in manual_issues[:8]
        ],
        "latest_audit": {
            "score_pct": latest_audit_score,
            "findings": audits[0].findings if audits else None,
            "corrective_action": audits[0].corrective_action if audits else None,
        },
    }

    updated = []
    for task in tasks:
        severity = _source_severity(task)
        revenue_at_risk = 8000.0
        hours_to_deadline = hours_between(now, task.due_at)

        if task.source == TaskSource.STOCK:
            severity = 5 if (min_days_cover is not None and min_days_cover <= 1) else 4
            revenue_at_risk = max_stock_revenue * festival_multiplier
            if min_days_cover is not None:
                hours_to_deadline = min(hours_to_deadline or 72, max(min_days_cover * 24, 1))
        elif task.source == TaskSource.MANPOWER:
            severity = 5 if lowest_coverage < 0.7 else 4
            revenue_at_risk = max_footfall * 150 * festival_multiplier
            hours_to_deadline = min(hours_to_deadline or 8, 4)
        elif task.source == TaskSource.COMPLAINT:
            severity = max_complaint_severity
            revenue_at_risk = len(complaints) * 5000
            hours_to_deadline = min(hours_to_deadline or 12, 8)
        elif task.source == TaskSource.AUDIT:
            severity = 4 if latest_audit_score < 85 else 3
            revenue_at_risk = 12000 + (5000 if critical_alert_count else 0)
        elif task.source == TaskSource.PROMOTION:
            severity = 4 if calendar.next_festival else 3
            revenue_at_risk = 22000 * festival_multiplier
        elif task.source == TaskSource.MANUAL:
            severity = max_manual_severity
            revenue_at_risk = 10000 + delayed_delivery_count * 5000

        if critical_alert_count:
            severity = min(5, severity + 1)
            revenue_at_risk += critical_alert_count * 8000

        if delayed_delivery_count and task.source in (TaskSource.STOCK, TaskSource.MANUAL):
            revenue_at_risk += delayed_delivery_count * 6000
            hours_to_deadline = min(hours_to_deadline or 24, 6)

        created_hours_ago = max((now - task.created_at).total_seconds() / 3600, 0)
        old_score = task.priority_score
        task.priority_score = score_task(ScoringInput(
            hours_to_deadline=hours_to_deadline,
            revenue_at_risk=revenue_at_risk,
            severity_1_to_5=severity,
            created_hours_ago=created_hours_ago,
        ))
        updated.append({
            "id": task.id,
            "title": task.title,
            "old_score": old_score,
            "new_score": task.priority_score,
            "source": task.source.value,
            "reason": "Local operational scoring fallback.",
        })

    generated_by = "local_rules"
    gemini_ranking = await _gemini_rank_tasks(tasks, updated, gemini_context)
    gemini_error = None
    if gemini_ranking and gemini_ranking.get("tasks"):
        by_id = {task.id: task for task in tasks}
        local_by_id = {row["id"]: row for row in updated}
        for row in gemini_ranking["tasks"]:
            task = by_id.get(row["id"])
            local_row = local_by_id.get(row["id"])
            if not task or not local_row:
                continue
            task.priority_score = row["score"]
            local_row["new_score"] = row["score"]
            local_row["reason"] = row["reason"] or "Gemini-ranked from live operational context."
        generated_by = "gemini"
    elif gemini_ranking:
        gemini_error = gemini_ranking.get("error")

    await db.commit()
    return {
        "outlet_id": outlet_id,
        "updated_count": len(updated),
        "generated_by": generated_by,
        "model": gemini_ranking.get("model") if generated_by == "gemini" else None,
        "gemini_error": gemini_error,
        "prioritized_at": calendar.local_datetime.isoformat(),
        "calendar": calendar.model_dump(),
        "signals": signals,
        "tasks": sorted(updated, key=lambda row: row["new_score"], reverse=True),
    }
