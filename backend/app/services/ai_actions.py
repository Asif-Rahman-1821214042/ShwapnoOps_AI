import datetime as dt
import asyncio
import json
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    AiRecommendationAudit, AiRecommendationStatus,
    Alert, Complaint, ComplaintStatus, DeliverySchedule, InventoryItem,
    ManpowerRoster, ManualIssue, Outlet, PromotionCampaign, SeasonalEvent,
    StockOutEvent, StoreAuditReport, Task, TaskStatus,
)
from app.schemas import AiActionItem, AiActionResponse
from app.services.analytics import outlet_scorecard
from app.services.business_calendar import business_calendar_context
from app.services.weather_context import weather_demand_context


PURPOSES = {
    "prioritize_tasks",
    "stock_replenishment",
    "manpower_reallocation",
    "complaint_triage",
    "daily_brief",
    "delivery_risk",
    "campaign_readiness",
    "audit_action_plan",
    "festival_preparedness",
    "root_cause_analysis",
    "regional_summary",
    "weather_demand",
}


def _enum_value(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _json_safe(value):
    return json.loads(json.dumps(value, default=str))


async def _build_context(db: AsyncSession, outlet_id: int) -> dict:
    outlet = await db.get(Outlet, outlet_id)
    calendar = await business_calendar_context(db, outlet_id)
    today = calendar.local_date

    tasks = (await db.execute(
        select(Task).where(Task.outlet_id == outlet_id, Task.status == TaskStatus.PENDING)
        .order_by(Task.priority_score.desc()).limit(8)
    )).scalars().all()
    inventory = (await db.execute(
        select(InventoryItem).where(InventoryItem.outlet_id == outlet_id)
    )).scalars().all()
    rosters = (await db.execute(
        select(ManpowerRoster).where(ManpowerRoster.outlet_id == outlet_id, ManpowerRoster.date == today)
    )).scalars().all()
    complaints = (await db.execute(
        select(Complaint).where(Complaint.outlet_id == outlet_id)
        .order_by(Complaint.created_at.desc()).limit(8)
    )).scalars().all()
    alerts = (await db.execute(
        select(Alert).where(Alert.outlet_id == outlet_id, Alert.acknowledged.is_(False))
        .order_by(Alert.created_at.desc()).limit(8)
    )).scalars().all()
    deliveries = (await db.execute(
        select(DeliverySchedule).where(DeliverySchedule.outlet_id == outlet_id)
        .order_by(DeliverySchedule.scheduled_date).limit(8)
    )).scalars().all()
    promotions = (await db.execute(
        select(PromotionCampaign).where(PromotionCampaign.outlet_id == outlet_id)
        .order_by(PromotionCampaign.start_date).limit(8)
    )).scalars().all()
    stock_outs = (await db.execute(
        select(StockOutEvent).where(StockOutEvent.outlet_id == outlet_id)
        .order_by(StockOutEvent.started_at.desc()).limit(8)
    )).scalars().all()
    seasonal_events = (await db.execute(
        select(SeasonalEvent).where(SeasonalEvent.outlet_id == outlet_id)
        .order_by(SeasonalEvent.start_date).limit(8)
    )).scalars().all()
    audit_reports = (await db.execute(
        select(StoreAuditReport).where(StoreAuditReport.outlet_id == outlet_id)
        .order_by(StoreAuditReport.audit_date.desc()).limit(4)
    )).scalars().all()
    manual_issues = (await db.execute(
        select(ManualIssue).where(ManualIssue.outlet_id == outlet_id)
        .order_by(ManualIssue.created_at.desc()).limit(8)
    )).scalars().all()
    all_outlets = (await db.execute(select(Outlet))).scalars().all()

    risky_inventory = []
    inventory_snapshot = []
    for item in inventory:
        days = item.on_hand_units / item.avg_daily_sales if item.avg_daily_sales > 0 else None
        inventory_snapshot.append({
            "sku": item.sku,
            "category": item.category,
            "on_hand_units": item.on_hand_units,
            "reorder_point": item.reorder_point,
            "avg_daily_sales": item.avg_daily_sales,
            "days_of_cover": round(days, 1) if days is not None else None,
            "next_delivery_date": item.next_delivery_date,
        })
        if item.on_hand_units <= item.reorder_point or (days is not None and days <= 3):
            risky_inventory.append({
                "sku": item.sku,
                "category": item.category,
                "on_hand_units": item.on_hand_units,
                "reorder_point": item.reorder_point,
                "avg_daily_sales": item.avg_daily_sales,
                "days_of_cover": round(days, 1) if days is not None else None,
                "next_delivery_date": item.next_delivery_date,
            })

    scorecard = await outlet_scorecard(db, outlet)
    regional_scorecards = []
    for regional_outlet in all_outlets:
        card = await outlet_scorecard(db, regional_outlet)
        regional_scorecards.append({
            "outlet_id": regional_outlet.id,
            "outlet_name": regional_outlet.name,
            "region": regional_outlet.region,
            "productivity_score": card.productivity_score,
            "sales_today": card.sales_today,
            "stock_health_pct": card.stock_health_pct,
            "manpower_coverage_pct": card.manpower_coverage_pct,
            "open_complaints": card.open_complaints,
            "critical_alerts": card.critical_alerts,
        })
    return {
        "outlet": {
            "id": outlet.id,
            "name": outlet.name,
            "code": outlet.code,
            "region": outlet.region,
            "manager_name": outlet.manager_name,
        },
        "scorecard": scorecard.model_dump(),
        "business_calendar": calendar.model_dump(),
        "pending_tasks": [
            {
                "title": task.title,
                "description": task.description,
                "source": _enum_value(task.source),
                "priority_score": task.priority_score,
                "due_at": task.due_at,
            }
            for task in tasks
        ],
        "risky_inventory": risky_inventory[:8],
        "inventory_snapshot": sorted(
            inventory_snapshot,
            key=lambda row: (row["days_of_cover"] is None, row["days_of_cover"] or 999, row["sku"]),
        )[:30],
        "today_roster": [
            {
                "shift": row.shift,
                "required_staff": row.required_staff,
                "present_staff": row.present_staff,
                "coverage_pct": round(100 * row.present_staff / row.required_staff, 1) if row.required_staff else 100,
                "peak_hour_footfall_forecast": row.peak_hour_footfall_forecast,
            }
            for row in rosters
        ],
        "recent_complaints": [
            {
                "category": complaint.category,
                "description": complaint.description,
                "severity": complaint.severity,
                "status": _enum_value(complaint.status),
                "created_at": complaint.created_at,
            }
            for complaint in complaints
        ],
        "active_alerts": [
            {
                "type": _enum_value(alert.type),
                "severity": _enum_value(alert.severity),
                "message": alert.message,
                "created_at": alert.created_at,
            }
            for alert in alerts
        ],
        "delivery_schedule": [
            {
                "supplier": row.supplier,
                "sku": row.sku,
                "category": row.category,
                "quantity": row.quantity,
                "scheduled_date": row.scheduled_date,
                "eta_window": row.eta_window,
                "status": _enum_value(row.status),
                "note": row.note,
            }
            for row in deliveries
        ],
        "promotion_calendar": [
            {
                "name": row.name,
                "category": row.category,
                "sku": row.sku,
                "start_date": row.start_date,
                "end_date": row.end_date,
                "discount_pct": row.discount_pct,
                "expected_uplift_pct": row.expected_uplift_pct,
                "status": _enum_value(row.status),
            }
            for row in promotions
        ],
        "stock_out_history": [
            {
                "sku": row.sku,
                "category": row.category,
                "started_at": row.started_at,
                "resolved_at": row.resolved_at,
                "estimated_lost_sales": row.estimated_lost_sales,
                "root_cause": row.root_cause,
            }
            for row in stock_outs
        ],
        "seasonal_events": [
            {
                "name": row.name,
                "start_date": row.start_date,
                "end_date": row.end_date,
                "category_focus": row.category_focus,
                "uplift_pct": row.uplift_pct,
                "notes": row.notes,
            }
            for row in seasonal_events
        ],
        "audit_reports": [
            {
                "audit_date": row.audit_date,
                "auditor_name": row.auditor_name,
                "score_pct": row.score_pct,
                "findings": row.findings,
                "corrective_action": row.corrective_action,
            }
            for row in audit_reports
        ],
        "manual_issues": [
            {
                "title": row.title,
                "description": row.description,
                "category": row.category,
                "severity": row.severity,
                "status": _enum_value(row.status),
                "reported_by": row.reported_by,
            }
            for row in manual_issues
        ],
        "regional_scorecards": sorted(regional_scorecards, key=lambda row: row["productivity_score"]),
    }


def _fallback_actions(purpose: str, context: dict) -> tuple[str, list[AiActionItem]]:
    tasks = context["pending_tasks"]
    stock = context["risky_inventory"]
    roster = context["today_roster"]
    complaints = [c for c in context["recent_complaints"] if c["status"] == ComplaintStatus.OPEN.value]
    alerts = context["active_alerts"]
    deliveries = context["delivery_schedule"]
    campaigns = context["promotion_calendar"]
    seasons = context["seasonal_events"]
    audits = context["audit_reports"]
    stock_outs = context["stock_out_history"]
    manual_issues = context["manual_issues"]
    weather = context.get("weather_forecast") or {}

    if purpose == "stock_replenishment":
        actions = [
            AiActionItem(
                title=f"Replenish {item['sku']}",
                rationale=f"{item['on_hand_units']} units on hand with {item['days_of_cover']} days cover.",
                urgency="high" if (item["days_of_cover"] or 99) <= 1 else "medium",
                due_in_hours=4 if (item["days_of_cover"] or 99) <= 1 else 12,
                source="inventory",
            )
            for item in stock[:5]
        ]
        return f"{len(actions)} replenishment action(s) need attention.", actions

    if purpose == "manpower_reallocation":
        weak = [s for s in roster if s["coverage_pct"] < 85]
        actions = [
            AiActionItem(
                title=f"Reallocate staff for {shift['shift']} shift",
                rationale=f"Coverage is {shift['coverage_pct']}% with forecast footfall {shift['peak_hour_footfall_forecast']}.",
                urgency="high" if shift["coverage_pct"] < 70 else "medium",
                due_in_hours=2,
                source="manpower",
            )
            for shift in weak
        ]
        return f"{len(actions)} shift coverage action(s) found.", actions

    if purpose == "complaint_triage":
        actions = [
            AiActionItem(
                title=f"Triage {item['category']} complaint",
                rationale=item["description"],
                urgency="high" if item["severity"] >= 4 else "medium",
                due_in_hours=4 if item["severity"] >= 4 else 8,
                source="complaint",
            )
            for item in complaints[:5]
        ]
        return f"{len(actions)} open complaint action(s) need follow-up.", actions

    if purpose == "delivery_risk":
        delayed = [d for d in deliveries if d["status"] == "delayed"]
        actions = [
            AiActionItem(
                title=f"Resolve delayed delivery for {item['sku']}",
                rationale=f"{item['supplier']} delivery is delayed for {item['scheduled_date']}. {item['note']}",
                urgency="high" if any(s["sku"] == item["sku"] for s in stock) else "medium",
                due_in_hours=3,
                source="delivery",
            )
            for item in delayed[:5]
        ]
        return f"{len(actions)} delivery risk action(s) generated.", actions

    if purpose == "campaign_readiness":
        active_or_planned = [c for c in campaigns if c["status"] in ("active", "planned")]
        actions = [
            AiActionItem(
                title=f"Check readiness for {campaign['name']}",
                rationale=f"{campaign['category']} campaign expects {campaign['expected_uplift_pct']}% uplift from {campaign['start_date']} to {campaign['end_date']}.",
                urgency="high" if campaign["status"] == "active" else "medium",
                due_in_hours=4 if campaign["status"] == "active" else 24,
                source="promotion",
            )
            for campaign in active_or_planned[:5]
        ]
        return f"{len(actions)} campaign readiness action(s) generated.", actions

    if purpose == "audit_action_plan":
        latest = audits[0] if audits else None
        actions = []
        if latest:
            actions.append(AiActionItem(
                title=f"Close audit corrective action from {latest['audit_date']}",
                rationale=f"Audit score {latest['score_pct']}%. Finding: {latest['findings']} Action: {latest['corrective_action']}",
                urgency="high" if latest["score_pct"] < 85 else "medium",
                due_in_hours=8,
                source="audit",
            ))
        actions.extend([
            AiActionItem(
                title=f"Resolve manager issue: {issue['title']}",
                rationale=issue["description"],
                urgency="high" if issue["severity"] >= 4 else "medium",
                due_in_hours=6,
                source="manual",
            )
            for issue in manual_issues[:3]
        ])
        return f"{len(actions)} audit and corrective action(s) generated.", actions

    if purpose == "festival_preparedness":
        calendar = context["business_calendar"]
        festival = calendar.get("next_festival") or calendar.get("next_known_festival")
        if not festival:
            return "No upcoming festival found in the calendar.", []
        actions = [
            AiActionItem(
                title=f"Prepare category focus for {festival['name']}",
                rationale=f"Focus categories: {festival['category_focus']}. Expected uplift {festival['uplift_pct']}%.",
                urgency="high" if festival["days_until_start"] <= 7 else "medium",
                due_in_hours=24,
                source="seasonal",
            )
        ]
        actions.extend([
            AiActionItem(
                title=f"Protect stock for {item['sku']}",
                rationale=f"{item['days_of_cover']} days cover during upcoming seasonal demand.",
                urgency="high",
                due_in_hours=6,
                source="inventory",
            )
            for item in stock[:3]
        ])
        return f"Festival preparedness plan generated for {festival['name']}.", actions

    if purpose == "root_cause_analysis":
        actions = [
            AiActionItem(
                title=f"Prevent repeat stock-out: {event['sku']}",
                rationale=f"Root cause: {event['root_cause']}. Estimated lost sales BDT {event['estimated_lost_sales']:.0f}.",
                urgency="high" if event["resolved_at"] is None else "medium",
                due_in_hours=8,
                source="stock_out_history",
            )
            for event in stock_outs[:4]
        ]
        actions.extend([
            AiActionItem(
                title=f"Investigate alert: {alert['type']}",
                rationale=alert["message"],
                urgency="high" if alert["severity"] == "critical" else "medium",
                due_in_hours=4,
                source="alert",
            )
            for alert in alerts[:2]
        ])
        return f"{len(actions)} root-cause prevention action(s) generated.", actions

    if purpose == "regional_summary":
        weakest = context["regional_scorecards"][:4]
        actions = [
            AiActionItem(
                title=f"Review {row['outlet_name']}",
                rationale=f"Score {row['productivity_score']}/100, stock health {row['stock_health_pct']}%, manpower {row['manpower_coverage_pct']}%, complaints {row['open_complaints']}, critical alerts {row['critical_alerts']}.",
                owner="Regional Manager",
                urgency="high" if row["critical_alerts"] or row["productivity_score"] < 70 else "medium",
                due_in_hours=4,
                source="regional",
            )
            for row in weakest
        ]
        return "Regional outlet attention list generated.", actions

    if purpose == "weather_demand":
        weather_summary = weather.get("summary") or {}
        daily_weather = weather.get("daily") or []
        inventory_snapshot = context.get("inventory_snapshot") or []
        rainy_days = int(weather_summary.get("rainy_days") or 0)
        hot_days = int(weather_summary.get("hot_days") or 0)
        high_humidity_days = int(weather_summary.get("high_humidity_days") or 0)
        stock_skus = {row["sku"] for row in stock}
        preferred_categories = []
        if rainy_days >= 3:
            preferred_categories.extend(["Rice", "Oil", "Tea", "Snacks", "Household", "Personal Care"])
        if hot_days >= 2:
            preferred_categories.extend(["Dairy", "Egg", "Fish", "Meat", "Vegetable", "Personal Care"])
        if high_humidity_days >= 4:
            preferred_categories.extend(["Household", "Personal Care", "Snacks"])
        if not preferred_categories:
            preferred_categories.extend(["Rice", "Oil", "Tea", "Fish", "Meat", "Vegetable", "Dairy"])

        category_rank = {category: index for index, category in enumerate(dict.fromkeys(preferred_categories))}
        candidates = [
            item for item in inventory_snapshot
            if item["category"] in category_rank or item["sku"] in stock_skus
        ]
        candidates = sorted(
            candidates,
            key=lambda item: (
                category_rank.get(item["category"], 99),
                item["days_of_cover"] is None,
                item["days_of_cover"] or 999,
                -item["avg_daily_sales"],
            ),
        )[:6]

        if not candidates:
            candidates = stock[:5]

        condition_text = ", ".join(weather_summary.get("dominant_conditions") or ["forecast conditions"])
        actions = [
            AiActionItem(
                title=f"Stock more {item['sku']}",
                rationale=(
                    f"Next {weather.get('forecast_days', 7)} days show {rainy_days} rainy day(s), "
                    f"{high_humidity_days} high-humidity day(s), {hot_days} hot day(s), and {condition_text}. "
                    f"{item['category']} has {item.get('on_hand_units')} units on hand with "
                    f"{item.get('days_of_cover')} days cover."
                ),
                urgency="high" if item.get("sku") in stock_skus or rainy_days >= 4 else "medium",
                due_in_hours=12 if item.get("sku") in stock_skus else 24,
                source="weather_inventory",
            )
            for item in candidates
        ]
        if daily_weather:
            summary = (
                f"Weather demand plan uses {len(daily_weather)} forecast day(s): "
                f"{rainy_days} rainy, {high_humidity_days} humid, {hot_days} hot."
            )
        else:
            summary = "Weather API did not return forecast data; generated from current inventory risk only."
        return summary, actions

    if purpose == "daily_brief":
        actions = [
            AiActionItem(
                title="Review outlet readiness score",
                rationale=f"Productivity is {context['scorecard']['productivity_score']}/100 with {context['scorecard']['critical_alerts']} critical alert(s).",
                urgency="high" if context["scorecard"]["critical_alerts"] else "medium",
                due_in_hours=1,
                source="scorecard",
            )
        ]
        actions.extend([
            AiActionItem(
                title=alert["message"],
                rationale=f"{alert['severity'].title()} {alert['type'].replace('_', ' ')} alert.",
                urgency="high" if alert["severity"] == "critical" else "medium",
                due_in_hours=4,
                source="alert",
            )
            for alert in alerts[:3]
        ])
        return "Daily action brief generated from scorecard and live alerts.", actions

    actions = [
        AiActionItem(
            title=task["title"],
            rationale=task["description"] or f"Priority score {task['priority_score']:.0f}.",
            urgency="high" if task["priority_score"] >= 70 else "medium",
            due_in_hours=4 if task["priority_score"] >= 70 else 12,
            source=task["source"],
        )
        for task in tasks[:5]
    ]
    return f"{len(actions)} prioritized task action(s) generated.", actions


async def _gemini_summary(purpose: str, instruction: str | None, context: dict, actions: list[AiActionItem]) -> str | None:
    if not settings.GEMINI_API_KEY or not settings.GEMINI_MODEL:
        return None

    try:
        from google import genai
    except Exception:
        return None

    prompt = (
        "You are ShwapnoOps AI. Write a concise action summary for a retail outlet manager. "
        "Use only the provided JSON. Do not invent operational facts. Mention the most important next move first.\n\n"
        f"Purpose: {purpose}\n"
        f"Extra instruction: {instruction or 'none'}\n"
        f"Context: {json.dumps(context, default=str)}\n"
        f"Deterministic actions: {json.dumps([a.model_dump() for a in actions], default=str)}"
    )

    try:
        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        response = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=settings.GEMINI_MODEL,
                contents=prompt,
            ),
            timeout=settings.GEMINI_TIMEOUT_SECONDS,
        )
        return response.text.strip() if response.text else None
    except Exception:
        return None


