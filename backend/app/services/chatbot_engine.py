"""
Operational Chatbot Engine
---------------------------
Lightweight intent-classification + data-retrieval chatbot for outlet
managers to ask natural-language operational questions ("what should I
prioritize today?", "which SKUs are about to run out?", "how is my
manpower coverage?").

This module keeps all answers grounded in outlet data. Gemini GenAI is used as
the response composer when GEMINI_API_KEY is configured; otherwise the local
deterministic composer keeps the demo fully runnable without external services.
"""
import datetime as dt
import json
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    Task, TaskStatus, InventoryItem, ManpowerRoster, Complaint,
    ComplaintStatus, Alert, AlertSeverity, Outlet,
)
from app.services.business_calendar import business_calendar_context

INTENT_KEYWORDS = {
    "calendar": ["date", "time", "today", "festival", "eid", "puja", "boishakh", "next 7 days"],
    "top_tasks": ["priorit", "what should i do", "focus", "today's task", "top task"],
    "stock_risk": ["stock", "inventory", "out of stock", "sku", "reorder"],
    "manpower": ["staff", "manpower", "roster", "shift", "understaffed", "attendance"],
    "complaints": ["complaint", "customer issue", "feedback"],
    "alerts": ["alert", "warning", "critical", "risk"],
    "scorecard": ["score", "performance", "kpi", "how am i doing", "productivity"],
}


def classify_intent(message: str) -> str:
    m = message.lower()
    for intent, keywords in INTENT_KEYWORDS.items():
        if any(k in m for k in keywords):
            return intent
    return "general"


def _as_text(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _fallback_reply(intent: str, data: dict) -> str:
    if intent == "calendar":
        calendar = data.get("business_calendar", {})
        next_festival = calendar.get("next_festival")
        base = (
            f"Local business date/time: {calendar.get('local_date')} "
            f"{calendar.get('local_time')} ({calendar.get('timezone')})."
        )
        if next_festival:
            return (
                f"{base}\nNext festival in the next {calendar.get('lookahead_days')} days: "
                f"{next_festival['name']} starts in {next_festival['days_until_start']} day(s), "
                f"focused on {next_festival['category_focus']}."
            )
        next_known = calendar.get("next_known_festival")
        if next_known:
            return (
                f"{base}\nNo festival or seasonal event is scheduled in the next "
                f"{calendar.get('lookahead_days')} days. Next known festival/holiday: "
                f"{next_known['name']} starts in {next_known['days_until_start']} day(s)."
            )
        return f"{base}\nNo festival or seasonal event is scheduled in the next {calendar.get('lookahead_days')} days."

    if intent == "top_tasks":
        tasks = data.get("tasks", [])
        if not tasks:
            return "You're all caught up - no pending tasks right now."
        lines = [f"{i+1}. [{t['priority_score']:.0f}] {t['title']}" for i, t in enumerate(tasks)]
        return "Here are your top priorities right now:\n" + "\n".join(lines)

    if intent == "stock_risk":
        risky = data.get("at_risk_skus", [])
        if not risky:
            return "Stock levels look healthy across all SKUs today."
        lines = [
            f"- {i['sku']}: {i['on_hand_units']} units left, {i['days_of_cover']} days cover"
            for i in risky[:8]
        ]
        return f"{len(risky)} SKU(s) need attention:\n" + "\n".join(lines)

    if intent == "manpower":
        shifts = data.get("shifts", [])
        if not shifts:
            return "No roster data logged for today yet."
        lines = [f"- {s['shift']}: {s['present_staff']}/{s['required_staff']} present" for s in shifts]
        return "Today's manpower coverage:\n" + "\n".join(lines)

    if intent == "complaints":
        return f"You have {data.get('open_complaints', 0)} open customer complaint(s) awaiting resolution."

    if intent == "alerts":
        alerts = data.get("alerts", [])
        if not alerts:
            return "No active alerts. Everything looks under control."
        lines = [f"- [{a['severity'].upper()}] {a['message']}" for a in alerts]
        return "Active alerts:\n" + "\n".join(lines)

    if intent == "scorecard":
        return (
            f"Productivity score: {data.get('productivity_score')}/100. "
            f"Stock health {data.get('stock_health_pct')}%, manpower coverage {data.get('manpower_coverage_pct')}%, "
            f"{data.get('open_complaints')} open complaint(s)."
        )

    return (
        "I can help with today's priorities, stock risk, manpower coverage, complaints, "
        "active alerts, or your outlet's performance scorecard."
    )


async def _gemini_reply(message: str, intent: str, data: dict) -> str | None:
    if not settings.GEMINI_API_KEY or not settings.GEMINI_MODEL:
        return None

    try:
        from google import genai
    except Exception:
        return None

    prompt = (
        "You are ShwapnoOps AI, a concise retail operations copilot for Shwapno outlet managers in Bangladesh. "
        "Use only the JSON context below. Do not invent SKUs, alerts, staff counts, or financial values. "
        "Return 2-5 practical sentences or a short numbered list. Keep currency as BDT when relevant.\n\n"
        f"User question: {message}\n"
        f"Classified intent: {intent}\n"
        f"Outlet context JSON: {json.dumps(data, default=str)}"
    )

    try:
        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        response = await client.aio.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=prompt,
        )
        return response.text.strip() if response.text else None
    except Exception:
        return None


