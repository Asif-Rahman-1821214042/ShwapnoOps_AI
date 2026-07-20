import csv
import datetime as dt
import io

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import (
    AttendanceStatus, Customer, Employee, EmployeeAttendance, InventoryItem,
    Outlet, OutletSalesTarget, PaymentMethod, PosPayment, PosTerminal, PosTransaction, TransactionCategory,
)

router = APIRouter(prefix="/api/pos", tags=["pos"])


def _range(period: str, start_date: dt.date | None, end_date: dt.date | None) -> tuple[dt.datetime, dt.datetime]:
    today = dt.date.today()
    if period == "yesterday":
        start = today - dt.timedelta(days=1)
        end = today
    elif period == "week":
        start = today - dt.timedelta(days=today.weekday())
        end = today + dt.timedelta(days=1)
    elif period == "month":
        start = today.replace(day=1)
        end = today + dt.timedelta(days=1)
    elif period == "custom":
        if not start_date or not end_date:
            raise HTTPException(422, "Custom range needs start_date and end_date")
        start, end = start_date, end_date + dt.timedelta(days=1)
    else:
        start, end = today, today + dt.timedelta(days=1)
    return dt.datetime.combine(start, dt.time.min), dt.datetime.combine(end, dt.time.min)


def _filters(outlet_id, terminal_id, cashier_employee_id, start, end):
    filters = [PosTransaction.transaction_at >= start, PosTransaction.transaction_at < end]
    if outlet_id:
        filters.append(PosTransaction.outlet_id == outlet_id)
    if terminal_id:
        filters.append(PosTransaction.terminal_id == terminal_id)
    if cashier_employee_id:
        filters.append(PosTransaction.cashier_employee_id == cashier_employee_id)
    return filters


def _transaction_category_filter(category: TransactionCategory):
    return PosTransaction.id.in_(
        select(PosPayment.transaction_id).where(PosPayment.transaction_category == category)
    )


async def _require_outlet(db: AsyncSession, outlet_id: int) -> None:
    exists = await db.scalar(select(Outlet.id).where(Outlet.id == outlet_id))
    if not exists:
        raise HTTPException(404, "Outlet not found")


def _order_payload(order: PosTransaction) -> dict:
    return {
        "id": order.id, "invoice_no": order.receipt_no, "transaction_at": order.transaction_at,
        "customer_name": order.customer.name if order.customer else "Walk-in Customer",
        "customer_phone": order.customer.phone if order.customer else None,
        "outlet_id": order.outlet_id, "outlet_name": order.outlet.name if order.outlet else "",
        "terminal_id": order.terminal_id, "terminal_name": order.terminal.name if order.terminal else "Unassigned",
        "cashier_employee_id": order.cashier_employee_id,
        "cashier_name": order.cashier_employee.name if order.cashier_employee else order.cashier_name,
        "order_amount": order.total_amount, "discount_amount": order.discount_amount,
        "tax_amount": order.tax_amount, "paid_amount": order.paid_amount,
        "payment_status": order.payment_status, "order_status": order.order_status,
        "payments": [{
            "id": payment.id, "method_id": payment.payment_method_id,
            "payment_method": payment.payment_method.name, "transaction_category": payment.transaction_category.value if hasattr(payment.transaction_category, "value") else payment.transaction_category, "transaction_id": payment.transaction_reference,
            "amount": payment.amount, "status": payment.status, "paid_at": payment.paid_at,
        } for payment in order.payments],
        "lines": [{
            "sku": line.sku, "product_name": line.product_name, "category": line.category,
            "quantity": line.quantity, "unit_price": line.unit_price,
            "discount_amount": line.discount_amount, "line_total": line.line_total,
        } for line in order.lines],
    }


@router.get("/payment-methods")
async def payment_methods(db: AsyncSession = Depends(get_db)):
    return [{"id": row.id, "code": row.code, "name": row.name, "is_mobile_financial_service": row.is_mobile_financial_service, "transaction_category": row.transaction_category.value if hasattr(row.transaction_category, "value") else row.transaction_category}
            for row in (await db.execute(select(PaymentMethod).where(PaymentMethod.is_active.is_(True)).order_by(PaymentMethod.id))).scalars()]


