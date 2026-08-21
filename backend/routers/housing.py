"""Housing rental endpoints: customers, apartment/unit management, contracts, installments, reminders, and analytics."""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends

from dependencies import require_dept_manager_or_gm, require_role
from schemas import (
    ActionResponse,
    ApartmentAddRequest,
    ContractExtendRequest,
    HousingCompleteRequest,
    HousingContractCreate,
    InstallmentPaymentCreate,
    RetireApartmentRequest,
)
from services import analytics_service, housing_service

router = APIRouter(prefix="/housing", tags=["Ev Kiralama"])

can_manage_housing = require_role("GENEL MÜDÜR", "GENEL MUDUR", "D1")
can_view_housing_analysis = require_dept_manager_or_gm("D1", "1")


@router.get("/customers")
def get_customers(user: dict = Depends(can_manage_housing)):
    """Return the list of housing rental customers."""
    df = housing_service.get_customers()
    return df.to_dict(orient="records") if df is not None else []


@router.get("/available")
def get_available_apartments(start_date: date, end_date: date, user: dict = Depends(can_manage_housing)):
    """Return apartments available for rental within the given date range."""
    df = housing_service.get_available_apartments(start_date, end_date)
    return df.to_dict(orient="records") if df is not None else []


@router.get("/contracts")
def get_all_contracts(user: dict = Depends(can_manage_housing)):
    """Return all housing rental contracts."""
    df = housing_service.get_all_contracts()
    if df is None or df.empty:
        return []
    df = df.copy()
    for col in ["baslangic_tarihi", "bitis_tarihi"]:
        df[col] = df[col].astype(str)
    return df.to_dict(orient="records")


@router.get("/contracts/overdue-rent")
def get_overdue_rent(user: dict = Depends(can_manage_housing)):
    """Return housing contracts with overdue rent payments."""
    df = housing_service.get_overdue_rent_contracts()
    if df is None or df.empty:
        return []
    df = df.copy()
    df["baslangic_tarihi"] = df["baslangic_tarihi"].astype(str)
    return df.to_dict(orient="records")


@router.get("/contracts/expiring")
def get_expiring_contracts(user: dict = Depends(can_manage_housing)):
    """Return housing contracts expiring within the next 30 days."""
    df = housing_service.get_expiring_contracts()
    if df is None or df.empty:
        return []
    df = df.copy()
    for col in ["baslangic_tarihi", "bitis_tarihi"]:
        df[col] = df[col].astype(str)
    return df.to_dict(orient="records")


@router.post("/contracts/{contract_no}/notify-expiry", response_model=ActionResponse)
def notify_expiry(contract_no: str, method: str = "email", user: dict = Depends(can_manage_housing)):
    """Send a contract expiry reminder for a single housing contract."""
    ok, message = housing_service.send_contract_expiry_reminder(
        contract_no, method=method, employee_id=user.get("sub", ""), department_id=user.get("department_id", "D1")
    )
    return ActionResponse(success=ok, message=message)


@router.post("/contracts/notify-expiry/send-all", response_model=ActionResponse)
def notify_expiry_all(method: str = "email", user: dict = Depends(can_manage_housing)):
    """Send expiry reminders to all housing contracts expiring within 30 days."""
    success_count, fail_count, first_error = housing_service.send_bulk_contract_expiry_reminders(
        user.get("sub", ""), user.get("department_id", "D1"), method=method
    )
    if success_count == 0 and fail_count == 0:
        return ActionResponse(success=True, message="Bitişine 30 gün veya daha az kalan sözleşme bulunamadı.")
    if success_count == 0:
        return ActionResponse(success=False, message=first_error or "Hiçbir hatırlatma gönderilemedi.")
    message = f"{success_count} adet sözleşmeye bitiş hatırlatması gönderildi."
    if fail_count:
        message += f" {fail_count} adedi gönderilemedi ({first_error})."
    return ActionResponse(success=True, message=message)


