"""Finance service: handles payment recording, debt recalculation, invoice generation, and payment reminder dispatch for rental contracts."""
import io
from datetime import date, datetime
from typing import Optional, Tuple

import pandas as pd
from sqlalchemy import text

from database import execute_query, run_query
from services import exchange_rate_service
from services.notification_service import log_transaction, send_payment_reminder_email, send_sms_notification


def _table_for_category(category: str) -> str:
    """Return the contracts table name matching the given rental category."""
    return "ev_kiralama_sozlesmeleri" if category == "EV" else "araba_kiralama_sozlesmeleri"


DEPOSIT_PAYMENT_TYPES_SQL = "('DEPOZITO_TAHSILATI', 'DEPOZITO_IADE', 'HASAR_KESINTISI')"

TAM_KAPAT_ESIK_TL = 5.00


def _recompute_debt_summary(table_name: str, contract_no: str) -> Tuple[bool, str]:
    """Recalculate a contract's paid total, remaining debt, and payment status from the payments ledger."""
    return execute_query(
        text(
            f"""
            UPDATE {table_name}
            SET odenen_toplam_tutar = (
                    SELECT COALESCE(SUM(odenen_tutar), 0) FROM odemeler
                    WHERE sozlesme_no = :contract_no AND UPPER(CAST(odeme_tipi AS VARCHAR)) NOT IN {DEPOSIT_PAYMENT_TYPES_SQL}
                ),
                kalan_borc = total_kira - (
                    SELECT COALESCE(SUM(odenen_tutar), 0) FROM odemeler
                    WHERE sozlesme_no = :contract_no AND UPPER(CAST(odeme_tipi AS VARCHAR)) NOT IN {DEPOSIT_PAYMENT_TYPES_SQL}
                ),
                odeme_durumu = CASE
                    WHEN (total_kira - (
                        SELECT COALESCE(SUM(odenen_tutar), 0) FROM odemeler
                        WHERE sozlesme_no = :contract_no AND UPPER(CAST(odeme_tipi AS VARCHAR)) NOT IN {DEPOSIT_PAYMENT_TYPES_SQL}
                    )) <= 0 THEN 'ÖDENDİ'
                    WHEN (
                        SELECT COALESCE(SUM(odenen_tutar), 0) FROM odemeler
                        WHERE sozlesme_no = :contract_no AND UPPER(CAST(odeme_tipi AS VARCHAR)) NOT IN {DEPOSIT_PAYMENT_TYPES_SQL}
                    ) > 0 THEN 'KISMİ ÖDENDİ'
                    ELSE 'ÖDENMEDİ'
                END
            WHERE sozlesme_no = :contract_no;
            """
        ),
        params={"contract_no": contract_no},
    )


def _apply_tam_kapat_correction(
    table_name: str,
    contract_no: str,
    category: str,
    customer_id: int,
    odeme_yontemi: str,
    taksit_id: Optional[int] = None,
) -> Tuple[bool, str]:
    """Insert a rounding-correction payment to close out a small remaining balance when "full close" is requested."""
    df = run_query(
        text(f"SELECT kalan_borc FROM {table_name} WHERE sozlesme_no = :no;"),
        params={"no": contract_no},
    )
    if df is None or df.empty:
        return True, ""
    remaining = float(df.iloc[0]["kalan_borc"] or 0)
    if not (0 < remaining <= TAM_KAPAT_ESIK_TL):
        return True, ""

    ok, err = execute_query(
        text(
            """
            INSERT INTO odemeler
                (sozlesme_no, kategori, musteri_id, odenen_tutar, odeme_tipi, aciklama,
                 doviz_cinsi, odenen_tutar_doviz, kur, odeme_yontemi, taksit_id)
            VALUES
                (:contract_no, :category, :customer_id, :amount, 'YUVARLAMA_DÜZELTMESİ',
                 'Tam Kapat: döviz/TL çeviriminden kalan kuruş farkının otomatik düzeltmesi.',
                 'TRY', :amount, 1.0, :odeme_yontemi, :taksit_id);
            """
        ),
        params={
            "contract_no": contract_no,
            "category": category,
            "customer_id": customer_id,
            "amount": round(remaining, 2),
            "odeme_yontemi": odeme_yontemi,
            "taksit_id": taksit_id,
        },
    )
    if not ok:
        return False, err or "Yuvarlama düzeltmesi eklenemedi."

    return _recompute_debt_summary(table_name, contract_no)


