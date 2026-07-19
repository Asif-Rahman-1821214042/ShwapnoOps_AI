"""Small deterministic POS dataset for the local demo database."""
import datetime as dt
import random

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Customer, Employee, Outlet, PaymentMethod, PosPayment, PosPaymentMethod,
    PosTerminal, PosTransaction, PosTransactionLine, ProductCategory,
)
from app.services.payment_classification import classify_payment_method


POS_PRODUCTS = [
    ("SKU-RICE-MINIKET-5KG", "Miniket Rice 5kg", "Rice", 560),
    ("SKU-SOYBEAN-OIL-1L", "Soybean Oil 1L", "Oil", 210),
    ("SKU-PASTEURIZED-MILK-1L", "Pasteurized Milk 1L", "Dairy", 110),
    ("SKU-EGG-BROWN-DZ", "Brown Eggs 12pc", "Egg", 155),
    ("SKU-DETERGENT-1KG", "Laundry Detergent 1kg", "Household", 195),
    ("SKU-BISCUIT-FAMILY-PKT", "Family Biscuit Pack", "Snacks", 85),
    ("SKU-BROILER-CHICKEN-1KG", "Broiler Chicken 1kg", "Meat", 265),
    ("SKU-VEG-COMBO-PACK", "Fresh Vegetable Combo", "Vegetable", 175),
]


