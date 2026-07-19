import random
import datetime as dt
from sqlalchemy import delete
from app.database import AsyncSessionLocal, init_db
from app.models import (
    Outlet, SalesRecord, InventoryItem, ManpowerRoster, Complaint, ComplaintStatus,
    PosTransaction, PosTransactionLine,
    Employee, EmployeeAttendance, AttendanceStatus,
    Task, TaskSource, TaskStatus, Alert, AlertSeverity, AlertType,
    InventoryMovement, InventoryMovementType, StockOutEvent,
    PromotionCampaign, CampaignStatus, DeliverySchedule, DeliveryStatus,
    SeasonalEvent, StoreAuditReport, ManualIssue, ManualIssueStatus,
    DemandForecast, AiRecommendationAudit, OutletSalesTarget, ProductCategory,
)
from app.services.product_taxonomy import ensure_product_categories
from app.services.sales_targets import target_splits
from app.services.pos_demo import ensure_pos_demo_data

random.seed(42)

OUTLETS = [
    ("Shwapno Dhanmondi 27", "DHM27", "Dhaka", "Rafiqul Islam", "House 27, Road 16, Dhanmondi, Dhaka", "+8801700001027", "dhm27@shwapno.example"),
    ("Shwapno Gulshan Avenue", "GLS01", "Dhaka", "Nusrat Jahan", "Gulshan Avenue, Dhaka", "+8801700001001", "gls01@shwapno.example"),
    ("Shwapno Uttara Sector 7", "UTR07", "Dhaka", "Kamal Hossain", "Sector 7, Uttara, Dhaka", "+8801700001007", "utr07@shwapno.example"),
    ("Shwapno Chattogram GEC", "CTG02", "Chattogram", "Farhana Akter", "GEC Circle, Chattogram", "+8801700002002", "ctg02@shwapno.example"),
]

SKUS = [
    ("SKU-RICE-MINIKET-5KG", "Rice"), ("SKU-SOYBEAN-OIL-1L", "Oil"),
    ("SKU-PASTEURIZED-MILK-1L", "Dairy"), ("SKU-EGG-BROWN-DZ", "Egg"),
    ("SKU-DETERGENT-1KG", "Household"), ("SKU-LUX-SOAP-BAR", "Personal Care"),
    ("SKU-BISCUIT-FAMILY-PKT", "Snacks"), ("SKU-ISPahANI-TEA-500G", "Tea"),
    ("SKU-BROILER-CHICKEN-1KG", "Meat"), ("SKU-VEG-COMBO-PACK", "Vegetable"),
    ("SKU-HILSA-CUT-500G", "Fish"), ("SKU-DIAPER-MED-PACK", "Baby Care"),
    ("SKU-FROZEN-PARATHA-20PC", "Frozen"), ("SKU-CAT-FOOD-1KG", "Pet Food"),
]

COMPLAINT_CATEGORIES = ["Product Quality", "Billing", "Service", "Cleanliness", "Availability"]

STAFF_ROLES = [
    "Outlet Supervisor", "Cashier", "Cashier", "Floor Associate",
    "Floor Associate", "Fresh Food Associate", "Inventory Associate",
    "Customer Service", "Security", "Cleaner",
]