def record_payment(
    contract_no: str,
    category: str,
    customer_id: int,
    amount_paid: float,
    payment_type: str = "KİRA_ODEMESI",
    description: str = "",
    doviz_cinsi: str = "TRY",
    odeme_yontemi: str = "NAKİT",
    odeme_tarihi: Optional[date] = None,
    taksit_id: Optional[int] = None,
    tam_kapat: bool = False,
) -> Tuple[bool, str]:
    """Record a new payment, converting foreign-currency amounts to TRY, and recompute the contract's debt summary."""
    doviz_cinsi = (doviz_cinsi or "TRY").upper()
    pay_date = odeme_tarihi or date.today()

    if doviz_cinsi != "TRY":
        rate_info = exchange_rate_service.get_exchange_rate(pay_date, doviz_cinsi)
        kur = float(rate_info.get("satis") or 1.0)
        odenen_tutar_doviz = amount_paid
        odenen_tutar_tl = round(amount_paid * kur, 2)
    else:
        kur = 1.0
        odenen_tutar_doviz = amount_paid
        odenen_tutar_tl = amount_paid

    ok, err = execute_query(
        text(
            """
            INSERT INTO odemeler
                (sozlesme_no, kategori, musteri_id, odenen_tutar, odeme_tipi, aciklama,
                 doviz_cinsi, odenen_tutar_doviz, kur, odeme_yontemi, taksit_id)
            VALUES
                (:contract_no, :category, :customer_id, :amount, :ptype, :desc,
                 :doviz_cinsi, :odenen_tutar_doviz, :kur, :odeme_yontemi, :taksit_id);
            """
        ),
        params={
            "contract_no": contract_no,
            "category": category,
            "customer_id": customer_id,
            "amount": odenen_tutar_tl,
            "ptype": payment_type,
            "desc": description,
            "doviz_cinsi": doviz_cinsi,
            "odenen_tutar_doviz": odenen_tutar_doviz,
            "kur": kur,
            "odeme_yontemi": odeme_yontemi,
            "taksit_id": taksit_id,
        },
    )
    if not ok:
        return False, err or "Ödeme eklenemedi."

    table_name = _table_for_category(category)
    ok, err = _recompute_debt_summary(table_name, contract_no)
    if not ok:
        return False, err or ""

    if tam_kapat:
        ok, err = _apply_tam_kapat_correction(
            table_name, contract_no, category, customer_id, odeme_yontemi, taksit_id
        )
        if not ok:
            return False, err or ""

    return True, ""


def reverse_contract_payments(contract_no: str, category: str, customer_id: int) -> Tuple[bool, str]:
    """Insert a reversal payment refunding the amount already paid on a contract that is being cancelled."""
    table_name = _table_for_category(category)
    df = run_query(
        text(f"SELECT odenen_toplam_tutar FROM {table_name} WHERE sozlesme_no = :no;"),
        params={"no": contract_no},
    )
    if df is None or df.empty:
        return False, "Sözleşme bulunamadı."
    already_paid = float(df.iloc[0]["odenen_toplam_tutar"] or 0)
    if already_paid <= 0.01:
        return True, "İade edilecek bir ödeme bulunmuyor."
    return record_payment(
        contract_no=contract_no,
        category=category,
        customer_id=customer_id,
        amount_paid=-already_paid,
        payment_type="İPTAL_İADESİ",
        description="Sözleşme iptali nedeniyle ödenen tutarın ters kaydı (iade).",
    )