async def handle_message(db: AsyncSession, outlet_id: int, message: str) -> tuple[str, str, dict]:
    intent = classify_intent(message)
    calendar = (await business_calendar_context(db, outlet_id)).model_dump()

    if intent == "calendar":
        data = {"business_calendar": calendar}
        reply = await _gemini_reply(message, intent, data) or _fallback_reply(intent, data)
        return reply, intent, data

    if intent == "top_tasks":
        tasks = (await db.execute(
            select(Task).where(Task.outlet_id == outlet_id, Task.status == TaskStatus.PENDING)
            .order_by(Task.priority_score.desc()).limit(5)
        )).scalars().all()
        data = {"business_calendar": calendar, "tasks": [
            {
                "title": t.title,
                "description": t.description,
                "source": _as_text(t.source),
                "priority_score": t.priority_score,
                "due_at": t.due_at,
            }
            for t in tasks
        ]}
        reply = await _gemini_reply(message, intent, data) or _fallback_reply(intent, data)
        return reply, intent, data

    if intent == "stock_risk":
        items = (await db.execute(
            select(InventoryItem).where(InventoryItem.outlet_id == outlet_id)
        )).scalars().all()
        risky = []
        for i in items:
            days = i.on_hand_units / i.avg_daily_sales if i.avg_daily_sales > 0 else None
            if i.on_hand_units <= i.reorder_point or (days is not None and days <= 3):
                risky.append({
                    "sku": i.sku,
                    "category": i.category,
                    "on_hand_units": i.on_hand_units,
                    "reorder_point": i.reorder_point,
                    "avg_daily_sales": i.avg_daily_sales,
                    "days_of_cover": round(days, 1) if days is not None else None,
                    "next_delivery_date": i.next_delivery_date,
                })
        data = {"business_calendar": calendar, "at_risk_skus": risky}
        reply = await _gemini_reply(message, intent, data) or _fallback_reply(intent, data)
        return reply, intent, data

    if intent == "manpower":
        today = calendar["local_date"]
        rosters = (await db.execute(
            select(ManpowerRoster).where(ManpowerRoster.outlet_id == outlet_id, ManpowerRoster.date == today)
        )).scalars().all()
        data = {"business_calendar": calendar, "shifts": [
            {
                "shift": r.shift,
                "required_staff": r.required_staff,
                "present_staff": r.present_staff,
                "coverage_pct": round(100 * r.present_staff / r.required_staff, 1) if r.required_staff else 100,
                "peak_hour_footfall_forecast": r.peak_hour_footfall_forecast,
            }
            for r in rosters
        ]}
        reply = await _gemini_reply(message, intent, data) or _fallback_reply(intent, data)
        return reply, intent, data

    if intent == "complaints":
        count = (await db.execute(
            select(func.count(Complaint.id)).where(
                Complaint.outlet_id == outlet_id, Complaint.status == ComplaintStatus.OPEN
            )
        )).scalar_one()
        data = {"business_calendar": calendar, "open_complaints": count}
        reply = await _gemini_reply(message, intent, data) or _fallback_reply(intent, data)
        return reply, intent, data

    if intent == "alerts":
        alerts = (await db.execute(
            select(Alert).where(Alert.outlet_id == outlet_id, Alert.acknowledged.is_(False))
            .order_by(Alert.created_at.desc()).limit(5)
        )).scalars().all()
        data = {"business_calendar": calendar, "alerts": [
            {"severity": _as_text(a.severity), "type": _as_text(a.type), "message": a.message, "created_at": a.created_at}
            for a in alerts
        ]}
        reply = await _gemini_reply(message, intent, data) or _fallback_reply(intent, data)
        return reply, intent, data

    if intent == "scorecard":
        from app.services.analytics import outlet_scorecard
        outlet = await db.get(Outlet, outlet_id)
        card = await outlet_scorecard(db, outlet)
        data = card.model_dump()
        data["business_calendar"] = calendar
        reply = await _gemini_reply(message, intent, data) or _fallback_reply(intent, data)
        return reply, intent, data

    data = {"business_calendar": calendar, "supported_topics": list(INTENT_KEYWORDS.keys())}
    reply = await _gemini_reply(message, intent, data) or _fallback_reply(intent, data)
    return reply, intent, data
