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
import asyncio
import datetime as dt
import hashlib
import json
from sqlalchemy import case, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    Task, TaskStatus, InventoryItem, ManpowerRoster, Complaint,
    ComplaintStatus, Alert, AlertSeverity, Outlet, PaymentMethod,
    PosPayment, PosTransaction,
)
from app.services.attendance import attendance_summary, predict_peak_context
from app.services.business_calendar import business_calendar_context

INTENT_KEYWORDS = {
    "attendance": ["attendance", "present", "absent", "late", "leave", "half day", "check-in", "check out", "check-out"],
    "top_tasks": ["priorit", "what should i do", "focus", "today's task", "top task"],
    "stock_risk": ["stock", "inventory", "out of stock", "sku", "reorder"],
    "manpower": ["staff", "manpower", "roster", "understaffed", "peak hour", "peak", "organize manpower"],
    "complaints": ["complaint", "customer issue", "feedback"],
    "alerts": ["alert", "warning", "critical", "risk"],
    "scorecard": ["score", "performance", "kpi", "how am i doing", "productivity"],
    "calendar": ["date", "time", "today", "festival", "eid", "puja", "boishakh", "next 7 days"],
    "payments": [
        "pos", "card", "credit", "debit", "terminal", "e-payment", "e payment", "epayment",
        "digital payment", "bkash", "nagad", "rocket", "bank transfer", "cash payment",
        "payment method", "payment completion", "unpaid", "partial payment", "transaction",
    ],
}

CHAT_MODEL_CACHE_TTL_SECONDS = 300
_chat_model_cache: dict[str, tuple[dt.datetime, str]] = {}


def classify_intent(message: str) -> str:
    m = message.lower()
    if any(keyword in m for keyword in INTENT_KEYWORDS["payments"]):
        return "payments"
    for intent, keywords in INTENT_KEYWORDS.items():
        if any(k in m for k in keywords):
            return intent
    return "general"