@router.post("/contracts/{contract_no}/notify-overdue", response_model=ActionResponse)
def notify_overdue(contract_no: str, method: str = "email", user: dict = Depends(can_manage_housing)):
    """Send a vacate notice for a housing contract that ended but wasn't closed out."""
    ok, message = housing_service.send_overdue_vacate_notice(
        contract_no, method=method, employee_id=user.get("sub", ""), department_id=user.get("department_id", "D1")
    )
    return ActionResponse(success=ok, message=message)


@router.post("/contracts/{contract_no}/notify-overdue-rent", response_model=ActionResponse)
def notify_overdue_rent(contract_no: str, method: str = "email", user: dict = Depends(can_manage_housing)):
    """Send a notice for an overdue rent payment on a housing contract."""
    ok, message = housing_service.send_overdue_rent_notice(
        contract_no, method=method, employee_id=user.get("sub", ""), department_id=user.get("department_id", "D1")
    )
    return ActionResponse(success=ok, message=message)


@router.post("/contracts", response_model=ActionResponse)
def create_contract(payload: HousingContractCreate, user: dict = Depends(can_manage_housing)):
    """Create a new housing rental contract, optionally registering a new customer and an installment plan."""
    result = housing_service.create_contract(
        customer_id=payload.customer_id,
        new_customer_name=payload.new_customer_name,
        new_customer_phone=payload.new_customer_phone,
        new_customer_email=payload.new_customer_email,
        new_customer_tc=payload.new_customer_tc,
        apartment_id=payload.apartment_id,
        start_date=payload.start_date,
        end_date=payload.end_date,
        deposit_amount=payload.deposit_amount,
        employee_id=user.get("sub", ""),
        department_id=user.get("department_id", "D1"),
        plan_type=payload.plan_type,
        installment_amount=payload.installment_amount,
    )
    return ActionResponse(**result)


@router.get("/contracts/{contract_no}/installments")
def get_installments(contract_no: str, user: dict = Depends(can_manage_housing)):
    """Return the installment payment plan for a housing contract."""
    df = housing_service.get_payment_plan(contract_no)
    if df is None or df.empty:
        return []
    df = df.copy()
    df["planlanan_tarih"] = df["planlanan_tarih"].astype(str)
    return df.to_dict(orient="records")


@router.post("/contracts/{contract_no}/installments/{taksit_id}/pay", response_model=ActionResponse)
def pay_installment(contract_no: str, taksit_id: int, payload: InstallmentPaymentCreate, user: dict = Depends(can_manage_housing)):
    """Record a payment against a specific installment of a housing contract's payment plan."""
    result = housing_service.pay_installment(
        contract_no=contract_no,
        taksit_id=taksit_id,
        customer_id=payload.customer_id,
        amount_paid=payload.amount_paid,
        doviz_cinsi=payload.doviz_cinsi,
        odeme_yontemi=payload.odeme_yontemi,
        description=payload.description,
        employee_id=user.get("sub", ""),
        department_id=user.get("department_id", "D1"),
        tam_kapat=payload.tam_kapat,
    )
    return ActionResponse(**result)


@router.get("/buildings")
def get_buildings(user: dict = Depends(can_manage_housing)):
    """Return the list of apartment buildings."""
    df = housing_service.get_apartment_buildings()
    return df.to_dict(orient="records") if df is not None else []


@router.get("/units")
def get_units(user: dict = Depends(can_manage_housing)):
    """Return all apartment units, active and retired."""
    df = housing_service.get_all_units()
    if df is None or df.empty:
        return []
    df = df.copy()
    for col in ["sisteme_ekleme_tarihi", "pasif_tarihi"]:
        df[col] = df[col].astype(str)
    return df.to_dict(orient="records")


@router.post("/units", response_model=ActionResponse)
def add_apartment(payload: ApartmentAddRequest, user: dict = Depends(can_manage_housing)):
    """Add a new apartment unit, optionally creating a new building."""
    result = housing_service.add_apartment(
        apartman_id=payload.apartman_id,
        new_apartman_adi=payload.new_apartman_adi,
        new_il=payload.new_il,
        new_ilce=payload.new_ilce,
        new_mahalle=payload.new_mahalle,
        daire_no=payload.daire_no,
        oda_sayisi=payload.oda_sayisi,
        aylik_kira=payload.aylik_kira,
        employee_id=user.get("sub", ""),
        department_id=user.get("department_id", "D1"),
    )
    return ActionResponse(**result)