async def generate_action_plan(
    db: AsyncSession,
    outlet_id: int,
    purpose: str,
    instruction: str | None = None,
) -> AiActionResponse:
    normalized_purpose = purpose.strip().lower()
    if normalized_purpose not in PURPOSES:
        normalized_purpose = "prioritize_tasks"

    context = await _build_context(db, outlet_id)
    if normalized_purpose == "weather_demand":
        calendar = context["business_calendar"]
        raw_local_date = calendar["local_date"]
        local_date = raw_local_date if isinstance(raw_local_date, dt.date) else dt.date.fromisoformat(raw_local_date)
        context["weather_forecast"] = await weather_demand_context(local_date)
    fallback_summary, actions = _fallback_actions(normalized_purpose, context)
    gemini_summary = await _gemini_summary(normalized_purpose, instruction, context, actions)

    response_context = {
        "supported_purposes": sorted(PURPOSES),
        "outlet": context["outlet"],
        "scorecard": context["scorecard"],
        "business_calendar": context["business_calendar"],
        "weather_forecast": context.get("weather_forecast"),
    }
    audit = AiRecommendationAudit(
        outlet_id=outlet_id,
        purpose=normalized_purpose,
        generated_by="gemini" if gemini_summary else "local_rules",
        model=settings.GEMINI_MODEL if gemini_summary else None,
        summary=gemini_summary or fallback_summary,
        actions=_json_safe([action.model_dump() for action in actions]),
        context_snapshot=_json_safe(response_context),
        status=AiRecommendationStatus.PENDING_APPROVAL,
    )
    db.add(audit)
    await db.commit()
    await db.refresh(audit)

    return AiActionResponse(
        recommendation_id=audit.id,
        outlet_id=outlet_id,
        purpose=normalized_purpose,
        generated_by="gemini" if gemini_summary else "local_rules",
        model=settings.GEMINI_MODEL if gemini_summary else None,
        approval_status=audit.status,
        summary=gemini_summary or fallback_summary,
        actions=actions,
        context=response_context,
    )