def _as_text(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _model_cache_key(message: str, intent: str, data: dict) -> str:
    """Exclude clock-only fields so unchanged questions can reuse a recent answer."""
    payload = json.loads(json.dumps(data, default=str))
    calendar = payload.get("business_calendar")
    if isinstance(calendar, dict):
        calendar.pop("local_time", None)
        calendar.pop("local_datetime", None)
    source = json.dumps(
        {"message": message.strip().lower(), "intent": intent, "data": payload},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


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
        peak = data.get("peak_prediction") or {}
        staffing = data.get("staffing") or {}
        if not staffing:
            return "No employee attendance is logged for today yet."
        lines = [
            f"Predicted peak window: {peak.get('peak_window', 'unknown')} "
            f"({peak.get('predicted_daily_footfall', 0)} daily footfall forecast)."
        ]
        lines.append(
            f"Available today: {staffing.get('available_staff', 0)}. "
            f"Recommended on customer-facing work during peak: {staffing.get('recommended_on_floor_staff', 0)}."
        )
        if data.get("recommendation"):
            lines.append(data["recommendation"])
        return "Today's manpower plan:\n" + "\n".join(lines)

    if intent == "attendance":
        summary = data.get("attendance_summary", {})
        if not summary or not summary.get("total_employees"):
            return "No employee attendance is logged for today yet."
        lines = [
            f"Today's attendance: {summary.get('available_staff')}/{summary.get('total_employees')} available ({summary.get('attendance_pct')}%).",
            (
                f"Present {summary.get('present')}, late {summary.get('late')}, "
                f"absent {summary.get('absent')}, leave {summary.get('leave')}, "
                f"half day {summary.get('half_day')}."
            ),
        ]
        exceptions = summary.get("exceptions") or []
        if exceptions:
            lines.append(
                "Exceptions: "
                + "; ".join(
                    f"{row['name']} ({row['status']})"
                    for row in exceptions[:5]
                )
            )
        return "\n".join(lines)

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

    if intent == "payments":
        payments = data.get("payments", {})
        today = payments.get("today", {})
        categories = payments.get("categories", {})
        if not today.get("orders"):
            return "No completed payment transactions have been recorded for this outlet today."

        lines = [
            f"Today: {today['orders']} completed order(s), BDT {today['sales']:,.2f} sales, "
            f"and {today['payment_completion_pct']}% payment completion.",
        ]
        for category, label in (
            ("pos_transaction", "Card POS"),
            ("e_payment", "E-payment"),
            ("normal_transaction", "Cash"),
        ):
            entry = categories.get(category)
            if not entry:
                continue
            methods = ", ".join(
                f"{method['name']} BDT {method['amount']:,.2f}"
                for method in entry["methods"]
            )
            lines.append(
                f"{label}: {entry['orders']} payment(s), BDT {entry['amount']:,.2f}"
                + (f" ({methods})." if methods else ".")
            )
        if today.get("unpaid_or_partial_orders"):
            lines.append(f"{today['unpaid_or_partial_orders']} order(s) still need payment follow-up.")
        return "\n".join(lines)

    return (
        "I can help with today's priorities, stock risk, manpower coverage, complaints, "
        "active alerts, payment operations, or your outlet's performance scorecard."
    )


async def _gemini_reply(message: str, intent: str, data: dict) -> str | None:
    if not settings.GEMINI_API_KEY or not settings.GEMINI_MODEL:
        return None

    try:
        from google import genai
    except Exception:
        return None

    cache_key = _model_cache_key(message, intent, data)
    now = dt.datetime.utcnow()
    cached = _chat_model_cache.get(cache_key)
    if cached and (now - cached[0]).total_seconds() < CHAT_MODEL_CACHE_TTL_SECONDS:
        return cached[1]

    prompt = (
        "You are ShwapnoOps AI, a concise retail operations copilot for Shwapno outlet managers in Bangladesh. "
        "Use only the JSON context below. Do not invent SKUs, alerts, staff counts, or financial values. "
        "Answer the user's direct question in no more than 120 words. Do not repeat every metric unless asked. "
        "Keep currency as BDT when relevant.\n\n"
        f"User question: {message}\n"
        f"Classified intent: {intent}\n"
        f"Outlet context JSON: {json.dumps(data, default=str)}"
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
        reply = response.text.strip() if response.text else None
        if reply:
            _chat_model_cache[cache_key] = (now, reply)
        return reply
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
        attendance = await attendance_summary(db, outlet_id, today)
        peak = await predict_peak_context(db, outlet_id, today)
        predicted_daily_footfall = peak["predicted_daily_footfall"] or max((r.peak_hour_footfall_forecast for r in rosters), default=650)
        predicted_peak_footfall = int(round(predicted_daily_footfall * peak["peak_demand_share"]))
        capacity_per_staff = 65
        recommended_staff = max(1, -(-predicted_peak_footfall // capacity_per_staff))
        available_staff = attendance["available_staff"]
        staff_gap = max(0, recommended_staff - available_staff)
        staffing = {
            "available_staff": available_staff,
            "recommended_on_floor_staff": recommended_staff,
            "staff_gap": staff_gap,
            "predicted_peak_footfall": predicted_peak_footfall,
            "footfall_per_available_staff": round(predicted_peak_footfall / available_staff, 1) if available_staff else None,
        }
        recommendation = (
            f"Arrange {staff_gap} additional available or cross-trained employee(s) for the peak window."
            if staff_gap else
            f"Keep at least {recommended_staff} available employees on customer-facing work during the peak window."
        )
        data = {"business_calendar": calendar, "staffing": staffing, "peak_prediction": {
            "peak_window": peak["peak_window"],
            "predicted_daily_footfall": predicted_daily_footfall,
            "active_promotions": peak["active_promotions"],
        }, "attendance_summary": attendance, "recommendation": recommendation}
        reply = await _gemini_reply(message, intent, data) or _fallback_reply(intent, data)
        return reply, intent, data

    if intent == "attendance":
        today = calendar["local_date"]
        summary = await attendance_summary(db, outlet_id, today)
        data = {"business_calendar": calendar, "attendance_summary": summary}
        return _fallback_reply(intent, data), intent, data

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

    if intent == "payments":
        today = calendar["local_date"]
        payment_rows = (await db.execute(
            select(
                PosPayment.transaction_category,
                PaymentMethod.name,
                func.count(PosPayment.id),
                func.coalesce(func.sum(PosPayment.amount), 0.0),
            ).select_from(PosPayment).join(PosTransaction).join(PaymentMethod).where(
                PosTransaction.outlet_id == outlet_id,
                func.date(PosTransaction.transaction_at) == today,
                PosTransaction.order_status == "completed",
                PosPayment.status.in_(["paid", "partial"]),
            ).group_by(PosPayment.transaction_category, PaymentMethod.name)
            .order_by(PosPayment.transaction_category, PaymentMethod.name)
        )).all()
        order_count, sales, paid, payment_follow_up = (await db.execute(
            select(
                func.count(PosTransaction.id),
                func.coalesce(func.sum(PosTransaction.total_amount), 0.0),
                func.coalesce(func.sum(PosTransaction.paid_amount), 0.0),
                func.coalesce(func.sum(case((PosTransaction.payment_status != "paid", 1), else_=0)), 0),
            ).where(
                PosTransaction.outlet_id == outlet_id,
                func.date(PosTransaction.transaction_at) == today,
                PosTransaction.order_status == "completed",
            )
        )).one()
        recent_rows = (await db.execute(
            select(
                PosTransaction.receipt_no,
                PosTransaction.transaction_at,
                PosTransaction.total_amount,
                PosTransaction.payment_status,
                PosPayment.transaction_category,
                PaymentMethod.name,
                PosPayment.amount,
            ).select_from(PosTransaction).join(PosPayment).join(PaymentMethod).where(
                PosTransaction.outlet_id == outlet_id,
                func.date(PosTransaction.transaction_at) == today,
            ).order_by(PosTransaction.transaction_at.desc()).limit(5)
        )).all()
        categories: dict[str, dict] = {}
        for category, method_name, count, amount in payment_rows:
            key = _as_text(category)
            entry = categories.setdefault(key, {"orders": 0, "amount": 0.0, "methods": []})
            entry["orders"] += int(count)
            entry["amount"] = round(entry["amount"] + float(amount), 2)
            entry["methods"].append({"name": method_name, "orders": int(count), "amount": round(float(amount), 2)})
        data = {
            "business_calendar": calendar,
            "payments": {
                "today": {
                    "orders": int(order_count),
                    "sales": round(float(sales), 2),
                    "paid_amount": round(float(paid), 2),
                    "payment_completion_pct": round((float(paid) / float(sales)) * 100, 1) if sales else 0.0,
                    "unpaid_or_partial_orders": int(payment_follow_up),
                },
                "categories": categories,
                "recent_transactions": [
                    {
                        "receipt_no": receipt_no,
                        "transaction_at": transaction_at,
                        "payment_method": method_name,
                        "transaction_category": _as_text(category),
                        "order_amount": round(float(total_amount), 2),
                        "paid_amount": round(float(payment_amount), 2),
                        "payment_status": payment_status,
                    }
                    for receipt_no, transaction_at, total_amount, payment_status, category, method_name, payment_amount in recent_rows
                ],
            },
        }
        reply = await _gemini_reply(message, intent, data) or _fallback_reply(intent, data)
        return reply, intent, data

    data = {"business_calendar": calendar, "supported_topics": list(INTENT_KEYWORDS.keys())}
    reply = await _gemini_reply(message, intent, data) or _fallback_reply(intent, data)
    return reply, intent, data
