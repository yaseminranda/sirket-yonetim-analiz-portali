"""API endpoints for listing, viewing, and updating customer records, accessible to any authenticated employee."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from dependencies import get_current_user
from schemas import ActionResponse, CustomerUpdate
from services import customer_service

router = APIRouter(prefix="/customers", tags=["Müşteriler"])


@router.get("")
def list_customers(search: Optional[str] = None, user: dict = Depends(get_current_user)):
    """Returns the list of customers, optionally filtered by a search term."""
    df = customer_service.get_customers(search)
    if df is None or df.empty:
        return []
    df = df.copy()
    df["kayit_tarihi"] = df["kayit_tarihi"].astype(str)
    return df.to_dict(orient="records")


@router.get("/{customer_id}")
def get_customer(customer_id: int, user: dict = Depends(get_current_user)):
    """Returns detailed information (profile and contracts) for a single customer."""
    detail = customer_service.get_customer_detail(customer_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Müşteri bulunamadı.")

    if detail["musteri"].get("kayit_tarihi") is not None:
        detail["musteri"]["kayit_tarihi"] = str(detail["musteri"]["kayit_tarihi"])

    for sozlesme in detail["sozlesmeler"]:
        sozlesme["baslangic_tarihi"] = str(sozlesme["baslangic_tarihi"])
        sozlesme["bitis_tarihi"] = str(sozlesme["bitis_tarihi"])

    return detail


@router.put("/{customer_id}", response_model=ActionResponse)
def update_customer(customer_id: int, payload: CustomerUpdate, user: dict = Depends(get_current_user)):
    """Updates a customer's contact and identity information."""
    ok, message = customer_service.update_customer(
        customer_id, payload.isim, payload.telefon, payload.email, payload.tc_kimlik_no
    )
    return ActionResponse(success=ok, message=message)
