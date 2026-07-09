import enum
import datetime as dt
from sqlalchemy import (
    String, Integer, Float, DateTime, ForeignKey, Enum, Boolean, Text, JSON
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


def utcnow():
    return dt.datetime.utcnow()


class ProductCategory(Base):
    """Canonical product category shared by every SKU-based operational table."""
    __tablename__ = "product_categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    slug: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    description: Mapped[str] = mapped_column(String(240), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class Outlet(Base):
    __tablename__ = "outlets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    region: Mapped[str] = mapped_column(String(80), default="Dhaka")
    manager_name: Mapped[str] = mapped_column(String(120), default="")

    sales: Mapped[list["SalesRecord"]] = relationship(back_populates="outlet")
    inventory: Mapped[list["InventoryItem"]] = relationship(back_populates="outlet")
    roster: Mapped[list["ManpowerRoster"]] = relationship(back_populates="outlet")
    complaints: Mapped[list["Complaint"]] = relationship(back_populates="outlet")
    tasks: Mapped[list["Task"]] = relationship(back_populates="outlet")
    alerts: Mapped[list["Alert"]] = relationship(back_populates="outlet")
    inventory_movements: Mapped[list["InventoryMovement"]] = relationship(back_populates="outlet")
    stock_out_events: Mapped[list["StockOutEvent"]] = relationship(back_populates="outlet")
    promotions: Mapped[list["PromotionCampaign"]] = relationship(back_populates="outlet")
    deliveries: Mapped[list["DeliverySchedule"]] = relationship(back_populates="outlet")
    seasonal_events: Mapped[list["SeasonalEvent"]] = relationship(back_populates="outlet")
    audit_reports: Mapped[list["StoreAuditReport"]] = relationship(back_populates="outlet")
    manual_issues: Mapped[list["ManualIssue"]] = relationship(back_populates="outlet")
    demand_forecasts: Mapped[list["DemandForecast"]] = relationship(back_populates="outlet")
    ai_recommendations: Mapped[list["AiRecommendationAudit"]] = relationship(back_populates="outlet")
    sales_targets: Mapped[list["OutletSalesTarget"]] = relationship(back_populates="outlet")


class SalesRecord(Base):
    """Daily sales by SKU/category, used for demand forecasting & footfall trend."""
    __tablename__ = "sales_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    outlet_id: Mapped[int] = mapped_column(ForeignKey("outlets.id"))
    sku: Mapped[str] = mapped_column(String(60))
    category: Mapped[str] = mapped_column(String(60))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("product_categories.id"), index=True)
    date: Mapped[dt.date] = mapped_column()
    units_sold: Mapped[int] = mapped_column(Integer)
    revenue: Mapped[float] = mapped_column(Float)
    footfall: Mapped[int] = mapped_column(Integer, default=0)
    is_festival_period: Mapped[bool] = mapped_column(Boolean, default=False)

    outlet: Mapped["Outlet"] = relationship(back_populates="sales")
    product_category: Mapped["ProductCategory | None"] = relationship()


class OutletSalesTarget(Base):
    """Monthly outlet sales target split automatically into weekly and daily targets."""
    __tablename__ = "outlet_sales_targets"

    id: Mapped[int] = mapped_column(primary_key=True)
    outlet_id: Mapped[int] = mapped_column(ForeignKey("outlets.id"), index=True)
    year: Mapped[int] = mapped_column(Integer, index=True)
    month: Mapped[int] = mapped_column(Integer, index=True)
    monthly_target: Mapped[float] = mapped_column(Float)
    weekly_target: Mapped[float] = mapped_column(Float)
    daily_target: Mapped[float] = mapped_column(Float)
    weeks_in_month: Mapped[int] = mapped_column(Integer, default=4)
    days_in_month: Mapped[int] = mapped_column(Integer, default=30)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    outlet: Mapped["Outlet"] = relationship(back_populates="sales_targets")