async def seed():
    await init_db()
    async with AsyncSessionLocal() as db:
        for model in (
            AiRecommendationAudit, DemandForecast, PosTransactionLine, PosTransaction,
            OutletSalesTarget,
            ManualIssue, StoreAuditReport, SeasonalEvent, DeliverySchedule,
            PromotionCampaign, StockOutEvent, InventoryMovement,
            Alert, Task, Complaint, EmployeeAttendance, Employee, ManpowerRoster,
            InventoryItem, SalesRecord, Outlet,
        ):
            await db.execute(delete(model))

        outlets = []
        for name, code, region, mgr, address, phone, email in OUTLETS:
            o = Outlet(
                name=name,
                code=code,
                region=region,
                manager_name=mgr,
                address=address,
                contact_phone=phone,
                contact_email=email,
                opening_date=dt.date(2021, random.randint(1, 12), random.randint(1, 24)),
                is_active=True,
            )
            db.add(o)
            outlets.append(o)
        await db.flush()

        today = dt.date.today()

        for o in outlets:
            monthly_target = random.randint(42, 58) * 100000
            split = target_splits(today.year, today.month, monthly_target)
            db.add(OutletSalesTarget(
                outlet_id=o.id,
                year=today.year,
                month=today.month,
                monthly_target=split["monthly_target"],
                weekly_target=split["weekly_target"],
                daily_target=split["daily_target"],
                weeks_in_month=split["weeks_in_month"],
                days_in_month=split["days_in_month"],
            ))

            # Sales history - last 21 days with weekend and festival bumps.
            for d_offset in range(21, 0, -1):
                d = today - dt.timedelta(days=d_offset)
                is_festival = d_offset in (3, 4, 5)
                weekend = d.weekday() in (4, 5)
                for sku, cat in SKUS:
                    base = random.randint(15, 60)
                    multiplier = (1.7 if is_festival else 1.0) * (1.25 if weekend else 1.0)
                    units = int(base * multiplier * random.uniform(0.8, 1.2))
                    db.add(SalesRecord(
                        outlet_id=o.id, sku=sku, category=cat, date=d,
                        units_sold=units, revenue=units * random.uniform(80, 450),
                        footfall=random.randint(200, 900) + (300 if is_festival else 0),
                        is_festival_period=is_festival,
                    ))
                # today's partial sales too
            for sku, cat in SKUS:
                units = random.randint(8, 34)
                db.add(SalesRecord(
                    outlet_id=o.id, sku=sku, category=cat, date=today,
                    units_sold=units, revenue=units * random.uniform(80, 450),
                    footfall=random.randint(50, 200), is_festival_period=False,
                ))

            # Inventory - deliberately make a couple of SKUs risky per outlet
            risky_skus = random.sample(SKUS, 3)
            for sku, cat in SKUS:
                avg_daily = random.uniform(8, 30)
                if (sku, cat) in risky_skus:
                    on_hand = random.randint(3, 18)
                else:
                    on_hand = random.randint(40, 200)
                db.add(InventoryItem(
                    outlet_id=o.id, sku=sku, category=cat, on_hand_units=on_hand,
                    reorder_point=20, avg_daily_sales=round(avg_daily, 1),
                    next_delivery_date=today + dt.timedelta(days=random.randint(1, 5)),
                ))

                for day_offset in range(5, 0, -1):
                    event_time = dt.datetime.utcnow() - dt.timedelta(days=day_offset, hours=random.randint(0, 6))
                    db.add(InventoryMovement(
                        outlet_id=o.id,
                        sku=sku,
                        category=cat,
                        movement_type=random.choice([
                            InventoryMovementType.RECEIPT,
                            InventoryMovementType.SALE,
                            InventoryMovementType.ADJUSTMENT,
                            InventoryMovementType.WASTAGE if cat in ("Fish", "Meat", "Vegetable", "Dairy") else InventoryMovementType.TRANSFER_IN,
                        ]),
                        quantity=random.choice([1, -1]) * random.randint(4, 55),
                        reference=f"INV-{o.code}-{day_offset}-{sku[-4:]}",
                        occurred_at=event_time,
                        note=random.choice([
                            "POS sale sync",
                            "DC receiving update",
                            "Cycle count adjustment",
                            "Inter-outlet transfer request",
                            "Perishable item quality wastage",
                        ]),
                    ))

            for sku, cat in random.sample(SKUS, 3):
                started = dt.datetime.utcnow() - dt.timedelta(days=random.randint(4, 18), hours=random.randint(2, 8))
                resolved = started + dt.timedelta(hours=random.randint(4, 28))
                db.add(StockOutEvent(
                    outlet_id=o.id,
                    sku=sku,
                    category=cat,
                    started_at=started,
                    resolved_at=resolved if random.random() > 0.2 else None,
                    estimated_lost_sales=random.randint(4500, 26000),
                    root_cause=random.choice([
                        "Supplier short shipment",
                        "Unexpected campaign uplift",
                        "Delayed DC dispatch",
                        "Shelf replenishment missed during peak hour",
                    ]),
                ))

            # Manpower roster today - one outlet deliberately understaffed
            shifts = [("morning", 6), ("peak", 10), ("evening", 6), ("closing", 4)]
            understaff = random.random() < 0.5
            for shift, required in shifts:
                present = required - random.randint(0, 1)
                if understaff and shift == "peak":
                    present = max(2, required - random.randint(3, 5))
                db.add(ManpowerRoster(
                    outlet_id=o.id, date=today, shift=shift, required_staff=required,
                    present_staff=present,
                    peak_hour_footfall_forecast=random.randint(400, 1200),
                ))

            # Employee attendance - last 7 days, filtered by outlet/date in the UI.
            first_names = ["Ayesha", "Rahim", "Sadia", "Imran", "Nabila", "Tanvir", "Mitu", "Hasan", "Ruma", "Jahid"]
            last_names = ["Akter", "Islam", "Hossain", "Rahman", "Karim", "Begum", "Ahmed", "Chowdhury", "Alam", "Sultana"]
            outlet_staff = [
                Employee(
                    outlet_id=o.id,
                    employee_code=f"EMP-{o.code}-{idx + 1:03d}",
                    name=f"{first_names[idx]} {last_names[idx]}",
                    email=f"emp-{o.code.lower()}-{idx + 1:03d}@shwapno.example",
                    phone=f"+88018{o.id:02d}{idx + 1:06d}",
                    designation=role,
                    hire_date=today - dt.timedelta(days=180 + idx * 17),
                    is_active=True,
                )
                for idx, role in enumerate(STAFF_ROLES)
            ]
            db.add_all(outlet_staff)
            await db.flush()

            for d_offset in range(6, -1, -1):
                attendance_date = today - dt.timedelta(days=d_offset)
                is_thursday = attendance_date.weekday() == 3
                for idx, employee in enumerate(outlet_staff):
                    start, end = dt.time(9, 0), dt.time(18, 0)
                    status = random.choices(
                        [
                            AttendanceStatus.PRESENT,
                            AttendanceStatus.LATE,
                            AttendanceStatus.ABSENT,
                            AttendanceStatus.LEAVE,
                            AttendanceStatus.HALF_DAY,
                        ],
                        weights=[74, 11, 7, 4, 4],
                    )[0]
                    if is_thursday and idx in (2, 5):
                        status = AttendanceStatus.LATE
                    if is_thursday and idx == 8:
                        status = AttendanceStatus.ABSENT

                    check_in = None
                    check_out = None
                    note = ""
                    if status in (AttendanceStatus.PRESENT, AttendanceStatus.LATE):
                        late_minutes = random.randint(8, 32) if status == AttendanceStatus.LATE else random.randint(-10, 5)
                        check_in = dt.datetime.combine(attendance_date, start) + dt.timedelta(minutes=late_minutes)
                        check_out = dt.datetime.combine(attendance_date, end) + dt.timedelta(minutes=random.randint(-12, 18))
                        note = "Late arrival logged by supervisor" if status == AttendanceStatus.LATE else "Biometric punch synced"
                    elif status == AttendanceStatus.HALF_DAY:
                        check_in = dt.datetime.combine(attendance_date, start) + dt.timedelta(minutes=random.randint(-5, 10))
                        check_out = check_in + dt.timedelta(hours=4)
                        note = "Half-day attendance approved"
                    elif status == AttendanceStatus.ABSENT:
                        note = "No punch recorded"
                    else:
                        note = "Approved leave"

                    working_hours = round((check_out - check_in).total_seconds() / 3600, 2) if check_in and check_out else 0.0
                    db.add(EmployeeAttendance(
                        employee_id=employee.id,
                        attendance_date=attendance_date,
                        check_in_at=check_in,
                        check_out_at=check_out,
                        status=status,
                        working_hours=working_hours,
                        remarks=note,
                    ))

            # Complaints - a mix of open/resolved
            for _ in range(random.randint(2, 5)):
                db.add(Complaint(
                    outlet_id=o.id,
                    category=random.choice(COMPLAINT_CATEGORIES),
                    description=random.choice([
                        "Customer reported item priced differently at shelf vs billing.",
                        "Long queue at checkout during peak hours.",
                        "Reported spoiled produce in vegetable section.",
                        "Requested SKU not available on shelf.",
                        "Floor cleanliness feedback near dairy section.",
                    ]),
                    severity=random.randint(1, 5),
                    status=random.choice([ComplaintStatus.OPEN, ComplaintStatus.OPEN, ComplaintStatus.RESOLVED]),
                ))

            db.add_all([
                PromotionCampaign(
                    outlet_id=o.id,
                    name="Weekend Staples Basket",
                    category="Oil",
                    sku="SKU-SOYBEAN-OIL-1L",
                    start_date=today - dt.timedelta(days=1),
                    end_date=today + dt.timedelta(days=2),
                    discount_pct=8,
                    expected_uplift_pct=22,
                    status=CampaignStatus.ACTIVE,
                    owner="Commercial Grocery",
                ),
                PromotionCampaign(
                    outlet_id=o.id,
                    name="Meat Friday Value",
                    category="Meat",
                    sku="SKU-BROILER-CHICKEN-1KG",
                    start_date=today + dt.timedelta(days=1),
                    end_date=today + dt.timedelta(days=3),
                    discount_pct=6,
                    expected_uplift_pct=18,
                    status=CampaignStatus.PLANNED,
                    owner="Meat Category Team",
                ),
                PromotionCampaign(
                    outlet_id=o.id,
                    name="Baby Care Monthly Saver",
                    category="Baby Care",
                    sku="SKU-DIAPER-MED-PACK",
                    start_date=today - dt.timedelta(days=8),
                    end_date=today - dt.timedelta(days=2),
                    discount_pct=10,
                    expected_uplift_pct=16,
                    status=CampaignStatus.COMPLETED,
                    owner="Non-food Category Team",
                ),
            ])

            for supplier, sku, cat, qty, status in [
                ("Shwapno DC Tongi", "SKU-RICE-MINIKET-5KG", "Rice", 140, DeliveryStatus.IN_TRANSIT),
                ("Aarong Dairy", "SKU-PASTEURIZED-MILK-1L", "Dairy", 90, DeliveryStatus.SCHEDULED),
                ("Kazi Farms", "SKU-BROILER-CHICKEN-1KG", "Meat", 65, DeliveryStatus.DELAYED),
                ("ACI Logistics", "SKU-DETERGENT-1KG", "Household", 48, DeliveryStatus.RECEIVED),
            ]:
                db.add(DeliverySchedule(
                    outlet_id=o.id,
                    supplier=supplier,
                    sku=sku,
                    category=cat,
                    quantity=qty,
                    scheduled_date=today + dt.timedelta(days=random.randint(0, 3)),
                    eta_window=random.choice(["08:00-10:00", "11:00-13:00", "15:00-17:00"]),
                    status=status,
                    grn_reference=f"GRN-{o.code}-{random.randint(100,999)}" if status == DeliveryStatus.RECEIVED else None,
                    note=random.choice([
                        "Priority receiving requested",
                        "Cold-chain check required",
                        "Pending GRN confirmation",
                        "Supplier confirmed dispatch",
                    ]),
                ))

            year = today.year
            seasonal_calendar = [
                ("Shab-e-Barat", dt.date(year, 2, 4), dt.date(year, 2, 4), "Dairy, sweets, family grocery", 12, "Actual Bangladesh 2026 holiday calendar marker."),
                ("Eid al-Fitr", dt.date(year, 3, 19), dt.date(year, 3, 23), "Staples, snacks, beverage, gift packs", 38, "Actual Bangladesh 2026 Eid al-Fitr window."),
                ("Pahela Boishakh", dt.date(year, 4, 14), dt.date(year, 4, 14), "Beverage, snacks, vegetable", 19, "Bengali New Year demand marker."),
                ("Buddha Purnima", dt.date(year, 5, 1), dt.date(year, 5, 1), "Vegetarian grocery, fruit, sweets", 9, "Actual Bangladesh 2026 holiday calendar marker."),
                ("Eid al-Adha", dt.date(year, 5, 26), dt.date(year, 5, 31), "Meat, fish, spices, oil, rice", 42, "Actual Bangladesh 2026 Eid al-Adha window."),
                ("Ashura", dt.date(year, 6, 26), dt.date(year, 6, 26), "Staples, milk, ready-to-cook items", 10, "Actual Bangladesh 2026 holiday calendar marker."),
                ("Eid-e-Milad un-Nabi", dt.date(year, 8, 26), dt.date(year, 8, 26), "Family grocery, sweets, bakery", 13, "Actual Bangladesh 2026 holiday calendar marker."),
                ("Krishna Janmashtami", dt.date(year, 9, 4), dt.date(year, 9, 4), "Dairy, sweets, fruit", 16, "Actual Bangladesh 2026 holiday calendar marker."),
                ("Vijayadashami", dt.date(year, 10, 21), dt.date(year, 10, 21), "Snacks, sweets, dairy, gift packs", 24, "Durga Puja/Vijayadashami demand marker."),
                ("Christmas Day", dt.date(year, 12, 25), dt.date(year, 12, 25), "Bakery, beverage, gift packs", 14, "Actual Bangladesh public holiday marker."),
            ]
            db.add_all([
                SeasonalEvent(
                    outlet_id=o.id,
                    name=name,
                    start_date=start,
                    end_date=end,
                    category_focus=focus,
                    uplift_pct=uplift,
                    notes=notes,
                )
                for name, start, end, focus, uplift, notes in seasonal_calendar
            ])

            for d_offset in (2, 16):
                score = random.uniform(78, 96)
                db.add(StoreAuditReport(
                    outlet_id=o.id,
                    audit_date=today - dt.timedelta(days=d_offset),
                    auditor_name=random.choice(["Tanvir Ahmed", "Moumita Roy", "Shahidul Karim"]),
                    score_pct=round(score, 1),
                    hygiene_score=round(score - random.uniform(0, 5), 1),
                    planogram_score=round(score - random.uniform(0, 7), 1),
                    cash_process_score=round(score - random.uniform(0, 4), 1),
                    findings=random.choice([
                        "Fish, meat and vegetable aisle signage needs alignment with current promo.",
                        "Backroom FIFO discipline good; dairy crate labeling needs attention.",
                        "Checkout cash handover log missing supervisor sign-off.",
                    ]),
                    corrective_action=random.choice([
                        "Complete planogram correction before evening peak.",
                        "Retrain receiving team on cold-chain checklist.",
                        "Update cash reconciliation checklist and verify daily.",
                    ]),
                ))

            db.add_all([
                ManualIssue(
                    outlet_id=o.id,
                    title="POS scanner intermittent at checkout 2",
                    description="Barcode scanner disconnects during peak queue; temporary manual entry is slowing billing.",
                    category="IT/POS",
                    severity=4,
                    status=ManualIssueStatus.ASSIGNED,
                    reported_by=o.manager_name,
                ),
                ManualIssue(
                    outlet_id=o.id,
                    title="Dairy chiller temperature variance",
                    description="Evening temperature log crossed threshold once; maintenance check requested.",
                    category="Maintenance",
                    severity=3,
                    status=ManualIssueStatus.OPEN,
                    reported_by=o.manager_name,
                ),
            ])

            now = dt.datetime.utcnow()
            db.add_all([
                Alert(
                    outlet_id=o.id,
                    type=AlertType.FESTIVAL_DEMAND,
                    severity=AlertSeverity.WARNING,
                    message=f"Weekend grocery basket demand trending above baseline at {o.name}.",
                    created_at=now - dt.timedelta(minutes=random.randint(18, 90)),
                ),
                Alert(
                    outlet_id=o.id,
                    type=AlertType.OPERATIONAL_DELAY,
                    severity=random.choice([AlertSeverity.INFO, AlertSeverity.WARNING]),
                    message="Vendor receiving bay has pending GRN confirmation for meat, fish, vegetable and dairy delivery.",
                    created_at=now - dt.timedelta(minutes=random.randint(5, 45)),
                    acknowledged=random.choice([False, False, True]),
                ),
            ])

            db.add_all([
                Task(
                    outlet_id=o.id,
                    title="Validate shelf prices for high-velocity food SKUs",
                    description="Cross-check shelf labels against POS for oil, rice, tea, and biscuit family packs before evening rush.",
                    source=TaskSource.AUDIT,
                    priority_score=random.uniform(52, 74),
                    status=TaskStatus.PENDING,
                    due_at=now + dt.timedelta(hours=3),
                ),
                Task(
                    outlet_id=o.id,
                    title="Prepare evening queue-busting checkout plan",
                    description="Assign backup cashier and floor support for peak footfall window.",
                    source=TaskSource.MANPOWER,
                    priority_score=random.uniform(58, 84),
                    status=TaskStatus.PENDING,
                    due_at=now + dt.timedelta(hours=2),
                ),
                Task(
                    outlet_id=o.id,
                    title="Refresh fish, meat, vegetable display and expiry check",
                    description="Inspect vegetables, poultry, fish, and chilled dairy before the customer traffic spike.",
                    source=TaskSource.AUDIT,
                    priority_score=random.uniform(38, 62),
                    status=random.choice([TaskStatus.PENDING, TaskStatus.IN_PROGRESS]),
                    due_at=now + dt.timedelta(hours=5),
                ),
                Task(
                    outlet_id=o.id,
                    title="Review competitor promo impact on staples",
                    description="Check if local competitor discounting is affecting rice and edible oil sell-through.",
                    source=TaskSource.PROMOTION,
                    priority_score=random.uniform(34, 55),
                    status=TaskStatus.PENDING,
                    due_at=now + dt.timedelta(days=1),
                ),
            ])

        await db.commit()
        await ensure_product_categories(db)
        await ensure_pos_demo_data(db)
    print("Seed data created.")


if __name__ == "__main__":
    import asyncio
    asyncio.run(seed())
