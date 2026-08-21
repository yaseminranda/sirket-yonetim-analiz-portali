"""Vehicle rental endpoints: customers, fleet management, contracts, reminders, and analytics."""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends

from dependencies import require_dept_manager_or_gm, require_role
from schemas import (
    ActionResponse,
    ContractExtendRequest,
    RetireVehicleRequest,
    VehicleAddRequest,
    VehicleChangeRequest,
    VehicleContractCreate,
)
from services import analytics_service, vehicle_service

router = APIRouter(prefix="/vehicles", tags=["Araç Kiralama"])

can_manage_vehicles = require_role("GENEL MÜDÜR", "GENEL MUDUR", "D2")
can_view_vehicle_analysis = require_dept_manager_or_gm("D2", "2")


@router.get("/customers")
def get_customers(user: dict = Depends(can_manage_vehicles)):
    """Return the list of vehicle rental customers."""
    df = vehicle_service.get_customers()
    return df.to_dict(orient="records") if df is not None else []


@router.get("/available")
def get_available_vehicles(start_date: date, end_date: date, user: dict = Depends(can_manage_vehicles)):
    """Return vehicles available for rental within the given date range."""
    df = vehicle_service.get_available_vehicles(start_date, end_date)
    return df.to_dict(orient="records") if df is not None else []


@router.get("/contracts")
def get_all_contracts(user: dict = Depends(can_manage_vehicles)):
    """Return all vehicle rental contracts."""
    df = vehicle_service.get_all_contracts()
    if df is None or df.empty:
        return []
    df = df.copy()
    for col in ["baslangic_tarihi", "bitis_tarihi"]:
        df[col] = df[col].astype(str)
    return df.to_dict(orient="records")


@router.post("/contracts", response_model=ActionResponse)
def create_contract(payload: VehicleContractCreate, user: dict = Depends(can_manage_vehicles)):
    """Create a new vehicle rental contract, optionally registering a new customer and drivers."""
    result = vehicle_service.create_contract(
        customer_id=payload.customer_id,
        new_customer_name=payload.new_customer_name,
        new_customer_phone=payload.new_customer_phone,
        new_customer_email=payload.new_customer_email,
        new_customer_tc=payload.new_customer_tc,
        vehicle_id=payload.vehicle_id,
        start_date=payload.start_date,
        end_date=payload.end_date,
        down_payment_confirmed=payload.down_payment_confirmed,
        employee_id=user.get("sub", ""),
        department_id=user.get("department_id", "D2"),
        driver1_name=payload.driver1_name,
        driver1_phone=payload.driver1_phone,
        driver1_email=payload.driver1_email,
        driver1_tc=payload.driver1_tc,
        driver2_name=payload.driver2_name,
        driver2_phone=payload.driver2_phone,
        driver2_email=payload.driver2_email,
        driver2_tc=payload.driver2_tc,
    )
    return ActionResponse(**result)


@router.get("/brands")
def get_brands(user: dict = Depends(can_manage_vehicles)):
    """Return the list of vehicle brands."""
    df = vehicle_service.get_brands()
    return df.to_dict(orient="records") if df is not None else []


@router.get("/models")
def get_models(marka_id: str, user: dict = Depends(can_manage_vehicles)):
    """Return the vehicle models for a given brand."""
    df = vehicle_service.get_models(marka_id)
    return df.to_dict(orient="records") if df is not None else []


@router.get("/fleet")
def get_fleet(user: dict = Depends(can_manage_vehicles)):
    """Return all vehicles in the fleet, active and retired."""
    df = vehicle_service.get_all_fleet()
    if df is None or df.empty:
        return []
    df = df.copy()
    for col in ["sisteme_ekleme_tarihi", "pasif_tarihi"]:
        df[col] = df[col].astype(str)
    return df.to_dict(orient="records")


@router.post("/fleet", response_model=ActionResponse)
def add_vehicle(payload: VehicleAddRequest, user: dict = Depends(can_manage_vehicles)):
    """Add a new vehicle to the fleet, optionally creating a new brand or model."""
    result = vehicle_service.add_vehicle(
        marka_id=payload.marka_id,
        new_marka_adi=payload.new_marka_adi,
        model_id=payload.model_id,
        new_model_adi=payload.new_model_adi,
        plaka=payload.plaka,
        gunluk_ucret=payload.gunluk_ucret,
        employee_id=user.get("sub", ""),
        department_id=user.get("department_id", "D2"),
    )
    return ActionResponse(**result)


@router.get("/fleet-count")
def get_fleet_count(as_of_date: Optional[date] = None, user: dict = Depends(can_manage_vehicles)):
    """Return the count of available and occupied vehicles as of a given date."""
    return {
        "sayi": vehicle_service.get_available_vehicle_count(as_of_date),
        "dolu": vehicle_service.get_occupied_vehicle_count(as_of_date),
    }


@router.post("/fleet/{arac_id}/retire", response_model=ActionResponse)
def retire_vehicle(arac_id: str, payload: RetireVehicleRequest, user: dict = Depends(can_manage_vehicles)):
    """Retire a vehicle from the fleet as of the given date."""
    result = vehicle_service.retire_vehicle(
        arac_id, payload.retire_date, user.get("sub", ""), user.get("department_id", "D2")
    )
    return ActionResponse(**result)