@router.get("/units-count")
def get_units_count(as_of_date: Optional[date] = None, user: dict = Depends(can_manage_housing)):
    """Return the count of available and occupied apartment units as of a given date."""
    return {
        "sayi": housing_service.get_available_apartment_count(as_of_date),
        "dolu": housing_service.get_occupied_apartment_count(as_of_date),
    }


@router.post("/units/{daire_id}/retire", response_model=ActionResponse)
def retire_apartment(daire_id: str, payload: RetireApartmentRequest, user: dict = Depends(can_manage_housing)):
    """Retire an apartment unit as of the given date."""
    result = housing_service.retire_apartment(
        daire_id, payload.retire_date, user.get("sub", ""), user.get("department_id", "D1")
    )
    return ActionResponse(**result)


@router.post("/contracts/{contract_no}/complete", response_model=ActionResponse)
def complete_contract(contract_no: str, payload: HousingCompleteRequest, user: dict = Depends(can_manage_housing)):
    """Mark a housing contract as completed, applying any damage cost deduction."""
    result = housing_service.complete_contract(
        contract_no, payload.damage_cost, user.get("sub", ""), user.get("department_id", "D1")
    )
    return ActionResponse(**result)


@router.post("/contracts/{contract_no}/cancel", response_model=ActionResponse)
def cancel_contract(contract_no: str, user: dict = Depends(can_manage_housing)):
    """Cancel a housing rental contract."""
    result = housing_service.cancel_contract(contract_no, user.get("sub", ""), user.get("department_id", "D1"))
    return ActionResponse(**result)


@router.post("/contracts/{contract_no}/extend", response_model=ActionResponse)
def extend_contract(contract_no: str, payload: ContractExtendRequest, user: dict = Depends(can_manage_housing)):
    """Extend the end date of an active housing rental contract."""
    result = housing_service.extend_contract(
        contract_no, payload.new_end_date, user.get("sub", ""), user.get("department_id", "D1")
    )
    return ActionResponse(**result)


@router.post("/contracts/{contract_no}/confirm-vacate", response_model=ActionResponse)
def confirm_vacate(contract_no: str, user: dict = Depends(can_manage_housing)):
    """Confirm that a tenant has vacated a housing contract's apartment."""
    result = housing_service.confirm_move_out(contract_no, user.get("sub", ""), user.get("department_id", "D1"))
    return ActionResponse(**result)


@router.get("/analysis")
def get_analysis_data(user: dict = Depends(can_view_housing_analysis)):
    """Return housing rental analytics data along with apartment and employee counts."""
    df = housing_service.load_analysis_data()
    if df is None or df.empty:
        return {"rows": [], "total_apartments": 0, "total_employees": 0}
    df = df.copy()
    for col in ["start_date", "end_date"]:
        df[col] = df[col].astype(str)
    return {
        "rows": df.to_dict(orient="records"),
        "total_apartments": housing_service.get_available_apartment_count(),
        "total_employees": housing_service.get_department_employee_count(),
    }


@router.get("/daily-revenue")
def get_daily_revenue(start_date: date, end_date: date, user: dict = Depends(can_view_housing_analysis)):
    """Return daily revenue and occupancy rate data for the housing analytics chart."""
    return {"rows": analytics_service.get_daily_revenue_and_occupancy("EV", start_date, end_date)}


@router.get("/logs/login")
def get_login_logs(user: dict = Depends(can_view_housing_analysis)):
    """Return login activity logs."""
    df = housing_service.load_login_logs()
    if df is None or df.empty:
        return []
    df = df.copy()
    df["log_time"] = df["log_time"].astype(str)
    return df.to_dict(orient="records")


@router.get("/logs/transactions")
def get_transaction_logs(user: dict = Depends(can_view_housing_analysis)):
    """Return transaction activity logs."""
    df = housing_service.load_transaction_logs()
    if df is None or df.empty:
        return []
    df = df.copy()
    df["log_time"] = df["log_time"].astype(str)
    return df.to_dict(orient="records")