@router.get("/kpis")
async def pos_kpis(
    period: str = "today", start_date: dt.date | None = None, end_date: dt.date | None = None,
    outlet_id: int = Query(..., gt=0), terminal_id: int | None = None, cashier_employee_id: int | None = None,
    transaction_category: TransactionCategory | None = None,
    db: AsyncSession = Depends(get_db),
):
    await _require_outlet(db, outlet_id)
    start, end = _range(period, start_date, end_date)
    filters = _filters(outlet_id, terminal_id, cashier_employee_id, start, end)
    if transaction_category:
        filters.append(_transaction_category_filter(transaction_category))
    order_row = (await db.execute(select(
        func.coalesce(func.sum(case((PosTransaction.order_status == "completed", PosTransaction.total_amount), else_=0.0)), 0.0),
        func.coalesce(func.count(case((PosTransaction.order_status == "completed", PosTransaction.id))), 0),
        func.coalesce(func.count(case((PosTransaction.payment_status.in_(["unpaid", "partial"]), PosTransaction.id))), 0),
        func.coalesce(func.count(case((PosTransaction.order_status == "cancelled", PosTransaction.id))), 0),
        func.coalesce(func.count(case((PosTransaction.order_status == "refunded", PosTransaction.id))), 0),
        func.coalesce(func.avg(case((PosTransaction.order_status == "completed", PosTransaction.total_amount))), 0.0),
        func.coalesce(func.avg(case((PosTransaction.order_status == "completed", PosTransaction.item_count))), 0.0),
        func.coalesce(func.count(case((and_(PosTransaction.order_status == "completed", PosTransaction.payment_status == "paid"), PosTransaction.id))), 0),
    ).where(*filters))).one()
    sales, completed_orders, pending_orders, cancelled, refunded, average_basket, average_items, paid_orders = order_row
    days = max((end.date() - start.date()).days, 1)
    target_stmt = select(func.coalesce(func.sum(OutletSalesTarget.daily_target), 0.0)).where(
        OutletSalesTarget.year == start.year, OutletSalesTarget.month == start.month
    )
    target_stmt = target_stmt.where(OutletSalesTarget.outlet_id == outlet_id)
    daily_target = float((await db.execute(target_stmt)).scalar_one())
    sales_target = daily_target * days
    achievement = round(100 * float(sales) / sales_target, 1) if sales_target else None
    digital_rows = (await db.execute(
        select(PaymentMethod.id, PaymentMethod.name, func.coalesce(func.sum(PosPayment.amount), 0.0))
        .select_from(PaymentMethod).join(PosPayment).join(PosTransaction)
        .where(PosPayment.transaction_category == (transaction_category or TransactionCategory.E_PAYMENT), PosPayment.status.in_(["paid", "partial"]), PosTransaction.order_status == "completed", *filters)
        .group_by(PaymentMethod.id).order_by(PaymentMethod.id)
    )).all()
    digital_total = sum(float(row[2]) for row in digital_rows)
    inventory_stmt = select(
        func.count(InventoryItem.id),
        func.coalesce(func.sum(case((InventoryItem.on_hand_units > 0, 1), else_=0)), 0),
        func.coalesce(func.sum(case((InventoryItem.on_hand_units <= InventoryItem.reorder_point, 1), else_=0)), 0),
    )
    inventory_stmt = inventory_stmt.where(InventoryItem.outlet_id == outlet_id)
    inventory_total, in_stock, low_stock = (await db.execute(inventory_stmt)).one()
    availability = round(100 * in_stock / inventory_total, 1) if inventory_total else 0.0
    attendance_date = dt.date.today()
    attendance_stmt = select(EmployeeAttendance.status, func.count(EmployeeAttendance.id)).join(Employee).where(
        EmployeeAttendance.attendance_date == attendance_date, Employee.is_active.is_(True)
    )
    attendance_stmt = attendance_stmt.where(Employee.outlet_id == outlet_id)
    attendance_counts = {str(status.value if hasattr(status, "value") else status): count for status, count in (await db.execute(attendance_stmt.group_by(EmployeeAttendance.status))).all()}
    employee_stmt = select(func.count(Employee.id)).where(Employee.is_active.is_(True))
    employee_stmt = employee_stmt.where(Employee.outlet_id == outlet_id)
    scheduled = (await db.execute(employee_stmt)).scalar_one()
    present = attendance_counts.get("present", 0) + attendance_counts.get("late", 0)
    absent, leave = attendance_counts.get("absent", 0), attendance_counts.get("leave", 0)
    attendance_pct = round(100 * present / scheduled, 1) if scheduled else 0.0
    payment_rate = round(100 * paid_orders / completed_orders, 1) if completed_orders else 0.0
    score_parts = [min(achievement or 0, 100), 100 if completed_orders else 0, 100 if average_basket else 0, attendance_pct, payment_rate, availability]
    productivity = round(sum(score_parts) / len(score_parts), 1)
    return {
        "range": {"start": start.date(), "end": (end - dt.timedelta(days=1)).date(), "period": period},
        "sales": {"amount": round(float(sales), 2), "target": round(sales_target, 2), "achievement_pct": achievement, "variance": round(float(sales) - sales_target, 2)},
        "digital_cash": {"amount": round(digital_total, 2), "sales_pct": round(100 * digital_total / float(sales), 1) if sales else 0, "breakdown": [{"id": method_id, "name": name, "amount": round(float(amount), 2)} for method_id, name, amount in digital_rows]},
        "orders": {"completed": completed_orders, "pending": pending_orders, "cancelled": cancelled, "refunded": refunded, "target": None},
        "basket": {"average_value": round(float(average_basket), 2), "average_items": round(float(average_items), 1), "target": None},
        "payment_completion": {"rate": payment_rate, "paid_orders": paid_orders, "pending_or_failed": pending_orders},
        "availability": {"pct": availability, "available_products": in_stock, "out_of_stock": inventory_total - in_stock, "low_stock": low_stock},
        "attendance": {"present": present, "scheduled": scheduled, "absent": absent, "leave": leave, "day_off": 0, "pct": attendance_pct},
        "productivity": {"score": productivity, "district_comparison_pct": None},
    }