async def ensure_pos_demo_data(db: AsyncSession) -> int:
    """Create and enrich local POS data once, without replacing project data."""
    outlets = (await db.execute(select(Outlet).order_by(Outlet.id))).scalars().all()
    methods = {}
    for code, name, is_mfs, is_digital in (
        ("cash", "Cash", False, False), ("bkash", "bKash", True, True), ("nagad", "Nagad", True, True),
        ("rocket", "Rocket", True, True), ("card", "Card", False, True),
        ("bank_transfer", "Bank transfer", False, True), ("other", "Other", False, True),
    ):
        method = (await db.execute(select(PaymentMethod).where(PaymentMethod.code == code))).scalar_one_or_none()
        if method is None:
            method = PaymentMethod(code=code, name=name, is_mobile_financial_service=is_mfs, is_digital=is_digital, transaction_category=classify_payment_method(code))
            db.add(method)
            await db.flush()
        methods[code] = method
        method.transaction_category = classify_payment_method(code)
        method.is_digital = method.transaction_category.value == "e_payment"

    terminals = {}
    for outlet in outlets:
        outlet_terminals = (await db.execute(
            select(PosTerminal).where(PosTerminal.outlet_id == outlet.id).order_by(PosTerminal.id)
        )).scalars().all()
        if not outlet_terminals:
            outlet_terminals = [
                PosTerminal(outlet_id=outlet.id, code="POS-01", name="Checkout 1"),
                PosTerminal(outlet_id=outlet.id, code="POS-02", name="Checkout 2"),
            ]
            db.add_all(outlet_terminals)
            await db.flush()
        terminals[outlet.id] = outlet_terminals

    existing = (await db.execute(select(func.count(PosTransaction.id)))).scalar_one()
    categories = {
        row.name: row.id
        for row in (await db.execute(select(ProductCategory))).scalars().all()
    }
    rng = random.Random(20260719)
    today = dt.date.today()
    created = 0

    if not existing:
        for outlet in outlets:
            for day_offset, transaction_count in ((1, 8), (0, 16)):
                transaction_date = today - dt.timedelta(days=day_offset)
                for sequence in range(1, transaction_count + 1):
                    hour = rng.choice([9, 10, 11, 12, 15, 16, 17, 18, 19, 20])
                    occurred_at = dt.datetime.combine(
                        transaction_date, dt.time(hour, rng.randint(0, 59), rng.randint(0, 59))
                    )
                    receipt = PosTransaction(
                        outlet_id=outlet.id,
                        receipt_no=f"POS-{outlet.code}-{transaction_date:%Y%m%d}-{sequence:04d}",
                        transaction_at=occurred_at,
                        cashier_name=rng.choice(["Ayesha Akter", "Rahim Islam", "Sadia Hossain"]),
                        payment_method=rng.choices(
                            [PosPaymentMethod.CASH, PosPaymentMethod.CARD, PosPaymentMethod.MFS],
                            weights=[40, 25, 35],
                        )[0],
                    )
                    db.add(receipt)
                    await db.flush()

                    subtotal = 0.0
                    discount = 0.0
                    item_count = 0
                    for sku, product_name, category, base_price in rng.sample(POS_PRODUCTS, rng.randint(1, 4)):
                        quantity = rng.randint(1, 3)
                        unit_price = round(base_price * rng.uniform(0.96, 1.04), 2)
                        line_discount = round(unit_price * quantity * (0.05 if rng.random() < 0.2 else 0), 2)
                        line_total = round(unit_price * quantity - line_discount, 2)
                        subtotal += unit_price * quantity
                        discount += line_discount
                        item_count += quantity
                        db.add(PosTransactionLine(
                            transaction_id=receipt.id,
                            sku=sku,
                            product_name=product_name,
                            category=category,
                            category_id=categories.get(category),
                            quantity=quantity,
                            unit_price=unit_price,
                            discount_amount=line_discount,
                            line_total=line_total,
                        ))
                    receipt.subtotal = round(subtotal, 2)
                    receipt.discount_amount = round(discount, 2)
                    receipt.tax_amount = 0.0
                    receipt.total_amount = round(subtotal - discount, 2)
                    receipt.item_count = item_count
                    created += 1

    orders = (await db.execute(select(PosTransaction).order_by(PosTransaction.id))).scalars().all()
    customer_names = ["Nusrat Jahan", "Imran Hossain", "Farzana Akter", "Tanvir Rahman", "Sadia Islam"]
    payment_codes = ["cash", "bkash", "nagad", "rocket", "card", "bank_transfer", "other"]
    for order in orders:
        employees = (await db.execute(
            select(Employee).where(Employee.outlet_id == order.outlet_id).order_by(Employee.id)
        )).scalars().all()
        if order.terminal_id is None:
            order.terminal_id = terminals[order.outlet_id][order.id % len(terminals[order.outlet_id])].id
        if order.cashier_employee_id is None and employees:
            cashier = next((employee for employee in employees if employee.name == order.cashier_name), employees[order.id % len(employees)])
            order.cashier_employee_id = cashier.id
            order.cashier_name = cashier.name
        if order.customer_id is None:
            phone = f"+88019{order.outlet_id:02d}{order.id:06d}"
            customer = Customer(name=customer_names[order.id % len(customer_names)], phone=phone)
            db.add(customer)
            await db.flush()
            order.customer_id = customer.id
        if order.id % 17 == 0:
            order.order_status, order.payment_status, order.paid_amount = "cancelled", "unpaid", 0.0
        elif order.id % 13 == 0:
            order.order_status, order.payment_status, order.paid_amount = "refunded", "refunded", order.total_amount
        elif order.id % 11 == 0:
            order.order_status, order.payment_status, order.paid_amount = "completed", "partial", round(order.total_amount * 0.6, 2)
        else:
            order.order_status, order.payment_status, order.paid_amount = "completed", "paid", order.total_amount
        if not (await db.execute(select(PosPayment.id).where(PosPayment.transaction_id == order.id))).scalar_one_or_none():
            method_code = payment_codes[order.id % len(payment_codes)]
            method = methods[method_code]
            reference = f"{method_code.upper()}-{order.transaction_at:%Y%m%d}-{order.id:06d}" if method.is_mobile_financial_service else None
            db.add(PosPayment(
                transaction_id=order.id,
                payment_method_id=method.id,
                transaction_reference=reference,
                amount=order.paid_amount,
                status=order.payment_status,
                transaction_category=classify_payment_method(method.code, order.terminal_id is not None),
                paid_at=order.transaction_at,
            ))

    await db.commit()
    return created
