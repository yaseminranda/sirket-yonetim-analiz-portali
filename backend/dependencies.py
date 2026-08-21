"""FastAPI dependencies for extracting the current user from a JWT and enforcing role/department-based access control."""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from auth_utils import decode_access_token

bearer_scheme = HTTPBearer()


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> dict:
    """Decodes the bearer token and returns the user payload, or raises 401 if invalid/expired."""
    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Oturum geçersiz veya süresi dolmuş. Lütfen tekrar giriş yapın.",
        )
    return payload


def require_role(*allowed_role_keywords: str):
    """Returns a dependency that allows access only if the user's role or department matches one of the given keywords."""

    def _checker(user: dict = Depends(get_current_user)) -> dict:
        role = str(user.get("role", "")).upper()
        dept = str(user.get("department_id", "")).upper()
        haystack = f"{role} {dept}"
        if not any(keyword.upper() in haystack for keyword in allowed_role_keywords):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Bu işlem için yetkiniz bulunmamaktadır.",
            )
        return user

    return _checker


def require_gm_only(user: dict = Depends(get_current_user)) -> dict:
    """Allows access only to users with the General Manager role."""
    role = str(user.get("role", "")).upper()
    if not any(k in role for k in ["GENEL MÜDÜR", "GENEL MUDUR"]):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Bu sayfaya erişim yetkiniz bulunmamaktadır.")
    return user


def require_gm_with_dept3(user: dict = Depends(get_current_user)) -> dict:
    """Allows access only to General Manager users belonging to department D3."""
    role = str(user.get("role", "")).upper()
    dept = str(user.get("department_id", "")).upper()
    is_gm = any(k in role for k in ["GENEL MÜDÜR", "GENEL MUDUR"]) and dept in ["D3", "3"]
    if not is_gm:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Bu sayfaya erişim yetkiniz bulunmamaktadır.")
    return user


def require_dept_manager_or_gm(*department_codes: str):
    """Returns a dependency allowing access to the company General Manager (D3) or a department manager of the given departments."""

    def _checker(user: dict = Depends(get_current_user)) -> dict:
        role = str(user.get("role", "")).upper()
        dept = str(user.get("department_id", "")).upper()
        is_gm = any(k in role for k in ["GENEL MÜDÜR", "GENEL MUDUR"]) and dept in ["D3", "3"]
        is_dept_mgr = any(k in role for k in ["DEPARTMAN MÜDÜRÜ", "DEPARTMAN MUDURU"]) and dept in department_codes
        if not (is_gm or is_dept_mgr):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Bu sayfaya erişim yetkiniz bulunmamaktadır.")
        return user

    return _checker


ALLOWED_CATEGORY_ALL = "ALL"
ALLOWED_CATEGORY_NONE = "NONE"


def get_allowed_contract_category(user: dict = Depends(get_current_user)) -> str:
    """Determines which contract category ("ALL", "ARAC", "EV", or "NONE") the current user is allowed to view."""
    role = str(user.get("role", "")).upper()
    dept = str(user.get("department_id", "")).upper()
    is_company_gm = any(k in role for k in ["GENEL MÜDÜR", "GENEL MUDUR"]) and dept in ["D3", "3"]
    if is_company_gm:
        return ALLOWED_CATEGORY_ALL
    if dept in ["D1", "1"]:
        return "EV"
    if dept in ["D2", "2"]:
        return "ARAC"
    return ALLOWED_CATEGORY_NONE