class InventoryItem(Base):
    """Current stock & movement per SKU per outlet."""
    __tablename__ = "inventory_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    outlet_id: Mapped[int] = mapped_column(ForeignKey("outlets.id"))
    sku: Mapped[str] = mapped_column(String(60))
    category: Mapped[str] = mapped_column(String(60))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("product_categories.id"), index=True)
    on_hand_units: Mapped[int] = mapped_column(Integer)
    reorder_point: Mapped[int] = mapped_column(Integer, default=20)
    avg_daily_sales: Mapped[float] = mapped_column(Float, default=0.0)
    next_delivery_date: Mapped[dt.date | None] = mapped_column(nullable=True)
    last_stock_out_date: Mapped[dt.date | None] = mapped_column(nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    outlet: Mapped["Outlet"] = relationship(back_populates="inventory")
    product_category: Mapped["ProductCategory | None"] = relationship()


class InventoryMovementType(str, enum.Enum):
    RECEIPT = "receipt"
    SALE = "sale"
    ADJUSTMENT = "adjustment"
    TRANSFER_IN = "transfer_in"
    TRANSFER_OUT = "transfer_out"
    WASTAGE = "wastage"


class InventoryMovement(Base):
    """SKU-level stock ledger: receipts, sales, adjustments, transfer and wastage."""
    __tablename__ = "inventory_movements"

    id: Mapped[int] = mapped_column(primary_key=True)
    outlet_id: Mapped[int] = mapped_column(ForeignKey("outlets.id"))
    sku: Mapped[str] = mapped_column(String(60))
    category: Mapped[str] = mapped_column(String(60))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("product_categories.id"), index=True)
    movement_type: Mapped[InventoryMovementType] = mapped_column(Enum(InventoryMovementType))
    quantity: Mapped[int] = mapped_column(Integer)
    reference: Mapped[str] = mapped_column(String(120), default="")
    occurred_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    note: Mapped[str] = mapped_column(Text, default="")

    outlet: Mapped["Outlet"] = relationship(back_populates="inventory_movements")
    product_category: Mapped["ProductCategory | None"] = relationship()


class StockOutEvent(Base):
    """Stock-out history by SKU, including duration and lost-sales estimate."""
    __tablename__ = "stock_out_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    outlet_id: Mapped[int] = mapped_column(ForeignKey("outlets.id"))
    sku: Mapped[str] = mapped_column(String(60))
    category: Mapped[str] = mapped_column(String(60))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("product_categories.id"), index=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    estimated_lost_sales: Mapped[float] = mapped_column(Float, default=0.0)
    root_cause: Mapped[str] = mapped_column(String(160), default="")

    outlet: Mapped["Outlet"] = relationship(back_populates="stock_out_events")
    product_category: Mapped["ProductCategory | None"] = relationship()


class ManpowerRoster(Base):
    """Daily roster/attendance per outlet."""
    __tablename__ = "manpower_roster"

    id: Mapped[int] = mapped_column(primary_key=True)
    outlet_id: Mapped[int] = mapped_column(ForeignKey("outlets.id"))
    date: Mapped[dt.date] = mapped_column()
    shift: Mapped[str] = mapped_column(String(20))  # morning/evening/peak
    required_staff: Mapped[int] = mapped_column(Integer)
    present_staff: Mapped[int] = mapped_column(Integer)
    peak_hour_footfall_forecast: Mapped[int] = mapped_column(Integer, default=0)

    outlet: Mapped["Outlet"] = relationship(back_populates="roster")


class ComplaintStatus(str, enum.Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"


class Complaint(Base):
    __tablename__ = "complaints"

    id: Mapped[int] = mapped_column(primary_key=True)
    outlet_id: Mapped[int] = mapped_column(ForeignKey("outlets.id"))
    category: Mapped[str] = mapped_column(String(60))  # product, service, billing, cleanliness
    description: Mapped[str] = mapped_column(Text)
    severity: Mapped[int] = mapped_column(Integer, default=2)  # 1-5
    status: Mapped[ComplaintStatus] = mapped_column(Enum(ComplaintStatus), default=ComplaintStatus.OPEN)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    outlet: Mapped["Outlet"] = relationship(back_populates="complaints")


class CampaignStatus(str, enum.Enum):
    PLANNED = "planned"
    ACTIVE = "active"
    COMPLETED = "completed"


class PromotionCampaign(Base):
    """Promotion and campaign calendar per outlet/category/SKU."""
    __tablename__ = "promotion_campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    outlet_id: Mapped[int] = mapped_column(ForeignKey("outlets.id"))
    name: Mapped[str] = mapped_column(String(160))
    category: Mapped[str] = mapped_column(String(60))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("product_categories.id"), index=True)
    sku: Mapped[str | None] = mapped_column(String(60), nullable=True)
    start_date: Mapped[dt.date] = mapped_column()
    end_date: Mapped[dt.date] = mapped_column()
    discount_pct: Mapped[float] = mapped_column(Float, default=0.0)
    expected_uplift_pct: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[CampaignStatus] = mapped_column(Enum(CampaignStatus), default=CampaignStatus.PLANNED)
    owner: Mapped[str] = mapped_column(String(120), default="Commercial Team")

    outlet: Mapped["Outlet"] = relationship(back_populates="promotions")
    product_category: Mapped["ProductCategory | None"] = relationship()


class DeliveryStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    IN_TRANSIT = "in_transit"
    RECEIVED = "received"
    DELAYED = "delayed"


class DeliverySchedule(Base):
    """Supplier/DC delivery schedule with ETA, status and receiving notes."""
    __tablename__ = "delivery_schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    outlet_id: Mapped[int] = mapped_column(ForeignKey("outlets.id"))
    supplier: Mapped[str] = mapped_column(String(120))
    sku: Mapped[str] = mapped_column(String(60))
    category: Mapped[str] = mapped_column(String(60))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("product_categories.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    scheduled_date: Mapped[dt.date] = mapped_column()
    eta_window: Mapped[str] = mapped_column(String(40), default="")
    status: Mapped[DeliveryStatus] = mapped_column(Enum(DeliveryStatus), default=DeliveryStatus.SCHEDULED)
    grn_reference: Mapped[str | None] = mapped_column(String(80), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")

    outlet: Mapped["Outlet"] = relationship(back_populates="deliveries")
    product_category: Mapped["ProductCategory | None"] = relationship()


class SeasonalEvent(Base):
    """Seasonal sales trend markers such as Eid, Puja and Pahela Boishakh."""
    __tablename__ = "seasonal_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    outlet_id: Mapped[int] = mapped_column(ForeignKey("outlets.id"))
    name: Mapped[str] = mapped_column(String(120))
    start_date: Mapped[dt.date] = mapped_column()
    end_date: Mapped[dt.date] = mapped_column()
    category_focus: Mapped[str] = mapped_column(String(120), default="")
    uplift_pct: Mapped[float] = mapped_column(Float, default=0.0)
    notes: Mapped[str] = mapped_column(Text, default="")

    outlet: Mapped["Outlet"] = relationship(back_populates="seasonal_events")


class StoreAuditReport(Base):
    """Store audit result for compliance, hygiene, planogram and process checks."""
    __tablename__ = "store_audit_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    outlet_id: Mapped[int] = mapped_column(ForeignKey("outlets.id"))
    audit_date: Mapped[dt.date] = mapped_column()
    auditor_name: Mapped[str] = mapped_column(String(120))
    score_pct: Mapped[float] = mapped_column(Float)
    hygiene_score: Mapped[float] = mapped_column(Float)
    planogram_score: Mapped[float] = mapped_column(Float)
    cash_process_score: Mapped[float] = mapped_column(Float)
    findings: Mapped[str] = mapped_column(Text, default="")
    corrective_action: Mapped[str] = mapped_column(Text, default="")

    outlet: Mapped["Outlet"] = relationship(back_populates="audit_reports")


class ManualIssueStatus(str, enum.Enum):
    OPEN = "open"
    ASSIGNED = "assigned"
    RESOLVED = "resolved"


class ManualIssue(Base):
    """Manual issue reporting by Outlet Managers."""
    __tablename__ = "manual_issues"

    id: Mapped[int] = mapped_column(primary_key=True)
    outlet_id: Mapped[int] = mapped_column(ForeignKey("outlets.id"))
    title: Mapped[str] = mapped_column(String(180))
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(80))
    severity: Mapped[int] = mapped_column(Integer, default=2)
    status: Mapped[ManualIssueStatus] = mapped_column(Enum(ManualIssueStatus), default=ManualIssueStatus.OPEN)
    reported_by: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    outlet: Mapped["Outlet"] = relationship(back_populates="manual_issues")


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    DISMISSED = "dismissed"


class TaskSource(str, enum.Enum):
    STOCK = "stock"
    MANPOWER = "manpower"
    COMPLAINT = "complaint"
    PROMOTION = "promotion"
    AUDIT = "audit"
    MANUAL = "manual"


class Task(Base):
    """AI-prioritized action items for the outlet manager."""
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    outlet_id: Mapped[int] = mapped_column(ForeignKey("outlets.id"))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[TaskSource] = mapped_column(Enum(TaskSource))
    priority_score: Mapped[float] = mapped_column(Float, default=0.0)  # 0-100, higher = more urgent
    status: Mapped[TaskStatus] = mapped_column(Enum(TaskStatus), default=TaskStatus.PENDING)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    due_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    outlet: Mapped["Outlet"] = relationship(back_populates="tasks")


class AlertSeverity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertType(str, enum.Enum):
    STOCK_OUT_RISK = "stock_out_risk"
    LOW_MANPOWER = "low_manpower"
    OPERATIONAL_DELAY = "operational_delay"
    COMPLAINT_SPIKE = "complaint_spike"
    FESTIVAL_DEMAND = "festival_demand"


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    outlet_id: Mapped[int] = mapped_column(ForeignKey("outlets.id"))
    type: Mapped[AlertType] = mapped_column(Enum(AlertType))
    severity: Mapped[AlertSeverity] = mapped_column(Enum(AlertSeverity))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)

    outlet: Mapped["Outlet"] = relationship(back_populates="alerts")


