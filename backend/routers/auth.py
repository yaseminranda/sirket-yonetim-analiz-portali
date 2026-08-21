"""Authentication endpoints: two-factor login, session lookup, and security-question/password reset flows."""
from datetime import timedelta

from fastapi import APIRouter, Depends

from auth_utils import create_access_token
from config import settings
from dependencies import get_current_user
from schemas import (
    LoginRequest,
    LoginResponse,
    PasswordResetConfirmRequest,
    PasswordResetRequestCodeRequest,
    SecurityQuestionConfirmRequest,
    SecurityQuestionRequestCodeRequest,
    SecurityQuestionResponse,
    SimpleResponse,
    UserProfileResponse,
    VerifyCodeRequest,
)
from services import auth_service

router = APIRouter(prefix="/auth", tags=["Kimlik Doğrulama"])


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest):
    """Validate employee ID and password, then send a one-time email verification code (step 1 of 2FA login)."""
    employee_id = payload.employee_id.strip().upper()
    result = auth_service.authenticate_user(employee_id, payload.password)

    if not result["success"]:
        return LoginResponse(success=False, message=result["message"])

    code_result = auth_service.generate_and_send_login_code(employee_id)
    if not code_result["success"]:
        return LoginResponse(success=False, message=code_result["message"])

    return LoginResponse(
        success=True,
        message=code_result["message"],
        requires_code=True,
        employee_id=employee_id,
    )


@router.post("/verify-code", response_model=LoginResponse)
def verify_code(payload: VerifyCodeRequest):
    """Verify the emailed login code and issue a JWT access token (step 2 of 2FA login)."""
    employee_id = payload.employee_id.strip().upper()
    verify_result = auth_service.verify_login_code(employee_id, payload.code.strip())
    if not verify_result["success"]:
        return LoginResponse(success=False, message=verify_result["message"])

    profile = auth_service.get_employee_profile(employee_id)
    if not profile:
        return LoginResponse(success=False, message="Çalışan bilgileri bulunamadı.")

    expires_delta = timedelta(days=settings.remember_me_expire_days) if payload.remember_me else None
    token = create_access_token(
        {
            "sub": profile["employee_id"],
            "user_name": profile["user_name"],
            "department_id": profile["department_id"],
            "role": profile["role"],
        },
        expires_delta=expires_delta,
    )
    return LoginResponse(
        success=True,
        message=f"Hoş geldiniz, {profile['user_name']}!",
        access_token=token,
        user_name=profile["user_name"],
        employee_id=profile["employee_id"],
        department_id=profile["department_id"],
        role=profile["role"],
    )


@router.get("/me", response_model=UserProfileResponse)
def get_me(user: dict = Depends(get_current_user)):
    """Return the profile of the currently authenticated user, validating the request's JWT."""
    return UserProfileResponse(
        success=True,
        employee_id=user.get("sub", ""),
        user_name=user.get("user_name", ""),
        department_id=user.get("department_id", ""),
        role=user.get("role", ""),
    )


@router.post("/security-question/request-code", response_model=SimpleResponse)
def request_security_question_code(payload: SecurityQuestionRequestCodeRequest):
    """Verify the current password and send a confirmation code to change the security question (step 1)."""
    result = auth_service.request_security_question_code(
        payload.employee_id.strip().upper(), payload.current_password
    )
    return SimpleResponse(**result)


@router.post("/security-question/confirm", response_model=SimpleResponse)
def confirm_security_question_change(payload: SecurityQuestionConfirmRequest):
    """Verify the password and emailed code, then save the new security question/answer (step 2)."""
    result = auth_service.confirm_security_question_change(
        payload.employee_id.strip().upper(),
        payload.current_password,
        payload.security_question,
        payload.security_answer,
        payload.code.strip(),
    )
    return SimpleResponse(**result)


@router.get("/security-question/{employee_id}", response_model=SecurityQuestionResponse)
def get_security_question(employee_id: str):
    """Return the stored security question for a user, used at the start of password reset."""
    result = auth_service.get_security_question(employee_id.strip().upper())
    return SecurityQuestionResponse(**result)


@router.post("/reset-password/request-code", response_model=SimpleResponse)
def request_password_reset_code(payload: PasswordResetRequestCodeRequest):
    """Verify the security answer and send a confirmation code to reset the password (step 1)."""
    result = auth_service.request_password_reset_code(
        payload.employee_id.strip().upper(), payload.security_answer
    )
    return SimpleResponse(**result)


@router.post("/reset-password/confirm", response_model=SimpleResponse)
def confirm_password_reset(payload: PasswordResetConfirmRequest):
    """Verify the security answer and emailed code, then set the new password (step 2)."""
    result = auth_service.confirm_password_reset(
        payload.employee_id.strip().upper(),
        payload.security_answer,
        payload.new_password,
        payload.code.strip(),
    )
    return SimpleResponse(**result)