def get_contract_payment_history(contract_no: str) -> pd.DataFrame:
    """Return the full payment history for a contract, most recent first."""
    query = text(
        """
        SELECT
            odeme_id AS "Ödeme ID",
            sozlesme_no AS "Sözleşme No",
            odenen_tutar AS "Ödenen Tutar (₺)",
            doviz_cinsi AS "Döviz",
            odenen_tutar_doviz AS "Döviz Tutarı",
            kur AS "Kur",
            odeme_yontemi AS "Ödeme Yöntemi",
            odeme_tarihi AS "Ödeme Tarihi",
            odeme_tipi AS "İşlem Tipi",
            aciklama AS "Açıklama"
        FROM odemeler
        WHERE sozlesme_no = :contract_no
        ORDER BY odeme_tarihi DESC, odeme_id DESC;
        """
    )
    return run_query(query, params={"contract_no": contract_no})


def get_payment_breakdown(category: str) -> dict:
    """Return payment totals grouped by payment method and by currency for a category, excluding negative reversal amounts."""
    method_df = run_query(
        text(
            """
            SELECT odeme_yontemi, COALESCE(SUM(odenen_tutar), 0) AS toplam_tl,
                   COUNT(*) AS odeme_adedi, COUNT(DISTINCT sozlesme_no) AS sozlesme_adedi
            FROM odemeler
            WHERE kategori = :category AND odenen_tutar > 0
            GROUP BY odeme_yontemi
            ORDER BY toplam_tl DESC;
            """
        ),
        params={"category": category},
    )
    currency_df = run_query(
        text(
            """
            SELECT doviz_cinsi, COALESCE(SUM(odenen_tutar), 0) AS toplam_tl,
                   COALESCE(SUM(odenen_tutar_doviz), 0) AS toplam_doviz_miktari,
                   COUNT(*) AS odeme_adedi, COUNT(DISTINCT sozlesme_no) AS sozlesme_adedi
            FROM odemeler
            WHERE kategori = :category AND odenen_tutar > 0
            GROUP BY doviz_cinsi
            ORDER BY toplam_tl DESC;
            """
        ),
        params={"category": category},
    )
    return {
        "by_method": method_df.to_dict(orient="records") if method_df is not None and not method_df.empty else [],
        "by_currency": currency_df.to_dict(orient="records") if currency_df is not None and not currency_df.empty else [],
    }


def generate_invoice_excel(
    contract_no: str, customer_name: str, amount: float, transaction_type: str, remaining_balance: float
) -> io.BytesIO:
    """Build an in-memory Excel invoice/receipt for a single payment transaction."""
    invoice_data = [
        {
            "Fatura No": f"FAC-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "Tarih": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Sözleşme No": contract_no,
            "Müşteri Ad Soyad": customer_name,
            "İşlem Tipi": transaction_type,
            "Tahsil Edilen Tutar (TL)": amount,
            "Kalan Toplam Borç (TL)": max(0, remaining_balance),
        }
    ]
    df = pd.DataFrame(invoice_data)
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Fatura_Makbuz")
    buffer.seek(0)
    return buffer


def check_debt_status(contract_no: str, category: str) -> Tuple[bool, float]:
    """Return whether a contract has outstanding debt and the remaining amount, excluding deposit-related payments."""
    table_name = _table_for_category(category)
    query = text(
        f"""
        SELECT
            c.total_kira,
            COALESCE(SUM(o.odenen_tutar), 0) AS odenen
        FROM {table_name} c
        LEFT JOIN odemeler o ON c.sozlesme_no = o.sozlesme_no
            AND UPPER(CAST(o.odeme_tipi AS VARCHAR)) NOT IN {DEPOSIT_PAYMENT_TYPES_SQL}
        WHERE c.sozlesme_no = :contract_no
        GROUP BY c.total_kira;
        """
    )
    res = run_query(query, params={"contract_no": contract_no})
    if res is not None and not res.empty:
        total = float(res.iloc[0]["total_kira"])
        paid = float(res.iloc[0]["odenen"])
        remaining = total - paid
        return remaining > 0.01, max(0.0, remaining)
    return False, 0.0


