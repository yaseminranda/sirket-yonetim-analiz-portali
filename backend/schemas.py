"""Pydantic request/response schemas for the FastAPI backend (auth, contracts, payments, customers)."""
import re
from datetime import date
from typing import Optional

from pydantic import BaseModel, field_validator

_PHONE_REGEX = re.compile(r"^0\d{10}$")
_EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PASSPORT_REGEX = re.compile(r"^[A-Za-z0-9]{5,20}$")


def _clean_and_validate_phone(value: Optional[str]) -> Optional[str]:
    """Strip separators from a phone number and validate it matches the 11-digit TR format."""
    if value is None or not value.strip():
        return value
    cleaned = re.sub(r"[\s\-()]", "", value)
    if not _PHONE_REGEX.match(cleaned):
        raise ValueError("Telefon numarası 11 haneli olmalı ve 0 ile başlamalıdır (örn. 05551234567).")
    return cleaned


def _validate_email(value: Optional[str]) -> Optional[str]:
    """Validate that a value is a well-formed email address."""
    if value is None or not value.strip():
        return value
    cleaned = value.strip()
    if not _EMAIL_REGEX.match(cleaned):
        raise ValueError("Geçerli bir e-posta adresi giriniz (örn. isim@ornek.com).")
    return cleaned


def _validate_identity_no(value: Optional[str]) -> Optional[str]:
    """Validate a national ID (11-digit TC Kimlik No) or a passport number (5-20 alphanumeric characters)."""
    if value is None or not value.strip():
        return value
    cleaned = value.strip().replace(" ", "")
    if len(cleaned) == 11 and cleaned.isdigit():
        if cleaned[0] == "0":
            raise ValueError("TC Kimlik No 0 ile başlayamaz.")
        return cleaned
    if not _PASSPORT_REGEX.match(cleaned):
        raise ValueError(
            "Geçerli bir TC Kimlik No (11 haneli, 0 ile başlamayan) ya da pasaport numarası "
            "(5-20 karakter, harf/rakam) giriniz."
        )
    return cleaned


class LoginRequest(BaseModel):
    """Request body for the first step of login (employee ID and password)."""
    employee_id: str
    password: str
    remember_me: bool = False


class VerifyCodeRequest(BaseModel):
    """Request body for the second step of 2FA login: the code emailed after password verification."""
    employee_id: str
    code: str
    remember_me: bool = False


class LoginResponse(BaseModel):
    """Response returned after a login attempt, including token and whether 2FA code entry is required."""
    success: bool
    message: str
    access_token: Optional[str] = None
    user_name: Optional[str] = None
    employee_id: Optional[str] = None
    department_id: Optional[str] = None
    role: Optional[str] = None
    requires_code: bool = False


class UserProfileResponse(BaseModel):
    """Response for /auth/me: validates a remembered token and returns the current user's profile."""
    success: bool
    employee_id: Optional[str] = None
    user_name: Optional[str] = None
    department_id: Optional[str] = None
    role: Optional[str] = None


class SecurityQuestionRequestCodeRequest(BaseModel):
    """Step 1 of changing the security question: verify current password and trigger an email code."""
    employee_id: str
    current_password: str


class SecurityQuestionConfirmRequest(BaseModel):
    """Step 2 of changing the security question: re-verify password and email code, then save the change."""
    employee_id: str
    current_password: str
    security_question: str
    security_answer: str
    code: str


class SimpleResponse(BaseModel):
    """Generic success/message response."""
    success: bool
    message: str


class SecurityQuestionResponse(BaseModel):
    """Response containing a user's stored security question, if any."""
    found: bool
    question: Optional[str] = None
    message: Optional[str] = None


class PasswordResetRequestCodeRequest(BaseModel):
    """Step 1 of password reset: verify the security answer and trigger an email code."""
    employee_id: str
    security_answer: str


class PasswordResetConfirmRequest(BaseModel):
    """Step 2 of password reset: re-verify the security answer and email code, then set the new password."""
    employee_id: str
    security_answer: str
    new_password: str
    code: str


class VehicleContractCreate(BaseModel):
    """Request body for creating a vehicle rental contract, including optional new customer and driver details."""
    customer_id: Optional[int] = None
    new_customer_name: Optional[str] = None
    new_customer_phone: Optional[str] = None
    new_customer_email: Optional[str] = None
    new_customer_tc: Optional[str] = None
    vehicle_id: str
    start_date: date
    end_date: date
    down_payment_confirmed: bool = False
    driver1_name: Optional[str] = None
    driver1_phone: Optional[str] = None
    driver1_email: Optional[str] = None
    driver1_tc: Optional[str] = None
    driver2_name: Optional[str] = None
    driver2_phone: Optional[str] = None
    driver2_email: Optional[str] = None
    driver2_tc: Optional[str] = None

    _v_phone = field_validator(
        "new_customer_phone", "driver1_phone", "driver2_phone"
    )(_clean_and_validate_phone)
    _v_email = field_validator(
        "new_customer_email", "driver1_email", "driver2_email"
    )(_validate_email)
    _v_tc = field_validator(
        "new_customer_tc", "driver1_tc", "driver2_tc"
    )(_validate_identity_no)


