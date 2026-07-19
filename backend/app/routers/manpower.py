import datetime as dt
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Employee, EmployeeAttendance, ManpowerRoster
from app.schemas import EmployeeAttendanceOut, EmployeeAttendanceSummaryOut, EmployeeOut, ManpowerOut
from app.services.attendance import attendance_summary, predict_peak_context

router = APIRouter(prefix="/api/manpower", tags=["manpower"])


@router.get("", response_model=list[ManpowerOut])
async def list_roster(outlet_id: int, date: dt.date | None = None, db: AsyncSession = Depends(get_db)):
    target_date = date or dt.date.today()
    rows = (await db.execute(
        select(ManpowerRoster).where(
            ManpowerRoster.outlet_id == outlet_id, ManpowerRoster.date == target_date
        )
    )).scalars().all()

    out = []
    for r in rows:
        item = ManpowerOut.model_validate(r)
        item.coverage_pct = round(100 * r.present_staff / r.required_staff, 1) if r.required_staff else None
        out.append(item)
    return out


@router.get("/employees", response_model=list[EmployeeOut])
async def list_employees(outlet_id: int, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(Employee).where(Employee.outlet_id == outlet_id).order_by(
            Employee.name,
        )
    )).scalars().all()
    return rows


@router.get("/attendance/summary", response_model=EmployeeAttendanceSummaryOut)
async def get_employee_attendance_summary(
    outlet_id: int,
    date: dt.date | None = None,
    db: AsyncSession = Depends(get_db),
):
    target_date = date or dt.date.today()
    return await attendance_summary(db, outlet_id, target_date)


@router.get("/attendance", response_model=list[EmployeeAttendanceOut])
async def list_employee_attendance(
    outlet_id: int,
    date: dt.date | None = None,
    db: AsyncSession = Depends(get_db),
):
    target_date = date or dt.date.today()
    rows = (await db.execute(
        select(EmployeeAttendance, Employee).join(Employee).where(
            Employee.outlet_id == outlet_id,
            EmployeeAttendance.attendance_date == target_date,
        ).order_by(Employee.name)
    )).all()

    response = []
    for attendance, employee in rows:
        response.append(EmployeeAttendanceOut(
            attendance_id=attendance.id,
            employee_id=employee.id,
            outlet_id=employee.outlet_id,
            employee_code=employee.employee_code,
            employee_name=employee.name,
            email=employee.email,
            phone=employee.phone,
            designation=employee.designation,
            attendance_date=attendance.attendance_date,
            check_in_at=attendance.check_in_at,
            check_out_at=attendance.check_out_at,
            status=attendance.status,
            working_hours=attendance.working_hours,
            remarks=attendance.remarks,
            created_at=attendance.created_at,
            updated_at=attendance.updated_at,
        ))
    return response


@router.get("/optimize")
async def optimize_shifts(outlet_id: int, db: AsyncSession = Depends(get_db)):
    """
    Predict today's peak window from recent outlet footfall and active campaigns,
    then recommend outlet-level coverage using actual daily attendance.
    """
    today = dt.date.today()
    rows = (await db.execute(
        select(ManpowerRoster).where(ManpowerRoster.outlet_id == outlet_id, ManpowerRoster.date == today)
    )).scalars().all()
    attendance = await attendance_summary(db, outlet_id, today)
    peak_prediction = await predict_peak_context(db, outlet_id, today)
    predicted_daily_footfall = peak_prediction["predicted_daily_footfall"]
    if predicted_daily_footfall <= 0:
        predicted_daily_footfall = max((r.peak_hour_footfall_forecast for r in rows), default=650)
        peak_prediction["predicted_daily_footfall"] = predicted_daily_footfall

    peak_window = peak_prediction["peak_window"]
    predicted_peak_footfall = int(round(predicted_daily_footfall * peak_prediction["peak_demand_share"]))
    staff_capacity_per_peak_window = 65
    recommended_staff = max(1, -(-predicted_peak_footfall // staff_capacity_per_peak_window))
    available_staff = attendance["available_staff"]
    staff_gap = max(0, recommended_staff - available_staff)
    footfall_per_available_staff = round(predicted_peak_footfall / available_staff, 1) if available_staff else None
    staffing = {
        "available_staff": available_staff,
        "recommended_on_floor_staff": recommended_staff,
        "staff_gap": staff_gap,
        "predicted_peak_footfall": predicted_peak_footfall,
        "footfall_per_available_staff": footfall_per_available_staff,
        "capacity_per_staff": staff_capacity_per_peak_window,
    }
    if staff_gap:
        recommendation = (
            f"Predicted peak is {peak_window}. Arrange {staff_gap} additional available or cross-trained employee(s) "
            f"for customer-facing work; {available_staff} are available from today's attendance."
        )
    else:
        recommendation = (
            f"Predicted peak is {peak_window}. Keep at least {recommended_staff} available employees on customer-facing work; "
            f"today's attendance has {available_staff} available."
        )

    return {
        "peak_prediction": {
            **peak_prediction,
            "attendance_pct": attendance["attendance_pct"],
        },
        "attendance_summary": attendance,
        "staffing": staffing,
        "recommendation": recommendation,
    }