def recalculate_all_debt_summaries() -> dict:
    """One-time maintenance task that recomputes debt summary fields for every car and house contract."""
    updated_counts = {"ARAC": 0, "EV": 0}
    for category in ("ARAC", "EV"):
        table_name = _table_for_category(category)
        contracts_df = run_query(text(f"SELECT sozlesme_no FROM {table_name};"))
        if contracts_df is None or contracts_df.empty:
            continue
        for sozlesme_no in contracts_df["sozlesme_no"].dropna():
            ok, _ = _recompute_debt_summary(table_name, sozlesme_no)
            if ok:
                updated_counts[category] += 1
    return updated_counts


def get_reminder_candidates(category: Optional[str] = None) -> pd.DataFrame:
    """Return all active car and house contracts with outstanding debt, optionally filtered to one category, ordered by due date."""
    query = text(
        """
        SELECT
            ak.sozlesme_no, 'ARAC' AS kategori, ak.musteri_id, m.isim AS musteri_adi,
            m.telefon AS musteri_telefon, m.email AS musteri_email, ak.bitis_tarihi AS vade_tarihi, ak.kalan_borc
        FROM araba_kiralama_sozlesmeleri ak
        LEFT JOIN musteriler m ON ak.musteri_id = m.musteri_id
        WHERE ak.kalan_borc > 0.01
          AND LOWER(CAST(ak.sozlesme_durumu AS VARCHAR)) NOT LIKE '%iptal%'
          AND LOWER(CAST(ak.sozlesme_durumu AS VARCHAR)) NOT LIKE '%tamamlan%'
        UNION ALL
        SELECT
            ek.sozlesme_no, 'EV' AS kategori, ek.musteri_id, m.isim AS musteri_adi,
            m.telefon AS musteri_telefon, m.email AS musteri_email,
            COALESCE(
                (SELECT MIN(planlanan_tarih) FROM ev_odeme_plani WHERE sozlesme_no = ek.sozlesme_no AND durum != 'ÖDENDİ'),
                ek.bitis_tarihi
            ) AS vade_tarihi,
            ek.kalan_borc
        FROM ev_kiralama_sozlesmeleri ek
        LEFT JOIN musteriler m ON ek.musteri_id = m.musteri_id
        WHERE ek.kalan_borc > 0.01
          AND LOWER(CAST(ek.sozlesme_durumu AS VARCHAR)) NOT LIKE '%iptal%'
          AND LOWER(CAST(ek.sozlesme_durumu AS VARCHAR)) NOT LIKE '%tamamlan%'
        ORDER BY vade_tarihi ASC;
        """
    )
    df = run_query(query)
    if category and df is not None and not df.empty:
        df = df[df["kategori"] == category]
    return df