class ForecastRisk(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class DemandForecast(Base):
    """Production demand forecast output per SKU/date with model inputs and confidence."""
    __tablename__ = "demand_forecasts"

    id: Mapped[int] = mapped_column(primary_key=True)
    outlet_id: Mapped[int] = mapped_column(ForeignKey("outlets.id"), index=True)
    sku: Mapped[str] = mapped_column(String(60), index=True)
    category: Mapped[str] = mapped_column(String(60))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("product_categories.id"), index=True)
    forecast_date: Mapped[dt.date] = mapped_column(index=True)
    predicted_units: Mapped[float] = mapped_column(Float)
    baseline_units: Mapped[float] = mapped_column(Float)
    weather_uplift_pct: Mapped[float] = mapped_column(Float, default=0.0)
    promotion_uplift_pct: Mapped[float] = mapped_column(Float, default=0.0)
    seasonal_uplift_pct: Mapped[float] = mapped_column(Float, default=0.0)
    trend_uplift_pct: Mapped[float] = mapped_column(Float, default=0.0)
    recommended_stock_units: Mapped[int] = mapped_column(Integer)
    current_on_hand_units: Mapped[int] = mapped_column(Integer, default=0)
    inbound_units: Mapped[int] = mapped_column(Integer, default=0)
    projected_gap_units: Mapped[int] = mapped_column(Integer, default=0)
    risk_level: Mapped[ForecastRisk] = mapped_column(Enum(ForecastRisk), default=ForecastRisk.LOW)
    confidence_pct: Mapped[float] = mapped_column(Float, default=70.0)
    model_name: Mapped[str] = mapped_column(String(120), default="weighted-ensemble-v1")
    model_version: Mapped[str] = mapped_column(String(40), default="1.0")
    drivers: Mapped[dict] = mapped_column(JSON, default=dict)
    generated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    outlet: Mapped["Outlet"] = relationship(back_populates="demand_forecasts")
    product_category: Mapped["ProductCategory | None"] = relationship()


class AiRecommendationStatus(str, enum.Enum):
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    ESCALATED = "escalated"
    IMPLEMENTED = "implemented"


class AiRecommendationAudit(Base):
    """Historical audit trail for every AI recommendation and approval decision."""
    __tablename__ = "ai_recommendation_audits"

    id: Mapped[int] = mapped_column(primary_key=True)
    outlet_id: Mapped[int] = mapped_column(ForeignKey("outlets.id"), index=True)
    purpose: Mapped[str] = mapped_column(String(80), index=True)
    generated_by: Mapped[str] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    summary: Mapped[str] = mapped_column(Text)
    actions: Mapped[list] = mapped_column(JSON, default=list)
    context_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[AiRecommendationStatus] = mapped_column(
        Enum(AiRecommendationStatus),
        default=AiRecommendationStatus.PENDING_APPROVAL,
        index=True,
    )
    requested_by: Mapped[str] = mapped_column(String(120), default="Outlet Manager")
    reviewed_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    review_note: Mapped[str] = mapped_column(Text, default="")
    escalated_to: Mapped[str | None] = mapped_column(String(120), nullable=True)
    escalation_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    reviewed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    escalated_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    outlet: Mapped["Outlet"] = relationship(back_populates="ai_recommendations")