class VehicleAddRequest(BaseModel):
    """Request body for adding a new vehicle (with optional new brand/model)."""
    marka_id: Optional[str] = None
    new_marka_adi: Optional[str] = None
    model_id: Optional[str] = None
    new_model_adi: Optional[str] = None
    plaka: str
    gunluk_ucret: float


class RetireVehicleRequest(BaseModel):
    """Request body for retiring a vehicle from service."""
    retire_date: date


class VehicleChangeRequest(BaseModel):
    """Request body for swapping the vehicle on an active rental contract."""
    new_vehicle_id: str
    change_date: date
    reason: str = ""


class ContractExtendRequest(BaseModel):
    """Request body for extending a vehicle or housing rental contract."""
    new_end_date: date


class HousingContractCreate(BaseModel):
    """Request body for creating a housing rental contract, including optional new customer and payment plan."""
    customer_id: Optional[int] = None
    new_customer_name: Optional[str] = None
    new_customer_phone: Optional[str] = None
    new_customer_email: Optional[str] = None
    new_customer_tc: Optional[str] = None
    apartment_id: str
    start_date: date
    end_date: date
    deposit_amount: float = 0.0
    plan_type: str = "SURE_BAZLI"
    installment_amount: Optional[float] = None

    _v_phone = field_validator("new_customer_phone")(_clean_and_validate_phone)
    _v_email = field_validator("new_customer_email")(_validate_email)
    _v_tc = field_validator("new_customer_tc")(_validate_identity_no)


class InstallmentPaymentCreate(BaseModel):
    """Request body for recording an installment payment on a housing contract."""
    customer_id: int
    amount_paid: float
    doviz_cinsi: str = "TRY"
    odeme_yontemi: str = "NAKİT"
    description: str = ""
    tam_kapat: bool = False


class HousingCompleteRequest(BaseModel):
    """Request body for completing a housing contract, optionally recording damage cost."""
    damage_cost: float = 0.0


class ApartmentAddRequest(BaseModel):
    """Request body for adding a new apartment unit (with optional new building/location)."""
    apartman_id: Optional[str] = None
    new_apartman_adi: Optional[str] = None
    new_il: Optional[str] = None
    new_ilce: Optional[str] = None
    new_mahalle: Optional[str] = None
    daire_no: str
    oda_sayisi: str
    metrekare: Optional[float] = None
    aylik_kira: float


class RetireApartmentRequest(BaseModel):
    """Request body for retiring an apartment from availability."""
    retire_date: date


def _validate_positive_amount(value: float) -> float:
    """Reject a payment amount that is zero or negative."""
    if value is None or value <= 0:
        raise ValueError("Ödeme tutarı 0'dan büyük olmalıdır.")
    return value


def _reject_blank_customer_isim(value: Optional[str]) -> Optional[str]:
    """Reject an explicitly provided but blank customer name."""
    if value is not None and not value.strip():
        raise ValueError("Ad Soyad boş bırakılamaz.")
    return value


def _reject_blank_customer_phone(value: Optional[str]) -> Optional[str]:
    """Reject a blank customer phone, otherwise validate its format."""
    if value is not None and not value.strip():
        raise ValueError("Telefon boş bırakılamaz.")
    return _clean_and_validate_phone(value)


def _reject_blank_customer_email(value: Optional[str]) -> Optional[str]:
    """Reject a blank customer email, otherwise validate its format."""
    if value is not None and not value.strip():
        raise ValueError("E-posta boş bırakılamaz.")
    return _validate_email(value)


def _reject_blank_customer_tc(value: Optional[str]) -> Optional[str]:
    """Reject a blank customer ID number, otherwise validate its format."""
    if value is not None and not value.strip():
        raise ValueError("TC Kimlik No boş bırakılamaz.")
    return _validate_identity_no(value)


class CustomerUpdate(BaseModel):
    """Request body for editing an existing customer; fields are optional but reject blank strings if sent."""

    isim: Optional[str] = None
    telefon: Optional[str] = None
    email: Optional[str] = None
    tc_kimlik_no: Optional[str] = None

    _v_isim = field_validator("isim")(_reject_blank_customer_isim)
    _v_phone = field_validator("telefon")(_reject_blank_customer_phone)
    _v_email = field_validator("email")(_reject_blank_customer_email)
    _v_tc = field_validator("tc_kimlik_no")(_reject_blank_customer_tc)


class PaymentCreate(BaseModel):
    """Request body for recording a payment against a vehicle or housing contract."""
    contract_no: str
    category: str
    customer_id: int
    amount_paid: float
    payment_type: str = "KİRA_ODEMESI"
    description: str = ""
    doviz_cinsi: str = "TRY"
    odeme_yontemi: str = "NAKİT"
    tam_kapat: bool = False

    _v_amount = field_validator("amount_paid")(_validate_positive_amount)


class SmsNotificationRequest(BaseModel):
    """Request body for sending an SMS notification."""
    phone_number: str
    message: str
    notification_type: str = "BİLDİRİM"


class CancellationRiskRequest(BaseModel):
    """Request body for the ML-based cancellation risk prediction."""
    category: str
    duration: float
    price: float


class ActionResponse(BaseModel):
    """Generic response for actions that may return contract/payment amounts alongside success status."""
    success: bool
    message: str
    contract_no: Optional[str] = None
    total_price: Optional[float] = None
    down_payment: Optional[float] = None
    deposit_amount: Optional[float] = None