def send_reminder(
    sozlesme_no: str, category: str, employee_id: str = "SYSTEM", department_id: str = "AUTO", method: str = "sms"
) -> Tuple[bool, str]:
    """Send a single payment reminder (SMS or email) to the customer on a given contract."""
    df = get_reminder_candidates()
    if df is None or df.empty:
        return False, "Sözleşme bulunamadı veya borç kalmamış."
    row_df = df[(df["sozlesme_no"] == sozlesme_no) & (df["kategori"] == category)]
    if row_df.empty:
        return False, "Sözleşme bulunamadı veya borç kalmamış."
    row = row_df.iloc[0]

    if method == "email":
        email = row["musteri_email"]
        if not email or not str(email).strip():
            return False, f"{row['musteri_adi']} için sistemde kayıtlı bir e-posta adresi bulunamadı."
        subject = f"Ödeme Hatırlatması - Sözleşme #{sozlesme_no}"
        body = (
            f"Sayın {row['musteri_adi']},\n\n#{sozlesme_no} numaralı sözleşmenize ait "
            f"₺{float(row['kalan_borc']):,.2f} tutarında ödemenizin vadesi {row['vade_tarihi']}. "
            "Lütfen ödemenizi gerçekleştiriniz."
        )
        ok, err = send_payment_reminder_email(email, subject, body)
        if ok:
            log_transaction(
                employee_id, department_id, "Ödeme Hatırlatması Gönderildi",
                f"Sözleşme #{sozlesme_no} ({category}) için hatırlatma e-postası gönderildi.",
            )
            return True, f"📧 Hatırlatma e-postası {email} adresine gönderildi."
        return False, err or "E-posta gönderilemedi."

    phone = row["musteri_telefon"] or "+90 (555) 000 0000"
    msg = (
        f"Sayın {row['musteri_adi']}, #{sozlesme_no} numaralı sözleşmenize ait ₺{float(row['kalan_borc']):,.2f} "
        f"tutarında ödemenizin vadesi {row['vade_tarihi']}. Lütfen ödemenizi gerçekleştiriniz."
    )
    ok, err = send_sms_notification(phone, msg, "ÖDEME HATIRLATMA")
    if ok:
        log_transaction(
            employee_id, department_id, "Ödeme Hatırlatması Gönderildi",
            f"Sözleşme #{sozlesme_no} ({category}) için hatırlatma SMS'i gönderildi.",
        )
        return True, f"📲 Hatırlatma SMS'i {phone} numarasına gönderildi."
    return False, err or "SMS gönderilemedi."


def send_bulk_reminders(
    employee_id: str = "SYSTEM", department_id: str = "AUTO", method: str = "sms", category: Optional[str] = None
) -> Tuple[int, int, Optional[str]]:
    """Send payment reminders to every contract with outstanding debt, optionally restricted to one category, and log the result."""
    df = get_reminder_candidates(category=category)
    if df is None or df.empty:
        return 0, 0, None

    success_count = 0
    fail_count = 0
    first_error: Optional[str] = None

    for _, row in df.iterrows():
        if method == "email":
            email = row["musteri_email"]
            if not email or not str(email).strip():
                fail_count += 1
                if not first_error:
                    first_error = f"{row['musteri_adi']} için kayıtlı e-posta adresi yok."
                continue
            subject = f"Ödeme Hatırlatması - Sözleşme #{row['sozlesme_no']}"
            body = (
                f"Sayın {row['musteri_adi']},\n\n#{row['sozlesme_no']} numaralı sözleşmenize ait "
                f"₺{float(row['kalan_borc']):,.2f} tutarında ödemenizin vadesi {row['vade_tarihi']}. "
                "Lütfen ödemenizi gerçekleştiriniz."
            )
            ok, err = send_payment_reminder_email(email, subject, body)
        else:
            phone = row["musteri_telefon"] or "+90 (555) 000 0000"
            msg = (
                f"Sayın {row['musteri_adi']}, #{row['sozlesme_no']} numaralı sözleşmenize ait ₺{float(row['kalan_borc']):,.2f} "
                f"tutarında ödemenizin vadesi {row['vade_tarihi']}. Lütfen ödemenizi gerçekleştiriniz."
            )
            ok, err = send_sms_notification(phone, msg, "ÖDEME HATIRLATMA")

        if ok:
            success_count += 1
        else:
            fail_count += 1
            if not first_error:
                first_error = err

    if success_count:
        log_transaction(
            employee_id, department_id, "Toplu Ödeme Hatırlatması Gönderildi",
            f"{success_count} adet sözleşmeye toplu ödeme hatırlatma bildirimi gönderildi ({method})."
            + (f" {fail_count} adet başarısız oldu." if fail_count else ""),
        )
    return success_count, fail_count, first_error
