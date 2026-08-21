"""Backend service layer for the customers page: listing and editing existing customers only (new customers are created as part of contract creation)."""
from typing import Optional, Tuple

import pandas as pd
from sqlalchemy import text

from database import execute_query, run_query


def get_customers(search: Optional[str] = None) -> pd.DataFrame:
    """List all customers, optionally filtered by name, phone, or national ID."""
    if search:
        query = text(
            """
            SELECT musteri_id, isim, telefon, email, tc_kimlik_no, kayit_tarihi
            FROM musteriler
            WHERE isim ILIKE :term OR telefon ILIKE :term OR COALESCE(tc_kimlik_no, '') ILIKE :term
            ORDER BY isim ASC;
            """
        )
        return run_query(query, params={"term": f"%{search}%"})
    return run_query(
        "SELECT musteri_id, isim, telefon, email, tc_kimlik_no, kayit_tarihi "
        "FROM musteriler ORDER BY isim ASC;"
    )


def get_customer_detail(customer_id: int) -> Optional[dict]:
    """Return a customer's basic info, combined vehicle/housing contracts, and total payments made."""
    df = run_query(
        text(
            "SELECT musteri_id, isim, telefon, email, tc_kimlik_no, kayit_tarihi "
            "FROM musteriler WHERE musteri_id = :id;"
        ),
        params={"id": customer_id},
    )
    if df is None or df.empty:
        return None
    info = df.iloc[0].to_dict()

    contracts_query = text(
        """
        SELECT sozlesme_no, 'ARAC' AS kategori, baslangic_tarihi, bitis_tarihi,
               total_kira AS toplam_tutar, odenen_toplam_tutar, kalan_borc, sozlesme_durumu
        FROM araba_kiralama_sozlesmeleri WHERE musteri_id = :id
        UNION ALL
        SELECT sozlesme_no, 'EV' AS kategori, baslangic_tarihi, bitis_tarihi,
               total_kira AS toplam_tutar, odenen_toplam_tutar, kalan_borc, sozlesme_durumu
        FROM ev_kiralama_sozlesmeleri WHERE musteri_id = :id
        ORDER BY baslangic_tarihi DESC;
        """
    )
    contracts_df = run_query(contracts_query, params={"id": customer_id})

    payment_df = run_query(
        text("SELECT COALESCE(SUM(odenen_tutar), 0) AS toplam_odeme FROM odemeler WHERE musteri_id = :id;"),
        params={"id": customer_id},
    )
    toplam_odeme = (
        float(payment_df.iloc[0]["toplam_odeme"]) if payment_df is not None and not payment_df.empty else 0.0
    )

    return {
        "musteri": info,
        "sozlesmeler": contracts_df.to_dict(orient="records") if contracts_df is not None else [],
        "toplam_odeme": toplam_odeme,
    }


def update_customer(
    customer_id: int,
    isim: Optional[str],
    telefon: Optional[str],
    email: Optional[str],
    tc_kimlik_no: Optional[str],
) -> Tuple[bool, str]:
    """Update the given fields for a customer; fields left as None are left unchanged."""
    exists = run_query(
        text("SELECT musteri_id FROM musteriler WHERE musteri_id = :id;"), params={"id": customer_id}
    )
    if exists is None or exists.empty:
        return False, "Müşteri bulunamadı."

    set_clauses = []
    params: dict = {"id": customer_id}
    if isim is not None:
        set_clauses.append("isim = :isim")
        params["isim"] = isim
    if telefon is not None:
        set_clauses.append("telefon = :telefon")
        params["telefon"] = telefon
    if email is not None:
        set_clauses.append("email = :email")
        params["email"] = email
    if tc_kimlik_no is not None:
        set_clauses.append("tc_kimlik_no = :tc_kimlik_no")
        params["tc_kimlik_no"] = tc_kimlik_no

    if not set_clauses:
        return True, "Herhangi bir değişiklik gönderilmedi."

    query = text(f"UPDATE musteriler SET {', '.join(set_clauses)} WHERE musteri_id = :id;")
    ok, err = execute_query(query, params=params)
    if not ok:
        return False, f"Müşteri güncellenemedi: {err}"
    return True, "Müşteri bilgileri başarıyla güncellendi."