@router.post("/contracts/{contract_no}/change-vehicle", response_model=ActionResponse)
def change_vehicle(contract_no: str, payload: VehicleChangeRequest, user: dict = Depends(can_manage_vehicles)):
    """Swap the vehicle assigned to an active contract for a different vehicle."""
    result = vehicle_service.change_vehicle(
        contract_no=contract_no,
        new_vehicle_id=payload.new_vehicle_id,
        change_date=payload.change_date,
        reason=payload.reason,
        employee_id=user.get("sub", ""),
        department_id=user.get("department_id", "D2"),
    )
    return ActionResponse(**result)


@router.post("/contracts/{contract_no}/complete", response_model=ActionResponse)
def complete_contract(contract_no: str, user: dict = Depends(can_manage_vehicles)):
    """Mark a vehicle rental contract as completed."""
    result = vehicle_service.complete_contract(contract_no, user.get("sub", ""), user.get("department_id", "D2"))
    return ActionResponse(**result)


@router.post("/contracts/{contract_no}/cancel", response_model=ActionResponse)
def cancel_contract(contract_no: str, user: dict = Depends(can_manage_vehicles)):
    """Cancel a vehicle rental contract."""
    result = vehicle_service.cancel_contract(contract_no, user.get("sub", ""), user.get("department_id", "D2"))
    return ActionResponse(**result)


@router.post("/contracts/{contract_no}/extend", response_model=ActionResponse)
def extend_contract(contract_no: str, payload: ContractExtendRequest, user: dict = Depends(can_manage_vehicles)):
    """Extend the end date of an active vehicle rental contract."""
    result = vehicle_service.extend_contract(
        contract_no, payload.new_end_date, user.get("sub", ""), user.get("department_id", "D2")
    )
    return ActionResponse(**result)


@router.get("/contracts/expiring")
def get_expiring_contracts(user: dict = Depends(can_manage_vehicles)):
    """Return vehicle contracts expiring within the next 0-3 days."""
    df = vehicle_service.get_expiring_contracts()
    if df is None or df.empty:
        return []
    df = df.copy()
    for col in ["baslangic_tarihi", "bitis_tarihi"]:
        df[col] = df[col].astype(str)
    return df.to_dict(orient="records")


@router.post("/contracts/{contract_no}/notify-expiry", response_model=ActionResponse)
def notify_expiry(contract_no: str, method: str = "email", user: dict = Depends(can_manage_vehicles)):
    """Send a contract expiry reminder for a single contract."""
    ok, message = vehicle_service.send_contract_expiry_reminder(
        contract_no, method=method, employee_id=user.get("sub", ""), department_id=user.get("department_id", "D2")
    )
    return ActionResponse(success=ok, message=message)


@router.post("/contracts/notify-expiry/send-all", response_model=ActionResponse)
def notify_expiry_all(method: str = "email", user: dict = Depends(can_manage_vehicles)):
    """Send expiry reminders to all vehicle contracts expiring within 3 days."""
    success_count, fail_count, first_error = vehicle_service.send_bulk_contract_expiry_reminders(
        user.get("sub", ""), user.get("department_id", "D2"), method=method
    )
    if success_count == 0 and fail_count == 0:
        return ActionResponse(success=True, message="Bitişine 3 gün veya daha az kalan sözleşme bulunamadı.")
    if success_count == 0:
        return ActionResponse(success=False, message=first_error or "Hiçbir hatırlatma gönderilemedi.")
    message = f"{success_count} adet sözleşmeye bitiş hatırlatması gönderildi."
    if fail_count:
        message += f" {fail_count} adedi gönderilemedi ({first_error})."
    return ActionResponse(success=True, message=message)


@router.post("/contracts/{contract_no}/notify-overdue", response_model=ActionResponse)
def notify_overdue(contract_no: str, method: str = "email", user: dict = Depends(can_manage_vehicles)):
    """Send an overdue return notice for a vehicle that was not returned by its contract end date."""
    ok, message = vehicle_service.send_overdue_return_notice(
        contract_no, method=method, employee_id=user.get("sub", ""), department_id=user.get("department_id", "D2")
    )
    return ActionResponse(success=ok, message=message)


@router.get("/analysis")
def get_analysis_data(user: dict = Depends(can_view_vehicle_analysis)):
    """Return vehicle rental analytics data along with fleet and employee counts."""
    df = vehicle_service.load_analysis_data()
    if df is None or df.empty:
        return {"rows": [], "total_vehicles": 0, "total_employees": 0}
    df = df.copy()
    for col in ["start_date", "end_date"]:
        df[col] = df[col].astype(str)
    return {
        "rows": df.to_dict(orient="records"),
        "total_vehicles": vehicle_service.get_available_vehicle_count(),
        "total_employees": vehicle_service.get_department_employee_count(),
    }


@router.get("/daily-revenue")
def get_daily_revenue(start_date: date, end_date: date, user: dict = Depends(can_view_vehicle_analysis)):
    """Return daily revenue and occupancy rate data for the vehicle analytics chart."""
    return {"rows": analytics_service.get_daily_revenue_and_occupancy("ARAC", start_date, end_date)}


@router.get("/logs/login")
def get_login_logs(user: dict = Depends(can_view_vehicle_analysis)):
    """Return login activity logs."""
    df = vehicle_service.load_login_logs()
    if df is None or df.empty:
        return []
    df = df.copy()
    df["log_time"] = df["log_time"].astype(str)
    return df.to_dict(orient="records")


@router.get("/logs/transactions")
def get_transaction_logs(user: dict = Depends(can_view_vehicle_analysis)):
    """Return transaction activity logs."""
    df = vehicle_service.load_transaction_logs()
    if df is None or df.empty:
        return []
    df = df.copy()
    df["log_time"] = df["log_time"].astype(str)
    return df.to_dict(orient="records")
