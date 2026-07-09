import datetime as dt
from typing import Literal
from pydantic import BaseModel, ConfigDict
from app.models import (
    TaskStatus, TaskSource, AlertSeverity, AlertType, ComplaintStatus,
    InventoryMovementType, CampaignStatus, DeliveryStatus, ManualIssueStatus,
    ForecastRisk, AiRecommendationStatus,
)


class OutletOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    code: str
    region: str
    manager_name: str


class ProductCategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    slug: str
    description: str
    is_active: bool


class SalesOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    outlet_id: int
    sku: str
    category: str
    category_id: int | None
    date: dt.date
    units_sold: int
    revenue: float
    footfall: int
    is_festival_period: bool


class OutletSalesTargetIn(BaseModel):
    outlet_id: int
    year: int
    month: int
    monthly_target: float


class OutletSalesTargetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    outlet_id: int
    year: int
    month: int
    monthly_target: float
    weekly_target: float
    daily_target: float
    weeks_in_month: int
    days_in_month: int
    created_at: dt.datetime
    updated_at: dt.datetime


class SalesTargetProgressOut(BaseModel):
    outlet_id: int
    year: int
    month: int
    monthly_target: float
    weekly_target: float
    daily_target: float
    month_sales: float
    week_sales: float
    today_sales: float
    month_achievement_pct: float
    week_achievement_pct: float
    today_achievement_pct: float
    month_gap: float
    week_gap: float
    today_gap: float
    days_in_month: int
    weeks_in_month: int


class InventoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    outlet_id: int
    sku: str
    category: str
    category_id: int | None
    on_hand_units: int
    reorder_point: int
    avg_daily_sales: float
    next_delivery_date: dt.date | None
    last_stock_out_date: dt.date | None
    days_of_cover: float | None = None
    risk_level: str | None = None


class InventoryMovementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    outlet_id: int
    sku: str
    category: str
    category_id: int | None
    movement_type: InventoryMovementType
    quantity: int
    reference: str
    occurred_at: dt.datetime
    note: str


class StockOutEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    outlet_id: int
    sku: str
    category: str
    category_id: int | None
    started_at: dt.datetime
    resolved_at: dt.datetime | None
    estimated_lost_sales: float
    root_cause: str


class ManpowerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    outlet_id: int
    date: dt.date
    shift: str
    required_staff: int
    present_staff: int
    peak_hour_footfall_forecast: int
    coverage_pct: float | None = None


class ComplaintIn(BaseModel):
    outlet_id: int
    category: str
    description: str
    severity: int = 2


class ComplaintOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    outlet_id: int
    category: str
    description: str
    severity: int
    status: ComplaintStatus
    created_at: dt.datetime


class PromotionCampaignOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    outlet_id: int
    name: str
    category: str
    category_id: int | None
    sku: str | None
    start_date: dt.date
    end_date: dt.date
    discount_pct: float
    expected_uplift_pct: float
    status: CampaignStatus
    owner: str


class DeliveryScheduleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    outlet_id: int
    supplier: str
    sku: str
    category: str
    category_id: int | None
    quantity: int
    scheduled_date: dt.date
    eta_window: str
    status: DeliveryStatus
    grn_reference: str | None
    note: str


class SeasonalEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    outlet_id: int
    name: str
    start_date: dt.date
    end_date: dt.date
    category_focus: str
    uplift_pct: float
    notes: str


class CalendarFestivalOut(BaseModel):
    id: int
    name: str
    start_date: dt.date
    end_date: dt.date
    category_focus: str
    uplift_pct: float
    days_until_start: int
    is_active_today: bool


class BusinessCalendarOut(BaseModel):
    timezone: str
    local_date: dt.date
    local_time: str
    local_datetime: dt.datetime
    lookahead_days: int
    next_festival: CalendarFestivalOut | None
    next_known_festival: CalendarFestivalOut | None
    festivals_next_7_days: list[CalendarFestivalOut]


class StoreAuditReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    outlet_id: int
    audit_date: dt.date
    auditor_name: str
    score_pct: float
    hygiene_score: float
    planogram_score: float
    cash_process_score: float
    findings: str
    corrective_action: str


class ManualIssueIn(BaseModel):
    outlet_id: int
    title: str
    description: str
    category: str
    severity: int = 2
    reported_by: str = "Outlet Manager"


class ManualIssueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    outlet_id: int
    title: str
    description: str
    category: str
    severity: int
    status: ManualIssueStatus
    reported_by: str
    created_at: dt.datetime
    resolved_at: dt.datetime | None


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    outlet_id: int
    title: str
    description: str
    source: TaskSource
    priority_score: float
    status: TaskStatus
    created_at: dt.datetime
    due_at: dt.datetime | None


class TaskUpdate(BaseModel):
    status: TaskStatus


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    outlet_id: int
    type: AlertType
    severity: AlertSeverity
    message: str
    created_at: dt.datetime
    acknowledged: bool


class ChatRequest(BaseModel):
    outlet_id: int
    message: str


class ChatResponse(BaseModel):
    reply: str
    intent: str
    data: dict | None = None


ActionPurpose = Literal[
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
]


class AiActionRequest(BaseModel):
    outlet_id: int
    purpose: ActionPurpose
    instruction: str | None = None


class AiActionItem(BaseModel):
    title: str
    rationale: str
    owner: str = "Outlet Manager"
    urgency: str = "medium"
    due_in_hours: float | None = None
    source: str | None = None


class AiActionResponse(BaseModel):
    recommendation_id: int | None = None
    outlet_id: int
    purpose: ActionPurpose
    generated_by: str
    model: str | None = None
    approval_status: AiRecommendationStatus | None = None
    summary: str
    actions: list[AiActionItem]
    context: dict | None = None


class AiRecommendationAuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    outlet_id: int
    purpose: str
    generated_by: str
    model: str | None
    summary: str
    actions: list
    context_snapshot: dict
    status: AiRecommendationStatus
    requested_by: str
    reviewed_by: str | None
    review_note: str
    escalated_to: str | None
    escalation_reason: str
    created_at: dt.datetime
    reviewed_at: dt.datetime | None
    escalated_at: dt.datetime | None


class AiRecommendationDecisionIn(BaseModel):
    reviewed_by: str = "Outlet Manager"
    note: str = ""


class AiRecommendationEscalateIn(BaseModel):
    escalated_to: str = "Area Manager"
    reason: str = ""


class DemandForecastOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    outlet_id: int
    sku: str
    category: str
    category_id: int | None
    forecast_date: dt.date
    predicted_units: float
    baseline_units: float
    weather_uplift_pct: float
    promotion_uplift_pct: float
    seasonal_uplift_pct: float
    trend_uplift_pct: float
    recommended_stock_units: int
    current_on_hand_units: int
    inbound_units: int
    projected_gap_units: int
    risk_level: ForecastRisk
    confidence_pct: float
    model_name: str
    model_version: str
    drivers: dict
    generated_at: dt.datetime


class ForecastRunResponse(BaseModel):
    outlet_id: int
    horizon_days: int
    model_name: str
    generated_count: int
    forecasts: list[DemandForecastOut]


class ScorecardOut(BaseModel):
    outlet_id: int
    outlet_name: str
    sales_today: float
    sales_current_month: float
    sales_current_year: float
    stock_health_pct: float
    manpower_coverage_pct: float
    open_complaints: int
    critical_alerts: int
    productivity_score: float
