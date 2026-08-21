"""Payment recording, exchange-rate lookup, invoicing, and payment/expiry reminder endpoints."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from dependencies import (
    ALLOWED_CATEGORY_NONE,
    get_allowed_contract_category,
    get_current_user,
    require_gm_only,
)
from schemas import ActionResponse, PaymentCreate, SmsNotificationRequest
from services import exchange_rate_service, finance_service
from services.notification_service import send_sms_notification

router = APIRouter(prefix="/finance", tags=["Ödeme / Finans"])


@router.post("/payments", response_model=ActionResponse)
def create_payment(
    payload: PaymentCreate,
    user: dict = Depends(get_current_user),
    allowed_category: str = Depends(get_allowed_contract_category),
):
    """Record a payment against a contract and return a success or failure message."""
    if allowed_category not in ("ALL", payload.category):
        raise HTTPException(status_code=403, detail="Bu departmana ait olmayan bir sözleşmeye ödeme kaydedemezsiniz.")
    ok, err = finance_service.record_payment(
        contract_no=payload.contract_no,
        category=payload.category,
        customer_id=payload.customer_id,
        amount_paid=payload.amount_paid,
        payment_type=payload.payment_type,
        description=payload.description,
        doviz_cinsi=payload.doviz_cinsi,
        odeme_yontemi=payload.odeme_yontemi,
        tam_kapat=payload.tam_kapat,
    )
    if ok:
        birim = payload.doviz_cinsi if payload.doviz_cinsi != "TRY" else "₺"
        tutar_str = f"{payload.amount_paid:,.2f} {birim}" if birim != "₺" else f"₺{payload.amount_paid:,.2f}"
        return ActionResponse(success=True, message=f"{tutar_str} tutarındaki ödeme başarıyla kaydedildi!")
    return ActionResponse(success=False, message=f"Ödeme kaydedilemedi: {err}")


@router.get("/exchange-rate")
def get_exchange_rate_preview(currency: str, user: dict = Depends(get_current_user)):
    """Return today's preview exchange rate for a currency (final rate is recalculated when a payment is recorded)."""
    return exchange_rate_service.get_exchange_rate(date.today(), currency)


@router.get("/payments/{contract_no}")
def get_payment_history(contract_no: str, user: dict = Depends(get_current_user)):
    """Return the payment history for a contract as a list of records."""
    df = finance_service.get_contract_payment_history(contract_no)
    if df is None or df.empty:
        return []
    df = df.copy()
    df["Ödeme Tarihi"] = df["Ödeme Tarihi"].astype(str)
    return df.to_dict(orient="records")


@router.get("/payment-breakdown")
def get_payment_breakdown(category: str, user: dict = Depends(get_current_user)):
    """Return payment breakdown statistics for a contract category."""
    return finance_service.get_payment_breakdown(category)


@router.get("/debt/{contract_no}")
def get_debt_status(contract_no: str, category: str, user: dict = Depends(get_current_user)):
    """Return whether a contract has outstanding debt and the remaining amount."""
    has_debt, remaining = finance_service.check_debt_status(contract_no, category)
    return {"has_debt": has_debt, "remaining": remaining}


@router.get("/invoice/{contract_no}")
def get_invoice(
    contract_no: str,
    customer_name: str,
    amount: float,
    transaction_type: str,
    remaining_balance: float,
    user: dict = Depends(get_current_user),
):
    """Generate and stream an Excel invoice file for a contract payment."""
    buffer = finance_service.generate_invoice_excel(contract_no, customer_name, amount, transaction_type, remaining_balance)
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=Fatura_{contract_no}.xlsx"},
    )


@router.post("/notify-sms", response_model=ActionResponse)
def notify_sms(payload: SmsNotificationRequest, user: dict = Depends(get_current_user)):
    """Send an SMS notification to a phone number and return the result."""
    ok, err = send_sms_notification(payload.phone_number, payload.message, payload.notification_type)
    if ok:
        return ActionResponse(success=True, message="SMS başarıyla gönderildi.")
    return ActionResponse(success=False, message=err or "SMS gönderilemedi.")


@router.get("/reminders")
def get_reminders(
    user: dict = Depends(get_current_user),
    allowed_category: str = Depends(get_allowed_contract_category),
):
    """Return contracts eligible for a payment reminder, scoped to the caller's allowed department category."""
    if allowed_category == ALLOWED_CATEGORY_NONE:
        return []
    df = finance_service.get_reminder_candidates(category=None if allowed_category == "ALL" else allowed_category)
    if df is None or df.empty:
        return []
    df = df.copy()
    df["vade_tarihi"] = df["vade_tarihi"].astype(str)
    return df.to_dict(orient="records")


@router.post("/reminders/send/{sozlesme_no}", response_model=ActionResponse)
def send_single_reminder(
    sozlesme_no: str,
    category: str,
    method: str = "sms",
    user: dict = Depends(get_current_user),
    allowed_category: str = Depends(get_allowed_contract_category),
):
    """Send a single payment reminder for a contract, rejecting requests outside the caller's department."""
    if allowed_category not in ("ALL", category):
        raise HTTPException(status_code=403, detail="Bu departmana ait olmayan bir sözleşmeye hatırlatma gönderemezsiniz.")
    ok, message = finance_service.send_reminder(
        sozlesme_no, category, user.get("sub", ""), user.get("department_id", ""), method=method
    )
    return ActionResponse(success=ok, message=message)


@router.post("/reminders/send-all", response_model=ActionResponse)
def send_all_reminders(
    method: str = "sms",
    user: dict = Depends(get_current_user),
    allowed_category: str = Depends(get_allowed_contract_category),
):
    """Send payment reminders to all overdue contracts within the caller's allowed department category."""
    if allowed_category == ALLOWED_CATEGORY_NONE:
        return ActionResponse(success=True, message="Hatırlatma gönderilecek borçlu sözleşme bulunamadı.")
    success_count, fail_count, first_error = finance_service.send_bulk_reminders(
        user.get("sub", ""), user.get("department_id", ""), method=method,
        category=None if allowed_category == "ALL" else allowed_category,
    )
    if success_count == 0 and fail_count == 0:
        return ActionResponse(success=True, message="Hatırlatma gönderilecek borçlu sözleşme bulunamadı.")
    if success_count == 0:
        return ActionResponse(success=False, message=first_error or "Hiçbir hatırlatma gönderilemedi.")
    message = f"{success_count} adet sözleşmeye toplu ödeme hatırlatması gönderildi."
    if fail_count:
        message += f" {fail_count} adedi gönderilemedi ({first_error})."
    return ActionResponse(success=True, message=message)


@router.post("/recalculate-debts", response_model=ActionResponse)
def recalculate_debts(user: dict = Depends(require_gm_only)):
    """Recalculate paid/remaining debt totals for all vehicle and housing contracts (GM-only maintenance action)."""
    counts = finance_service.recalculate_all_debt_summaries()
    return ActionResponse(
        success=True,
        message=f"{counts['ARAC']} araç ve {counts['EV']} ev sözleşmesinin borç özeti yeniden hesaplandı.",
    )