@router.get("/terminals")
async def terminals(outlet_id: int | None = None, db: AsyncSession = Depends(get_db)):
    stmt = select(PosTerminal).where(PosTerminal.is_active.is_(True)).order_by(PosTerminal.name)
    if outlet_id:
        stmt = stmt.where(PosTerminal.outlet_id == outlet_id)
    return [{"id": row.id, "outlet_id": row.outlet_id, "code": row.code, "name": row.name}
            for row in (await db.execute(stmt)).scalars()]


@router.get("/cashiers")
async def cashiers(outlet_id: int | None = None, db: AsyncSession = Depends(get_db)):
    stmt = select(Employee).where(Employee.is_active.is_(True)).order_by(Employee.name)
    if outlet_id:
        stmt = stmt.where(Employee.outlet_id == outlet_id)
    return [{"id": row.id, "outlet_id": row.outlet_id, "name": row.name, "employee_code": row.employee_code}
            for row in (await db.execute(stmt)).scalars()]


@router.get("/transactions")
async def list_transactions(
    outlet_id: int, date: dt.date | None = None, limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Compact receipt feed retained for the operations dashboard."""
    await _require_outlet(db, outlet_id)
    stmt = select(PosTransaction).where(PosTransaction.outlet_id == outlet_id).options(
        selectinload(PosTransaction.payments).selectinload(PosPayment.payment_method)
    ).order_by(PosTransaction.transaction_at.desc()).limit(limit)
    if date:
        start = dt.datetime.combine(date, dt.time.min)
        stmt = stmt.where(PosTransaction.transaction_at >= start, PosTransaction.transaction_at < start + dt.timedelta(days=1))
    rows = (await db.execute(stmt)).scalars().all()
    return [{
        "id": row.id, "outlet_id": row.outlet_id, "receipt_no": row.receipt_no,
        "transaction_at": row.transaction_at, "cashier_name": row.cashier_name,
        "payment_method": row.payments[0].payment_method.name if row.payments else "Unpaid",
        "transaction_category": (row.payments[0].transaction_category.value if row.payments and hasattr(row.payments[0].transaction_category, "value") else (row.payments[0].transaction_category if row.payments else None)),
        "total_amount": row.total_amount, "item_count": row.item_count,
        "status": row.order_status,
    } for row in rows]


@router.get("/summary")
async def pos_summary(
    period: str = "today", start_date: dt.date | None = None, end_date: dt.date | None = None,
    outlet_id: int = Query(..., gt=0), terminal_id: int | None = None, cashier_employee_id: int | None = None,
    transaction_category: TransactionCategory | None = None,
    db: AsyncSession = Depends(get_db),
):
    await _require_outlet(db, outlet_id)
    start, end = _range(period, start_date, end_date)
    filters = _filters(outlet_id, terminal_id, cashier_employee_id, start, end)
    if transaction_category:
        filters.append(_transaction_category_filter(transaction_category))
    row = (await db.execute(select(
        func.count(PosTransaction.id),
        func.coalesce(func.sum(case((PosTransaction.order_status == "completed", PosTransaction.total_amount), else_=0.0)), 0.0),
        func.coalesce(func.sum(case((and_(PosTransaction.order_status == "completed", PosTransaction.payment_status.in_(["paid", "partial"])), PosTransaction.paid_amount), else_=0.0)), 0.0),
        func.coalesce(func.sum(case((and_(PosTransaction.order_status == "completed", PosTransaction.payment_status != "paid"), PosTransaction.total_amount - PosTransaction.paid_amount), else_=0.0)), 0.0),
        func.coalesce(func.sum(case((PosTransaction.order_status == "cancelled", 1), else_=0)), 0),
        func.coalesce(func.sum(case((PosTransaction.order_status == "refunded", 1), else_=0)), 0),
        func.coalesce(func.sum(PosTransaction.discount_amount), 0.0),
        func.coalesce(func.sum(PosTransaction.tax_amount), 0.0),
        func.coalesce(func.avg(case((PosTransaction.order_status != "cancelled", PosTransaction.total_amount))), 0.0),
    ).where(*filters))).one()
    methods = (await db.execute(select(PaymentMethod).where(PaymentMethod.is_active.is_(True)).order_by(PaymentMethod.id))).scalars().all()
    if transaction_category:
        methods = [method for method in methods if method.transaction_category == transaction_category]
    payment_rows = (await db.execute(
        select(PosPayment.payment_method_id, func.count(PosPayment.id), func.coalesce(func.sum(PosPayment.amount), 0.0))
        .select_from(PosPayment).join(PosTransaction)
        .where(*filters, PosTransaction.order_status == "completed", PosPayment.status.in_(["paid", "partial"]))
        .group_by(PosPayment.payment_method_id)
    )).all()
    payment_totals = {method_id: (count, amount) for method_id, count, amount in payment_rows}
    total_sales = float(row[1])
    return {
        "range": {"start": start.date(), "end": (end - dt.timedelta(days=1)).date(), "period": period},
        "orders": row[0], "total_sales": round(total_sales, 2), "total_paid": round(float(row[2]), 2),
        "pending_unpaid": round(float(row[3]), 2), "cancelled_orders": row[4], "refunded_orders": row[5],
        "average_order_value": round(float(row[8]), 2), "total_discount": round(float(row[6]), 2), "total_tax": round(float(row[7]), 2),
        "payment_methods": [{"id": method.id, "name": method.name, "transaction_category": method.transaction_category.value if hasattr(method.transaction_category, "value") else method.transaction_category, "orders": payment_totals.get(method.id, (0, 0.0))[0], "received_amount": round(float(payment_totals.get(method.id, (0, 0.0))[1]), 2),
                             "sales_pct": round(100 * float(amount) / total_sales, 1) if total_sales else 0}
                            for method in methods for amount in [payment_totals.get(method.id, (0, 0.0))[1]]],
    }


@router.get("/mobile-orders")
async def mobile_orders(
    period: str = "today", start_date: dt.date | None = None, end_date: dt.date | None = None,
    outlet_id: int = Query(..., gt=0), terminal_id: int | None = None, cashier_employee_id: int | None = None,
    payment_method_id: int | None = None, payment_status: str | None = None, order_status: str | None = None,
    transaction_category: TransactionCategory = TransactionCategory.E_PAYMENT,
    search: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    sort_by: str = "transaction_at", sort_dir: str = "desc", db: AsyncSession = Depends(get_db),
):
    await _require_outlet(db, outlet_id)
    start, end = _range(period, start_date, end_date)
    filters = _filters(outlet_id, terminal_id, cashier_employee_id, start, end) + [PosPayment.transaction_category == transaction_category]
    if payment_method_id:
        filters.append(PosPayment.payment_method_id == payment_method_id)
    if payment_status:
        filters.append(PosTransaction.payment_status == payment_status)
    if order_status:
        filters.append(PosTransaction.order_status == order_status)
    if search:
        term = f"%{search.strip()}%"
        filters.append(or_(PosTransaction.receipt_no.ilike(term), Customer.phone.ilike(term), PosPayment.transaction_reference.ilike(term)))
    base = select(PosTransaction).join(PosPayment).join(PaymentMethod).outerjoin(Customer).where(*filters).distinct()
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    sort_map = {"transaction_at": PosTransaction.transaction_at, "amount": PosTransaction.total_amount, "invoice": PosTransaction.receipt_no}
    ordering = sort_map.get(sort_by, PosTransaction.transaction_at)
    ordering = ordering.asc() if sort_dir.lower() == "asc" else ordering.desc()
    rows = (await db.execute(base.options(
        selectinload(PosTransaction.payments).selectinload(PosPayment.payment_method), selectinload(PosTransaction.lines),
        selectinload(PosTransaction.customer), selectinload(PosTransaction.outlet), selectinload(PosTransaction.terminal), selectinload(PosTransaction.cashier_employee),
    ).order_by(ordering).offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return {"items": [_order_payload(row) for row in rows], "total": total, "page": page, "page_size": page_size}


@router.get("/orders/{order_id}")
async def order_details(order_id: int, outlet_id: int = Query(..., gt=0), db: AsyncSession = Depends(get_db)):
    await _require_outlet(db, outlet_id)
    order = (await db.execute(select(PosTransaction).where(PosTransaction.id == order_id, PosTransaction.outlet_id == outlet_id).options(
        selectinload(PosTransaction.payments).selectinload(PosPayment.payment_method), selectinload(PosTransaction.lines),
        selectinload(PosTransaction.customer), selectinload(PosTransaction.outlet), selectinload(PosTransaction.terminal), selectinload(PosTransaction.cashier_employee),
    ))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "POS order not found")
    return _order_payload(order)


@router.get("/mobile-orders/export.csv")
async def export_mobile_orders(
    period: str = "today", start_date: dt.date | None = None, end_date: dt.date | None = None,
    outlet_id: int = Query(..., gt=0), terminal_id: int | None = None, cashier_employee_id: int | None = None,
    payment_method_id: int | None = None, payment_status: str | None = None, order_status: str | None = None,
    transaction_category: TransactionCategory = TransactionCategory.E_PAYMENT,
    search: str | None = None, db: AsyncSession = Depends(get_db),
):
    await _require_outlet(db, outlet_id)
    start, end = _range(period, start_date, end_date)
    filters = _filters(outlet_id, terminal_id, cashier_employee_id, start, end) + [PosPayment.transaction_category == transaction_category]
    if payment_method_id: filters.append(PosPayment.payment_method_id == payment_method_id)
    if payment_status: filters.append(PosTransaction.payment_status == payment_status)
    if order_status: filters.append(PosTransaction.order_status == order_status)
    if search:
        term = f"%{search.strip()}%"
        filters.append(or_(PosTransaction.receipt_no.ilike(term), Customer.phone.ilike(term), PosPayment.transaction_reference.ilike(term)))
    rows = (await db.execute(select(PosTransaction).join(PosPayment).join(PaymentMethod).outerjoin(Customer).where(*filters).distinct().options(
        selectinload(PosTransaction.payments).selectinload(PosPayment.payment_method), selectinload(PosTransaction.customer),
        selectinload(PosTransaction.outlet), selectinload(PosTransaction.cashier_employee),
    ).order_by(PosTransaction.transaction_at.desc()).limit(10000))).scalars().all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Invoice", "Date/time", "Customer", "Phone", "Outlet", "Cashier", "Payment method", "Transaction ID", "Order amount", "Discount", "Paid", "Payment status", "Order status"])
    for order in rows:
        payment = next((row for row in order.payments if row.transaction_category == TransactionCategory.E_PAYMENT), None)
        if payment:
            writer.writerow([order.receipt_no, order.transaction_at.isoformat(sep=" "), order.customer.name if order.customer else "Walk-in Customer", order.customer.phone if order.customer else "", order.outlet.name, order.cashier_employee.name if order.cashier_employee else order.cashier_name, payment.payment_method.name, payment.transaction_reference or "", order.total_amount, order.discount_amount, order.paid_amount, order.payment_status, order.order_status])
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=mobile-financial-service-orders.csv"})
