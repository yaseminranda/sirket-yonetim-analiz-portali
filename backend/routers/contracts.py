"""Read-only API endpoints for listing contracts and generating payment/contract documents (Excel invoices, Word contracts)."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from dependencies import ALLOWED_CATEGORY_NONE, get_allowed_contract_category, get_current_user
from services import contract_service

router = APIRouter(prefix="/contracts", tags=["Sözleşmeler"])


@router.get("")
def list_contracts(
    search: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    user: dict = Depends(get_current_user),
    allowed_category: str = Depends(get_allowed_contract_category),
):
    """Returns contracts matching the search/category/status filters, restricted to the user's allowed category."""
    if allowed_category == ALLOWED_CATEGORY_NONE:
        return []
    if allowed_category != "ALL":
        category = allowed_category

    df = contract_service.get_all_contracts(search=search, category=category, status=status)
    if df is None or df.empty:
        return []
    df = df.copy()
    df["baslangic_tarihi"] = df["baslangic_tarihi"].astype(str)
    df["bitis_tarihi"] = df["bitis_tarihi"].astype(str)
    return df.to_dict(orient="records")


@router.get("/payments/{odeme_id}/invoice")
def get_single_payment_invoice(odeme_id: int, user: dict = Depends(get_current_user)):
    """Streams an Excel invoice for a single payment."""
    buffer = contract_service.generate_single_payment_invoice_excel(odeme_id)
    if buffer is None:
        raise HTTPException(status_code=404, detail="Ödeme kaydı bulunamadı.")
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=Makbuz_{odeme_id}.xlsx"},
    )


@router.get("/{sozlesme_no}")
def get_contract(sozlesme_no: str, category: str, user: dict = Depends(get_current_user)):
    """Returns detailed information for a single contract, including its payments."""
    detail = contract_service.get_contract_detail(sozlesme_no, category)
    if detail is None:
        raise HTTPException(status_code=404, detail="Sözleşme bulunamadı.")

    for key in ("baslangic_tarihi", "bitis_tarihi"):
        if detail.get(key) is not None:
            detail[key] = str(detail[key])
    for p in detail.get("odemeler", []):
        if p.get("odeme_tarihi") is not None:
            p["odeme_tarihi"] = str(p["odeme_tarihi"])
    return detail


@router.get("/{sozlesme_no}/bulk-invoice")
def get_bulk_invoice(sozlesme_no: str, category: str, user: dict = Depends(get_current_user)):
    """Streams a consolidated Excel invoice covering all payments of a contract."""
    buffer = contract_service.generate_bulk_invoice_excel(sozlesme_no, category)
    if buffer is None:
        raise HTTPException(status_code=404, detail="Sözleşme bulunamadı.")
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=Toplu_Makbuz_{sozlesme_no}.xlsx"},
    )


@router.get("/{sozlesme_no}/document")
def get_contract_document(sozlesme_no: str, category: str, user: dict = Depends(get_current_user)):
    """Streams the contract text as a generated Word (.docx) document."""
    buffer = contract_service.generate_contract_docx(sozlesme_no, category)
    if buffer is None:
        raise HTTPException(status_code=404, detail="Sözleşme bulunamadı.")
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename=Sozlesme_{sozlesme_no}.docx"},
    )
