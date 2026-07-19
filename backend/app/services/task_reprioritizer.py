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
from app.services.attendance import attendance_summary, predict_peak_context
from app.services.business_calendar import business_calendar_context
from app.services.prioritization import ScoringInput, hours_between, score_task

REASON_TYPES = {
    "stock_out_risk",
    "workload_overload",
    "customer_complaint",
    "delivery_delay",
    "audit_issue",
    "promotion_risk",
    "operational_issue",
}


def _fallback_reason_type(task: Task) -> str:
    return {
        TaskSource.STOCK: "stock_out_risk",
        TaskSource.MANPOWER: "workload_overload",
        TaskSource.COMPLAINT: "customer_complaint",
        TaskSource.AUDIT: "audit_issue",
        TaskSource.PROMOTION: "promotion_risk",
        TaskSource.MANUAL: "operational_issue",
    }.get(task.source, "operational_issue")


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
        "{\"tasks\":[{\"id\":123,\"score\":87.5,\"reason_type\":\"stock_out_risk\","
        "\"reason\":\"Vegetable stock has 0.4 days of cover: 6 units on hand versus 14.9 average daily sales.\"}]}. "
        "reason_type must be exactly one of: stock_out_risk, workload_overload, customer_complaint, "
        "delivery_delay, audit_issue, promotion_risk, operational_issue. "
        "Every reason must state the actual root cause and cite concrete evidence from the JSON, such as "
        "days of cover, units on hand, staffing coverage, footfall, complaint severity, delivery status, "
        "audit score, alert severity, or deadline. Do not write vague reasons and do not invent evidence. "
        "Scores must be numbers from 0 to 100. Higher score means the task should appear earlier. "
        "Consider stock-out risk, manpower coverage, today's compact attendance summary, complaints, "
        "alerts, delayed deliveries, audits, manual issues, current date/time, and festival context. "
        "Attendance context is summarized; do not ask for full employee attendance rows.\n\n"
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
            "reason_type": (
                str(row.get("reason_type", "")).strip().lower()
                if str(row.get("reason_type", "")).strip().lower() in REASON_TYPES
                else "operational_issue"
            ),
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
    today_attendance = await attendance_summary(db, outlet_id, today)
    peak_prediction = await predict_peak_context(db, outlet_id, today)

    risky_items = []
    for item in inventory:
        days = item.on_hand_units / item.avg_daily_sales if item.avg_daily_sales > 0 else None
        if item.on_hand_units <= item.reorder_point or (days is not None and days <= 3):
            risky_items.append((item, days))

    min_days_cover = min((days for _, days in risky_items if days is not None), default=None)
    max_stock_revenue = max((item.avg_daily_sales * 3 * 250 for item, _ in risky_items), default=0)
    lowest_coverage = min((r.present_staff / r.required_staff for r in roster if r.required_staff), default=1)
    max_footfall = max(
        max((r.peak_hour_footfall_forecast for r in roster), default=0),
        int(peak_prediction["predicted_daily_footfall"] * peak_prediction["peak_demand_share"]),
    )
    unavailable_staff = today_attendance["unavailable_staff"]
    late_staff = today_attendance["late"]
    attendance_pct = today_attendance["attendance_pct"] if today_attendance["attendance_pct"] is not None else 100.0
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
        "pending_tasks": len(tasks),
        "overdue_tasks": sum(1 for task in tasks if task.due_at and task.due_at < now),
        "peak_hour_footfall": max_footfall,
        "predicted_peak_window": peak_prediction["peak_window"],
        "predicted_daily_footfall": peak_prediction["predicted_daily_footfall"],
        "attendance_pct": attendance_pct,
        "attendance_unavailable_staff": unavailable_staff,
        "attendance_late_staff": late_staff,
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
        "attendance_summary": {
            "date": today_attendance["date"],
            "total_employees": today_attendance["total_employees"],
            "available_staff": today_attendance["available_staff"],
            "unavailable_staff": today_attendance["unavailable_staff"],
            "present": today_attendance["present"],
            "late": today_attendance["late"],
            "absent": today_attendance["absent"],
            "leave": today_attendance["leave"],
            "half_day": today_attendance["half_day"],
            "attendance_pct": today_attendance["attendance_pct"],
            "exceptions": today_attendance["exceptions"],
        },
        "peak_prediction": {
            "peak_window": peak_prediction["peak_window"],
            "predicted_daily_footfall": peak_prediction["predicted_daily_footfall"],
            "active_promotions": peak_prediction["active_promotions"],
            "method": peak_prediction["method"],
        },
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
        reason_type = _fallback_reason_type(task)
        reason = f"{task.source.value.title()} task ranked from its deadline and current operational impact."

        if task.source == TaskSource.STOCK:
            matched = next(
                (
                    (item, days) for item, days in risky_items
                    if item.sku in task.title or item.sku in task.description
                ),
                None,
            )
            severity = 5 if (min_days_cover is not None and min_days_cover <= 1) else 4
            revenue_at_risk = max_stock_revenue * festival_multiplier
            if min_days_cover is not None:
                hours_to_deadline = min(hours_to_deadline or 72, max(min_days_cover * 24, 1))
            if matched:
                item, days = matched
                reason = (
                    f"{item.sku} has {round(days, 1) if days is not None else 'unknown'} days of cover: "
                    f"{item.on_hand_units} units on hand versus {item.avg_daily_sales} average daily sales."
                )
                delayed = next(
                    (
                        delivery for delivery in deliveries
                        if delivery.sku == item.sku and delivery.status == DeliveryStatus.DELAYED
                    ),
                    None,
                )
                if delayed:
                    reason_type = "delivery_delay"
                    reason += f" Its delivery scheduled for {delayed.scheduled_date} is delayed."
        elif task.source == TaskSource.MANPOWER:
            severity = 5 if lowest_coverage < 0.7 or unavailable_staff >= 2 else 4
            revenue_at_risk = (max_footfall * 150 + unavailable_staff * 2500 + late_staff * 1200) * festival_multiplier
            hours_to_deadline = min(hours_to_deadline or 8, 4)
            reason = (
                f"Workload pressure: lowest staffing coverage is {round(lowest_coverage * 100, 1)}% "
                f"while predicted peak is {peak_prediction['peak_window']} with {max_footfall} forecast customers. "
                f"Today's attendance is {attendance_pct}% with {unavailable_staff} unavailable "
                f"and {late_staff} late."
            )
        elif task.source == TaskSource.COMPLAINT:
            severity = max_complaint_severity
            revenue_at_risk = len(complaints) * 5000
            hours_to_deadline = min(hours_to_deadline or 12, 8)
            reason = (
                f"Customer issue pressure: {len(complaints)} complaints are open and the "
                f"highest severity is {max_complaint_severity}/5."
            )
        elif task.source == TaskSource.AUDIT:
            severity = 4 if latest_audit_score < 85 else 3
            revenue_at_risk = 12000 + (5000 if critical_alert_count else 0)
            reason = (
                f"Audit follow-up: latest audit score is {latest_audit_score}% with "
                f"{critical_alert_count} active critical alerts."
            )
        elif task.source == TaskSource.PROMOTION:
            severity = 4 if calendar.next_festival else 3
            revenue_at_risk = 22000 * festival_multiplier
            reason = (
                "Promotion readiness requires review"
                + (
                    f" before {calendar.next_festival.name}."
                    if calendar.next_festival else
                    "; no festival is scheduled in the next 7 days, so urgency is lower."
                )
            )
        elif task.source == TaskSource.MANUAL:
            severity = max_manual_severity
            revenue_at_risk = 10000 + delayed_delivery_count * 5000
            reason = (
                f"Operational issue: maximum open manual-issue severity is {max_manual_severity}/5 "
                f"and {delayed_delivery_count} deliveries are delayed."
            )

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
            "reason_type": reason_type,
            "reason": reason[:240],
        })

    generated_by = "local_rules"
    for task in tasks:
        local_row = next((row for row in updated if row["id"] == task.id), None)
        if task.prioritized_by != "gemini" or not task.priority_reason:
            task.priority_reason_type = local_row["reason_type"] if local_row else _fallback_reason_type(task)
            task.priority_reason = local_row["reason"] if local_row else "Ranked from current operational signals."
            task.prioritized_by = generated_by
            task.prioritization_model = None
            task.prioritized_at = now

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
            task.priority_reason_type = row["reason_type"]
            task.priority_reason = row["reason"] or "Gemini-ranked from live operational context."
            task.prioritized_by = "gemini"
            task.prioritization_model = gemini_ranking["model"]
            task.prioritized_at = now
            local_row["new_score"] = row["score"]
            local_row["reason"] = task.priority_reason
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
