"""Backend service for housing (apartment) rental contracts, payment plans, and related notifications."""
import math
import re
from datetime import date
from typing import Optional, Tuple

import pandas as pd
from dateutil.relativedelta import relativedelta
from sqlalchemy import text

from database import execute_query, run_query
from services import exchange_rate_service, finance_service
from services.notification_service import log_transaction, send_payment_reminder_email, send_sms_notification

MAX_TAKSIT_SAYISI = 240


def get_customers() -> pd.DataFrame:
    """Return all customers with id, name, and phone, ordered by name."""
    return run_query("SELECT musteri_id, isim, telefon FROM musteriler ORDER BY isim ASC;")


def get_available_apartments(start_date: date, end_date: date) -> pd.DataFrame:
    """Return apartments that are not retired and have no overlapping active/pending contract in the given date range."""
    query = text(
        """
        SELECT d.daire_id, d.daire_no, d.oda_sayisi, d.aylik_kira, a.apartman_adi, a.il, a.ilce
        FROM daireler d
        LEFT JOIN apartmanlar a ON d.apartman_id = a.apartman_id
        WHERE d.pasif_tarihi IS NULL
          AND d.daire_id NOT IN (
            SELECT DISTINCT ek.daire_id
            FROM ev_kiralama_sozlesmeleri ek
            WHERE LOWER(CAST(ek.sozlesme_durumu AS VARCHAR)) IN ('aktif', 'devam ediyor', 'beklemede')
              AND ek.baslangic_tarihi <= :end_date
              AND ek.bitis_tarihi >= :start_date
              AND COALESCE(ek.tahliye_onayi, false) = false
        );
        """
    )
    return run_query(query, params={"start_date": str(start_date), "end_date": str(end_date)})


def get_all_contracts() -> pd.DataFrame:
    """Promote pending contracts that have started to active status, then return all contracts with apartment and customer details."""
    execute_query(
        text(
            """
            UPDATE ev_kiralama_sozlesmeleri
            SET sozlesme_durumu = 'DEVAM EDİYOR'
            WHERE LOWER(CAST(sozlesme_durumu AS VARCHAR)) LIKE '%bekleme%'
              AND baslangic_tarihi <= CURRENT_DATE;
            """
        )
    )
    query = """
        SELECT
            ek.sozlesme_no, ek.daire_id, a.apartman_adi, d.daire_no, d.oda_sayisi, a.il, a.ilce,
            m.isim AS musteri_adi, m.telefon, m.email, ek.musteri_id, c.ad_soyad AS calisan_adi,
            ek.baslangic_tarihi, ek.bitis_tarihi, ek.aylik_kira_yrd AS aylik_kira, ek.depozito,
            ek.total_kira AS toplam_tutar, ek.sozlesme_durumu, ek.kalan_borc
        FROM ev_kiralama_sozlesmeleri ek
        LEFT JOIN daireler d ON ek.daire_id = d.daire_id
        LEFT JOIN apartmanlar a ON d.apartman_id = a.apartman_id
        LEFT JOIN musteriler m ON ek.musteri_id = m.musteri_id
        LEFT JOIN calisan c ON ek.islemi_yapan_calisan_id = c.calisan_id
        ORDER BY ek.sozlesme_no DESC;
    """
    return run_query(query)


def get_overdue_rent_contracts() -> pd.DataFrame:
    """Calculate overdue monthly rent debt for active contracts based on elapsed months and payments made."""
    sql = """
        SELECT
            s.sozlesme_no, m.isim AS musteri_adi, m.telefon, m.email, a.apartman_adi, d.daire_no,
            s.aylik_kira_yrd AS aylik_kira, s.baslangic_tarihi, s.sozlesme_durumu,
            COALESCE(o_sum.odenen_kira, 0) AS odenen_kira
        FROM ev_kiralama_sozlesmeleri s
        LEFT JOIN daireler d ON s.daire_id = d.daire_id
        LEFT JOIN apartmanlar a ON d.apartman_id = a.apartman_id
        LEFT JOIN musteriler m ON s.musteri_id = m.musteri_id
        LEFT JOIN (
            SELECT sozlesme_no, SUM(odenen_tutar) AS odenen_kira FROM odemeler
            WHERE UPPER(CAST(odeme_tipi AS VARCHAR)) NOT LIKE '%DEPOZITO%' GROUP BY sozlesme_no
        ) o_sum ON s.sozlesme_no = o_sum.sozlesme_no;
    """
    df = run_query(sql)
    if df is None or df.empty:
        return pd.DataFrame()

    today = pd.to_datetime("today").normalize()
    df = df[
        ~df["sozlesme_durumu"].astype(str).str.upper().str.contains(
            "İPTAL|IPTAL|SİLİNDİ|SILINDI|TAMAMLAN|BITTI|BİTTİ", na=False
        )
    ].copy()
    if df.empty:
        return pd.DataFrame()

    df["baslangic_tarihi"] = pd.to_datetime(df["baslangic_tarihi"]).dt.normalize()

    gecen_aylar, vadesi_gelen_tutarlar, geciken_borclar = [], [], []
    for _, row in df.iterrows():
        b_tarih = row["baslangic_tarihi"]
        aylik_k = float(row["aylik_kira"]) if row["aylik_kira"] else 0.0
        odenen_k = float(row["odenen_kira"]) if row["odenen_kira"] else 0.0

        if b_tarih > today:
            vadesi_gelen_ay_sayisi = 0
        else:
            year_diff = today.year - b_tarih.year
            month_diff = today.month - b_tarih.month
            completed_months = year_diff * 12 + month_diff
            pass_months = completed_months if today.day >= b_tarih.day else max(0, completed_months - 1)
            vadesi_gelen_ay_sayisi = pass_months + 1

        toplam_vadesi_gelen_tutar = vadesi_gelen_ay_sayisi * aylik_k
        kalan_geciken_borc = toplam_vadesi_gelen_tutar - odenen_k

        gecen_aylar.append(vadesi_gelen_ay_sayisi)
        vadesi_gelen_tutarlar.append(toplam_vadesi_gelen_tutar)
        geciken_borclar.append(kalan_geciken_borc)

    df["gecen_ay_sayisi"] = gecen_aylar
    df["vadesi_gelen_tutar"] = vadesi_gelen_tutarlar
    df["geciken_kira_borcu"] = geciken_borclar

    return df[df["geciken_kira_borcu"] > 0].copy()


def _ensure_expiry_reminder_column() -> None:
    """Idempotently add the reminder-sent flag column to the contracts table if it does not already exist."""
    execute_query(
        text(
            "ALTER TABLE ev_kiralama_sozlesmeleri "
            "ADD COLUMN IF NOT EXISTS bitis_hatirlatma_gonderildi BOOLEAN DEFAULT FALSE;"
        )
    )


def get_expiring_contracts() -> pd.DataFrame:
    """Return not-yet-closed contracts expiring within 30 days, with customer contact info and days remaining."""
    _ensure_expiry_reminder_column()
    sql = """
        SELECT
            s.sozlesme_no, s.musteri_id, m.isim AS musteri_adi, m.telefon, m.email,
            a.apartman_adi, d.daire_no, a.il, a.ilce,
            s.baslangic_tarihi, s.bitis_tarihi, s.sozlesme_durumu,
            COALESCE(s.bitis_hatirlatma_gonderildi, FALSE) AS hatirlatma_gonderildi
        FROM ev_kiralama_sozlesmeleri s
        LEFT JOIN daireler d ON s.daire_id = d.daire_id
        LEFT JOIN apartmanlar a ON d.apartman_id = a.apartman_id
        LEFT JOIN musteriler m ON s.musteri_id = m.musteri_id
        WHERE s.bitis_tarihi >= CURRENT_DATE
          AND s.bitis_tarihi <= CURRENT_DATE + INTERVAL '30 days'
          AND UPPER(CAST(s.sozlesme_durumu AS VARCHAR)) NOT LIKE '%TAMAMLAN%'
          AND UPPER(CAST(s.sozlesme_durumu AS VARCHAR)) NOT LIKE '%İPTAL%'
          AND UPPER(CAST(s.sozlesme_durumu AS VARCHAR)) NOT LIKE '%IPTAL%'
          AND UPPER(CAST(s.sozlesme_durumu AS VARCHAR)) NOT LIKE '%BITTI%'
          AND UPPER(CAST(s.sozlesme_durumu AS VARCHAR)) NOT LIKE '%BİTTİ%'
        ORDER BY s.bitis_tarihi ASC;
    """
    df = run_query(sql)
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()
    df["baslangic_tarihi"] = pd.to_datetime(df["baslangic_tarihi"]).dt.normalize()
    df["bitis_tarihi"] = pd.to_datetime(df["bitis_tarihi"]).dt.normalize()
    today = pd.to_datetime("today").normalize()
    df["kalan_gun"] = (df["bitis_tarihi"] - today).dt.days
    return df


def _mark_expiry_reminder_sent(contract_no: str) -> None:
    """Flag the given contract as having had its expiry reminder sent."""
    execute_query(
        text("UPDATE ev_kiralama_sozlesmeleri SET bitis_hatirlatma_gonderildi = TRUE WHERE sozlesme_no = :no;"),
        params={"no": contract_no},
    )


def _send_expiry_reminder_for_row(
    row, method: str, employee_id: str, department_id: str
) -> Tuple[bool, str]:
    """Send an expiry reminder (email or SMS) using an already-fetched expiring-contract row, and mark it as reminded on success."""
    contract_no = row["sozlesme_no"]
    kalan_gun = int(row["kalan_gun"])
    bitis_str = row["bitis_tarihi"].strftime("%d.%m.%Y")

    if method == "email":
        email = row["email"]
        if not email or not str(email).strip():
            return False, f"{row['musteri_adi']} için sistemde kayıtlı bir e-posta adresi bulunamadı."
        subject = f"Konut Sözleşmeniz Sona Eriyor - Sözleşme #{contract_no}"
        body = (
            f"Sayın {row['musteri_adi']},\n\n#{contract_no} numaralı konut kiralama sözleşmenizin "
            f"bitimine son 1 ay kaldı.\n\nKalan gün: {kalan_gun} gün\nSözleşme bitiş tarihi: {bitis_str}\n\n"
            "Sözleşmenizi uzatmak/yenilemek isterseniz lütfen bizimle iletişime geçiniz."
        )
        ok, err = send_payment_reminder_email(email, subject, body)
        message = f"📧 Sözleşme bitiş hatırlatma e-postası {email} adresine gönderildi." if ok else (err or "E-posta gönderilemedi.")
    else:
        phone = row["telefon"] or "+90 (555) 000 0000"
        msg = (
            f"Sayın {row['musteri_adi']}, #{contract_no} numaralı konut sözleşmenizin bitimine "
            f"{kalan_gun} gün kaldı (bitiş tarihi: {bitis_str}). Sözleşmenizi uzatmak için bizimle iletişime geçiniz."
        )
        ok, err = send_sms_notification(phone, msg, "SÖZLEŞME BİTİŞ HATIRLATMA")
        message = f"📲 Sözleşme bitiş hatırlatma SMS'i {phone} numarasına gönderildi." if ok else (err or "SMS gönderilemedi.")

    if ok:
        _mark_expiry_reminder_sent(contract_no)
        log_transaction(
            employee_id, department_id, "Sözleşme Bitiş Hatırlatması Gönderildi",
            f"Sözleşme #{contract_no} için bitiş hatırlatması gönderildi ({method}, kalan gün: {kalan_gun}).",
        )
    return ok, message


def send_contract_expiry_reminder(
    contract_no: str, method: str = "email", employee_id: str = "SYSTEM", department_id: str = "AUTO"
) -> Tuple[bool, str]:
    """Send an expiry reminder (email or SMS) for a single expiring contract and mark it as reminded on success."""
    df = get_expiring_contracts()
    if df is None or df.empty:
        return False, "Sözleşme bulunamadı ya da bitişine 30 günden az kalan bir sözleşme değil."
    row_df = df[df["sozlesme_no"] == contract_no]
    if row_df.empty:
        return False, "Sözleşme bulunamadı ya da bitişine 30 günden az kalan bir sözleşme değil."
    return _send_expiry_reminder_for_row(row_df.iloc[0], method, employee_id, department_id)


def send_bulk_contract_expiry_reminders(
    employee_id: str = "SYSTEM", department_id: str = "AUTO", method: str = "email"
) -> Tuple[int, int, Optional[str]]:
    """Manually send expiry reminders to every contract in the expiring list, regardless of prior reminders."""
    df = get_expiring_contracts()
    if df is None or df.empty:
        return 0, 0, None

    success_count = 0
    fail_count = 0
    first_error: Optional[str] = None
    for _, row in df.iterrows():
        ok, message = _send_expiry_reminder_for_row(row, method, employee_id, department_id)
        if ok:
            success_count += 1
        else:
            fail_count += 1
            if not first_error:
                first_error = message
    return success_count, fail_count, first_error


def run_daily_expiry_reminder_sweep(employee_id: str = "SYSTEM", department_id: str = "AUTO") -> Tuple[int, int, Optional[str]]:
    """Daily automated task that sends a one-time email and SMS expiry reminder to contracts not yet reminded."""
    df = get_expiring_contracts()
    if df is None or df.empty:
        return 0, 0, None
    df = df[~df["hatirlatma_gonderildi"].astype(bool)]
    if df.empty:
        return 0, 0, None

    success_count = 0
    fail_count = 0
    first_error: Optional[str] = None
    for _, row in df.iterrows():
        email_ok, email_msg = _send_expiry_reminder_for_row(row, "email", employee_id, department_id)
        sms_ok, sms_msg = _send_expiry_reminder_for_row(row, "sms", employee_id, department_id)
        if email_ok or sms_ok:
            success_count += 1
        else:
            fail_count += 1
            if not first_error:
                first_error = email_msg or sms_msg
    return success_count, fail_count, first_error


def send_overdue_vacate_notice(
    contract_no: str, method: str = "email", employee_id: str = "SYSTEM", department_id: str = "AUTO"
) -> Tuple[bool, str]:
    """Send a notice to a tenant whose active contract has expired but has not been closed or vacated."""
    df = get_all_contracts()
    if df is None or df.empty:
        return False, "Sözleşme bulunamadı."
    row_df = df[df["sozlesme_no"] == contract_no]
    if row_df.empty:
        return False, "Sözleşme bulunamadı."
    row = row_df.iloc[0]

    bitis = pd.to_datetime(row["bitis_tarihi"], errors="coerce")
    if pd.isna(bitis):
        return False, "Sözleşmenin bitiş tarihi okunamadı."
    bitis = bitis.normalize()
    today = pd.to_datetime("today").normalize()
    kalan_gun = int((bitis - today).days)
    durum = str(row.get("sozlesme_durumu") or "").upper()
    aktif_mi = any(k in durum for k in ["DEVAM EDİYOR", "DEVAM EDIYOR", "AKTİF", "AKTIF"])
    if kalan_gun >= 0 or not aktif_mi:
        return False, "Bu sözleşmenin süresi henüz dolmamış ya da sözleşme zaten kapatılmış."

    gecikme_gun = abs(kalan_gun)
    bitis_str = bitis.strftime("%d.%m.%Y")

    if method == "email":
        email = row.get("email")
        if not email or not str(email).strip():
            return False, f"{row['musteri_adi']} için sistemde kayıtlı bir e-posta adresi bulunamadı."
        subject = f"Konut Sözleşmeniz Süresi Doldu - Sözleşme #{contract_no}"
        body = (
            f"Sayın {row['musteri_adi']},\n\n#{contract_no} numaralı konut kiralama sözleşmenizin süresi "
            f"{gecikme_gun} gün önce ({bitis_str}) dolmuştur.\n\nLütfen en kısa sürede tahliye işlemlerini "
            "tamamlayınız ya da sözleşmeyi uzatmak için bizimle iletişime geçiniz."
        )
        ok, err = send_payment_reminder_email(email, subject, body)
        message = f"📧 Gecikme ihtar e-postası {email} adresine gönderildi." if ok else (err or "E-posta gönderilemedi.")
    else:
        phone = row.get("telefon") or "+90 (555) 000 0000"
        msg = (
            f"Sayın {row['musteri_adi']}, #{contract_no} numaralı konut sözleşmenizin süresi {gecikme_gun} gün "
            "önce dolmuştur. Lütfen bizimle iletişime geçiniz."
        )
        ok, err = send_sms_notification(phone, msg, "EV GECİKME İHTAR SMS")
        message = f"📲 Gecikme ihtar SMS'i {phone} numarasına gönderildi." if ok else (err or "SMS gönderilemedi.")

    if ok:
        log_transaction(
            employee_id, department_id, "Ev Tahliye Gecikme İhtarı Gönderildi",
            f"Sözleşme #{contract_no} için gecikme ihtarı gönderildi ({method}, gecikme: {gecikme_gun} gün).",
        )
    return ok, message


def send_overdue_rent_notice(
    contract_no: str, method: str = "email", employee_id: str = "SYSTEM", department_id: str = "AUTO"
) -> Tuple[bool, str]:
    """Send a notice to a tenant with overdue rent debt on their contract."""
    df = get_overdue_rent_contracts()
    if df is None or df.empty:
        return False, "Vadesi geçmiş kira borcu olan sözleşme bulunamadı."
    row_df = df[df["sozlesme_no"] == contract_no]
    if row_df.empty:
        return False, "Bu sözleşmenin vadesi geçmiş bir kira borcu bulunamadı."
    row = row_df.iloc[0]
    borc = float(row["geciken_kira_borcu"])

    if method == "email":
        email = row.get("email")
        if not email or not str(email).strip():
            return False, f"{row['musteri_adi']} için sistemde kayıtlı bir e-posta adresi bulunamadı."
        subject = f"Vadesi Geçmiş Kira Borcunuz - Sözleşme #{contract_no}"
        body = (
            f"Sayın {row['musteri_adi']},\n\n#{contract_no} numaralı konut sözleşmenize ait "
            f"₺{borc:,.2f} tutarında vadesi geçmiş kira borcunuz bulunmaktadır.\n\nLütfen en kısa sürede "
            "ödemenizi gerçekleştiriniz."
        )
        ok, err = send_payment_reminder_email(email, subject, body)
        message = f"📧 Kira ihtar e-postası {email} adresine gönderildi." if ok else (err or "E-posta gönderilemedi.")
    else:
        phone = row.get("telefon") or "+90 (555) 000 0000"
        msg = f"Sayın {row['musteri_adi']}, #{contract_no} sözleşmenize ait ₺{borc:,.2f} vadesi geçmiş kira borcunuz bulunmaktadır."
        ok, err = send_sms_notification(phone, msg, "KİRA_VADE_UYARISI")
        message = f"📲 Kira ihtar SMS'i {phone} numarasına gönderildi." if ok else (err or "SMS gönderilemedi.")

    if ok:
        log_transaction(
            employee_id, department_id, "Kira Gecikme İhtarı Gönderildi",
            f"Sözleşme #{contract_no} için kira gecikme ihtarı gönderildi ({method}, borç: ₺{borc:,.2f}).",
        )
    return ok, message


def _next_contract_no() -> str:
    """Generate the next sequential contract number in the KS-#### format."""
    res = run_query("SELECT sozlesme_no FROM ev_kiralama_sozlesmeleri;")
    max_num = 0
    if res is not None and not res.empty:
        for no_val in res["sozlesme_no"].dropna():
            parts = str(no_val).strip().split("-")
            if parts and parts[-1].isdigit():
                max_num = max(max_num, int(parts[-1]))
    return f"KS-{max_num + 1:04d}"


def _next_id(table: str, id_column: str, prefix: str) -> str:
    """Generate the next sequential ID for a table by scanning all existing IDs for the highest numeric suffix."""
    df = run_query(f"SELECT {id_column} FROM {table};")
    max_num = 0
    if df is not None and not df.empty:
        for val in df[id_column].dropna():
            digits = re.sub(r"[^0-9]", "", str(val).strip())
            if digits:
                max_num = max(max_num, int(digits))
    return f"{prefix}{max_num + 1}"


def get_apartment_buildings() -> pd.DataFrame:
    """Return all apartment buildings ordered by name."""
    return run_query("SELECT apartman_id, apartman_adi, il, ilce FROM apartmanlar ORDER BY apartman_adi ASC;")


def get_all_units() -> pd.DataFrame:
    """List all units, including retired ones, for the new-unit management page."""
    query = """
        SELECT d.daire_id, d.daire_no, d.oda_sayisi, d.aylik_kira, d.musaitlik_durumu,
               d.sisteme_ekleme_tarihi, d.pasif_tarihi, a.apartman_adi, a.il, a.ilce
        FROM daireler d
        LEFT JOIN apartmanlar a ON d.apartman_id = a.apartman_id
        ORDER BY d.pasif_tarihi NULLS FIRST, d.sisteme_ekleme_tarihi DESC;
    """
    return run_query(query)


def add_apartment(
    apartman_id: Optional[str],
    new_apartman_adi: Optional[str],
    new_il: Optional[str],
    new_ilce: Optional[str],
    new_mahalle: Optional[str],
    daire_no: str,
    oda_sayisi: str,
    aylik_kira: float,
    employee_id: str,
    department_id: str,
) -> dict:
    """Add a new unit to the system, either under an existing building or a newly created one."""
    if not daire_no or not oda_sayisi or aylik_kira is None or aylik_kira <= 0:
        return {"success": False, "message": "Daire no, oda sayısı ve geçerli (0'dan büyük) bir aylık kira girilmelidir."}

    if apartman_id is None:
        if not new_apartman_adi or not new_il or not new_ilce:
            return {"success": False, "message": "Mevcut bir apartman seçmediyseniz apartman adı, il ve ilçe bilgilerini girmelisiniz."}
        apartman_id = _next_id("apartmanlar", "apartman_id", "AP")
        ok, err = execute_query(
            text("INSERT INTO apartmanlar (apartman_id, apartman_adi, il, ilce) VALUES (:id, :adi, :il, :ilce);"),
            params={"id": apartman_id, "adi": new_apartman_adi, "il": new_il, "ilce": new_ilce},
        )
        if not ok:
            return {"success": False, "message": f"Yeni apartman eklenemedi: {err}"}

    dup_df = run_query(
        text("SELECT daire_id FROM daireler WHERE apartman_id = :aid AND UPPER(daire_no) = UPPER(:no);"),
        params={"aid": apartman_id, "no": daire_no},
    )
    if dup_df is not None and not dup_df.empty:
        return {"success": False, "message": f"Bu apartmanda '{daire_no}' numaralı bir daire zaten sistemde kayıtlı."}

    daire_id = _next_id("daireler", "daire_id", "D")
    ok, err = execute_query(
        text(
            "INSERT INTO daireler (daire_id, apartman_id, daire_no, oda_sayisi, aylik_kira, musaitlik_durumu, sisteme_ekleme_tarihi) "
            "VALUES (:id, :aid, :no, :oda, :kira, 'Müsait', :bugun);"
        ),
        params={
            "id": daire_id, "aid": apartman_id, "no": daire_no, "oda": oda_sayisi, "kira": aylik_kira,
            "bugun": date.today().strftime("%Y-%m-%d"),
        },
    )
    if not ok:
        return {"success": False, "message": f"Daire eklenemedi: {err}"}

    log_transaction(employee_id, department_id, "Yeni Daire Eklendi", f"Yeni daire sisteme eklendi: {daire_no} (ID: {daire_id})")
    return {"success": True, "message": f"'{daire_no}' numaralı daire sisteme başarıyla eklendi! (Daire ID: {daire_id})"}


def retire_apartment(daire_id: str, retire_date: date, employee_id: str, department_id: str) -> dict:
    """Retire a unit from the system (e.g. due to sale) by setting its retirement date."""
    df = run_query(text("SELECT daire_no, pasif_tarihi, sisteme_ekleme_tarihi FROM daireler WHERE daire_id = :id;"), params={"id": daire_id})
    if df is None or df.empty:
        return {"success": False, "message": "Daire bulunamadı."}
    if df.iloc[0]["pasif_tarihi"] is not None:
        return {"success": False, "message": "Bu daire zaten pasif durumda."}
    ekleme_tarihi = df.iloc[0]["sisteme_ekleme_tarihi"]
    if ekleme_tarihi is not None and retire_date < ekleme_tarihi:
        return {"success": False, "message": "Çıkış tarihi, dairenin sisteme eklendiği tarihten önce olamaz."}

    ok, err = execute_query(
        text("UPDATE daireler SET pasif_tarihi = :d, musaitlik_durumu = 'Pasif' WHERE daire_id = :id;"),
        params={"d": str(retire_date), "id": daire_id},
    )
    if not ok:
        return {"success": False, "message": f"İşlem başarısız: {err}"}
    log_transaction(
        employee_id, department_id, "Daire Pasife Alındı",
        f"Daire sistemden pasife alındı: {df.iloc[0]['daire_no']} (ID: {daire_id}), tarih: {retire_date}",
    )
    return {"success": True, "message": f"Daire ({df.iloc[0]['daire_no']}) başarıyla pasife alındı."}


def _calculate_installments(
    total_price: float, start_date: date, monthly_rent: float,
    plan_type: str, installment_amount: Optional[float],
) -> list:
    """Pure computation of an installment schedule (duration-based or fixed-amount) as a list of (date, amount) tuples."""
    if total_price <= 0:
        return []

    plan_type = (plan_type or "SURE_BAZLI").upper()
    if plan_type == "TUTAR_BAZLI" and installment_amount and installment_amount > 0:
        per_amount = round(min(installment_amount, total_price), 2)
    else:
        per_amount = round(monthly_rent, 2) if monthly_rent > 0 else round(total_price, 2)

    installment_count = min(max(1, math.ceil(total_price / per_amount)), MAX_TAKSIT_SAYISI)
    remaining = total_price
    rows = []
    for i in range(1, installment_count + 1):
        taksit_tarih = start_date + relativedelta(months=i - 1)
        tutar = round(remaining, 2) if i == installment_count else per_amount
        remaining = round(remaining - tutar, 2)
        rows.append((taksit_tarih, tutar))
    return rows


def _insert_installments(contract_no: str, start_taksit_no: int, rows: list) -> None:
    """Insert a list of computed installment rows into the payment plan table starting at the given installment number."""
    if not rows:
        return
    params_list = [
        {"no": contract_no, "tno": start_taksit_no + offset, "tarih": str(taksit_tarih), "tutar": tutar}
        for offset, (taksit_tarih, tutar) in enumerate(rows)
    ]
    execute_query(
        text(
            "INSERT INTO ev_odeme_plani (sozlesme_no, taksit_no, planlanan_tarih, planlanan_tutar, odenen_tutar, durum) "
            "VALUES (:no, :tno, :tarih, :tutar, 0, 'BEKLİYOR');"
        ),
        params=params_list,
    )


def _generate_payment_plan(
    contract_no: str, total_price: float, start_date: date, monthly_rent: float,
    plan_type: str, installment_amount: Optional[float],
) -> None:
    """Create and persist the flexible installment payment plan for a new contract."""
    rows = _calculate_installments(total_price, start_date, monthly_rent, plan_type, installment_amount)
    _insert_installments(contract_no, 1, rows)


def create_contract(
    customer_id: Optional[int],
    new_customer_name: Optional[str],
    new_customer_phone: Optional[str],
    new_customer_email: Optional[str],
    new_customer_tc: Optional[str],
    apartment_id: str,
    start_date: date,
    end_date: date,
    deposit_amount: float,
    employee_id: str,
    department_id: str,
    plan_type: str = "SURE_BAZLI",
    installment_amount: Optional[float] = None,
) -> dict:
    """Create a new housing rental contract, computing total price from monthly rent times duration plus partial-day proration."""
    if end_date < start_date:
        return {"success": False, "message": "Bitiş tarihi başlangıç tarihinden önce olamaz!"}

    if (plan_type or "SURE_BAZLI").upper() == "TUTAR_BAZLI" and not (installment_amount and installment_amount > 0):
        return {"success": False, "message": "Tutar bazlı ödeme planı için geçerli (0'dan büyük) bir taksit tutarı girmelisiniz."}

    apt_df = run_query(text("SELECT aylik_kira FROM daireler WHERE daire_id = :aid;"), params={"aid": apartment_id})
    if apt_df is None or apt_df.empty:
        return {"success": False, "message": "Seçilen daire bulunamadı."}
    monthly_rent = float(apt_df.iloc[0]["aylik_kira"])

    date_diff = relativedelta(end_date, start_date)
    full_months = date_diff.years * 12 + date_diff.months
    remaining_days = date_diff.days
    total_price = round((full_months * monthly_rent) + (remaining_days * (monthly_rent / 30.0)), 2)

    if (plan_type or "SURE_BAZLI").upper() == "TUTAR_BAZLI" and installment_amount and installment_amount > 0 and total_price > 0:
        projected_count = math.ceil(total_price / min(installment_amount, total_price))
        if projected_count > MAX_TAKSIT_SAYISI:
            return {
                "success": False,
                "message": (
                    f"Girilen taksit tutarına göre {projected_count} taksit oluşur, bu izin verilen "
                    f"en fazla {MAX_TAKSIT_SAYISI} taksitten fazla. Lütfen daha yüksek bir taksit tutarı girin."
                ),
            }

    overlap_df = run_query(
        text(
            """
            SELECT COUNT(*) AS adet
            FROM ev_kiralama_sozlesmeleri
            WHERE daire_id = :aid
              AND LOWER(CAST(sozlesme_durumu AS VARCHAR)) IN ('aktif', 'devam ediyor', 'beklemede')
              AND baslangic_tarihi <= :end_date
              AND bitis_tarihi >= :start_date
              AND COALESCE(tahliye_onayi, false) = false;
            """
        ),
        params={"aid": apartment_id, "start_date": str(start_date), "end_date": str(end_date)},
    )
    if overlap_df is not None and not overlap_df.empty and int(overlap_df.iloc[0]["adet"]) > 0:
        return {
            "success": False,
            "message": (
                "Üzgünüz, seçtiğiniz daire bu tarihler için az önce başka bir çalışan "
                "tarafından kiralandı. Lütfen müsait daire listesini yenileyip tekrar deneyin."
            ),
        }

    if customer_id is None:
        if not new_customer_name or not new_customer_phone or not new_customer_email or not new_customer_tc:
            return {
                "success": False,
                "message": "Yeni kiracının Ad Soyad, Telefon, E-posta ve Kimlik No (TC/Pasaport) bilgilerinin tümünü doldurun.",
            }
        max_res = run_query("SELECT COALESCE(MAX(musteri_id), 0) AS max_id FROM musteriler;")
        new_id = int(max_res.iloc[0]["max_id"]) + 1 if max_res is not None and not max_res.empty else 1
        ok, err = execute_query(
            text(
                "INSERT INTO musteriler (musteri_id, isim, telefon, email, tc_kimlik_no, kayit_tarihi) "
                "VALUES (:id, :name, :phone, :email, :tc, :today);"
            ),
            params={
                "id": new_id, "name": new_customer_name, "phone": new_customer_phone,
                "email": new_customer_email, "tc": new_customer_tc, "today": date.today().strftime("%Y-%m-%d"),
            },
        )
        if not ok:
            return {"success": False, "message": f"Müşteri eklenemedi: {err}"}
        customer_id = new_id
        log_transaction(employee_id, department_id, "Yeni Kiracı Eklendi", f"Yeni kiracı eklendi: {new_customer_name} (ID: {customer_id})")

    contract_no = _next_contract_no()
    initial_status = "BEKLEMEDE" if start_date > date.today() else "DEVAM EDİYOR"

    ok, err = execute_query(
        text(
            """
            INSERT INTO ev_kiralama_sozlesmeleri
            (sozlesme_no, musteri_id, daire_id, islemi_yapan_calisan_id, baslangic_tarihi, bitis_tarihi,
             aylik_kira_yrd, depozito, total_kira, sozlesme_durumu, odenen_toplam_tutar, kalan_borc, odeme_durumu)
            VALUES (:no, :cust, :aid, :emp, :start, :end, :rent, :dep, :total, :status, 0.00, :total, 'ÖDENMEDİ');
            """
        ),
        params={
            "no": contract_no, "cust": customer_id, "aid": apartment_id, "emp": employee_id,
            "start": str(start_date), "end": str(end_date), "rent": monthly_rent, "dep": deposit_amount,
            "total": total_price, "status": initial_status,
        },
    )
    if not ok:
        return {"success": False, "message": f"Sözleşme kaydedilemedi: {err}"}

    execute_query(text("UPDATE daireler SET musaitlik_durumu = 'Kirada' WHERE daire_id = :aid;"), params={"aid": apartment_id})

    if deposit_amount > 0:
        finance_service.record_payment(
            contract_no=contract_no, category="EV", customer_id=customer_id, amount_paid=deposit_amount,
            payment_type="DEPOZITO_TAHSILATI", description=f"Sözleşme başlangıcı depozito tahsilatı (₺{deposit_amount:,.2f})",
        )

    _generate_payment_plan(contract_no, total_price, start_date, monthly_rent, plan_type, installment_amount)

    log_transaction(
        employee_id, department_id, "Yeni Ev Sözleşmesi Oluşturuldu",
        f"Sözleşme #{contract_no} oluşturuldu. Toplam Ciro: ₺{total_price:,.2f}, Depozito: ₺{deposit_amount:,.2f}",
    )

    return {
        "success": True,
        "message": f"#{contract_no} numaralı ev sözleşmesi ve ₺{deposit_amount:,.2f} depozito tahsilatı oluşturuldu!",
        "contract_no": contract_no, "total_price": total_price, "deposit_amount": deposit_amount,
    }


def get_payment_plan(contract_no: str) -> pd.DataFrame:
    """Return the installment payment plan for a contract, if one exists."""
    return run_query(
        text(
            "SELECT id, sozlesme_no, taksit_no, planlanan_tarih, planlanan_tutar, odenen_tutar, durum "
            "FROM ev_odeme_plani WHERE sozlesme_no = :no ORDER BY taksit_no ASC;"
        ),
        params={"no": contract_no},
    )


def pay_installment(
    contract_no: str,
    taksit_id: int,
    customer_id: int,
    amount_paid: float,
    doviz_cinsi: str,
    odeme_yontemi: str,
    description: str,
    employee_id: str,
    department_id: str,
    tam_kapat: bool = False,
) -> dict:
    """Apply a payment to a selected installment, marking it paid or partially paid and auto-creating a new installment for any remainder."""
    if amount_paid <= 0:
        return {"success": False, "message": "Ödeme tutarı 0'dan büyük olmalıdır."}

    plan_df = run_query(
        text("SELECT id, taksit_no, planlanan_tutar, odenen_tutar, durum FROM ev_odeme_plani WHERE id = :id AND sozlesme_no = :no;"),
        params={"id": taksit_id, "no": contract_no},
    )
    if plan_df is None or plan_df.empty:
        return {"success": False, "message": "Taksit bulunamadı."}
    plan_row = plan_df.iloc[0]
    if str(plan_row["durum"]).upper() == "ÖDENDİ":
        return {"success": False, "message": "Bu taksit zaten tamamen ödenmiş."}

    doviz_cinsi = (doviz_cinsi or "TRY").upper()
    if doviz_cinsi != "TRY":
        rate_info = exchange_rate_service.get_exchange_rate(date.today(), doviz_cinsi)
        kur = float(rate_info.get("satis") or 1.0)
        tl_amount = round(amount_paid * kur, 2)
    else:
        tl_amount = round(amount_paid, 2)

    ok, err = finance_service.record_payment(
        contract_no=contract_no, category="EV", customer_id=customer_id, amount_paid=amount_paid,
        payment_type="TAKSİT_ÖDEMESİ", description=description or f"{plan_row['taksit_no']}. taksit ödemesi",
        doviz_cinsi=doviz_cinsi, odeme_yontemi=odeme_yontemi, taksit_id=int(plan_row["id"]),
        tam_kapat=tam_kapat,
    )
    if not ok:
        return {"success": False, "message": f"Ödeme kaydedilemedi: {err}"}

    planlanan_tutar = float(plan_row["planlanan_tutar"])
    onceki_odenen = float(plan_row["odenen_tutar"] or 0)
    yeni_odenen = round(onceki_odenen + tl_amount, 2)

    if yeni_odenen >= planlanan_tutar - 0.01:
        execute_query(
            text("UPDATE ev_odeme_plani SET odenen_tutar = :od, durum = 'ÖDENDİ' WHERE id = :id;"),
            params={"od": planlanan_tutar, "id": taksit_id},
        )
        log_transaction(
            employee_id, department_id, "Taksit Ödemesi Alındı",
            f"Sözleşme #{contract_no}, {plan_row['taksit_no']}. taksit tamamen ödendi (₺{planlanan_tutar:,.2f}).",
        )
        return {"success": True, "message": f"{plan_row['taksit_no']}. taksit ödemesi alındı ve tamamlandı."}

    kalan = round(planlanan_tutar - yeni_odenen, 2)
    execute_query(
        text("UPDATE ev_odeme_plani SET odenen_tutar = :od, durum = 'PARÇALI ÖDENDİ' WHERE id = :id;"),
        params={"od": yeni_odenen, "id": taksit_id},
    )
    max_no_df = run_query(text("SELECT COALESCE(MAX(taksit_no), 0) AS max_no FROM ev_odeme_plani WHERE sozlesme_no = :no;"), params={"no": contract_no})
    next_taksit_no = int(max_no_df.iloc[0]["max_no"]) + 1 if max_no_df is not None and not max_no_df.empty else int(plan_row["taksit_no"]) + 1
    execute_query(
        text(
            "INSERT INTO ev_odeme_plani (sozlesme_no, taksit_no, planlanan_tarih, planlanan_tutar, odenen_tutar, durum) "
            "VALUES (:no, :tno, CURRENT_DATE, :tutar, 0, 'BEKLİYOR');"
        ),
        params={"no": contract_no, "tno": next_taksit_no, "tutar": kalan},
    )
    log_transaction(
        employee_id, department_id, "Parçalı Taksit Ödemesi",
        f"Sözleşme #{contract_no}, {plan_row['taksit_no']}. taksit parçalı ödendi (₺{tl_amount:,.2f}). "
        f"Kalan ₺{kalan:,.2f} için yeni taksit (#{next_taksit_no}) oluşturuldu.",
    )
    return {
        "success": True,
        "message": f"{plan_row['taksit_no']}. taksit için parçalı ödeme yapıldı. Kalan ₺{kalan:,.2f} tutarındaki bakiye için yeni bir taksit oluşturuldu.",
    }


def _get_contract_apartment_info(contract_no: str) -> Optional[dict]:
    """Fetch the unit, status, deposit, and customer id associated with a contract."""
    df = run_query(
        text("SELECT daire_id, sozlesme_durumu, depozito, musteri_id FROM ev_kiralama_sozlesmeleri WHERE sozlesme_no = :no;"),
        params={"no": contract_no},
    )
    if df is None or df.empty:
        return None
    row = df.iloc[0]
    return {
        "apartment_id": row["daire_id"], "status": str(row["sozlesme_durumu"]).upper(),
        "deposit": float(row["depozito"]) if row["depozito"] else 0.0, "customer_id": int(row["musteri_id"]),
    }


def complete_contract(contract_no: str, damage_cost: float, employee_id: str, department_id: str) -> dict:
    """Close a contract on move-out: block if debt remains, deduct any damage cost from the deposit, and refund the rest."""
    info = _get_contract_apartment_info(contract_no)
    if info is None:
        return {"success": False, "message": "Sözleşme bulunamadı."}

    has_debt, debt_amount = finance_service.check_debt_status(contract_no, "EV")
    if has_debt:
        return {"success": False, "message": f"Ev teslim alınamaz! Ödenmemiş ₺{debt_amount:,.2f} borç bulunuyor."}

    damage_cost = max(0.0, min(damage_cost, info["deposit"]))
    refund_amount = info["deposit"] - damage_cost

    if damage_cost > 0:
        finance_service.record_payment(
            contract_no=contract_no, category="EV", customer_id=info["customer_id"], amount_paid=-damage_cost,
            payment_type="HASAR_KESINTISI", description=f"Tahliye sırasında {damage_cost} TL depozito kesintisi yapıldı.",
        )
    finance_service.record_payment(
        contract_no=contract_no, category="EV", customer_id=info["customer_id"], amount_paid=-refund_amount,
        payment_type="DEPOZITO_IADE", description=f"Tahliye sonrası {refund_amount} TL depozito iadesi gerçekleştirildi.",
    )

    ok1, _ = execute_query(
        text(
            "UPDATE ev_kiralama_sozlesmeleri SET sozlesme_durumu = 'TAMAMLANDI', hasar_kesintisi = :dc, "
            "iade_edilen_depozito = :refund WHERE sozlesme_no = :no;"
        ),
        params={"dc": damage_cost, "refund": refund_amount, "no": contract_no},
    )
    ok2, _ = execute_query(
        text("UPDATE daireler SET musaitlik_durumu = 'Müsait' WHERE daire_id = :aid;"), params={"aid": info["apartment_id"]}
    )
    if ok1 and ok2:
        log_transaction(employee_id, department_id, "Sözleşme Tamamlandı (Tahliye)", f"Konut sözleşmesi tamamlandı #{contract_no}")
        return {"success": True, "message": f"#{contract_no} numaralı sözleşme kapatıldı, daire boşaltıldı!"}
    return {"success": False, "message": "İşlem sırasında veritabanı hatası oluştu."}


def cancel_contract(contract_no: str, employee_id: str, department_id: str) -> dict:
    """Cancel a contract that has not yet started, reversing any payments made and freeing the unit."""
    info = _get_contract_apartment_info(contract_no)
    if info is None:
        return {"success": False, "message": "Sözleşme bulunamadı."}

    if "BEKLEME" not in str(info["status"]).upper():
        return {
            "success": False,
            "message": (
                f"Bu sözleşme iptal edilemez (durum: {info['status']}). "
                "İptal etme seçeneği sadece henüz başlamamış (BEKLEMEDE) sözleşmelerde geçerlidir."
            ),
        }

    finance_service.reverse_contract_payments(contract_no, "EV", info["customer_id"])

    ok1, _ = execute_query(
        text(
            "UPDATE ev_kiralama_sozlesmeleri SET sozlesme_durumu = 'İPTAL EDİLDİ', total_kira = 0, depozito = 0, "
            "kalan_borc = 0 WHERE sozlesme_no = :no;"
        ),
        params={"no": contract_no},
    )
    ok2, _ = execute_query(
        text("UPDATE daireler SET musaitlik_durumu = 'Müsait' WHERE daire_id = :aid;"), params={"aid": info["apartment_id"]}
    )
    if ok1 and ok2:
        log_transaction(employee_id, department_id, "Sözleşme İptal Edildi", f"Konut sözleşmesi iptal edildi #{contract_no}")
        return {"success": True, "message": f"#{contract_no} numaralı sözleşme İPTAL EDİLDİ!"}
    return {"success": False, "message": "İptal işlemi sırasında hata oluştu."}


def confirm_move_out(contract_no: str, employee_id: str, department_id: str) -> dict:
    """Confirm that a tenant is definitely moving out, allowing the unit to be listed as available before the contract's official end date."""
    info = _get_contract_apartment_info(contract_no)
    if info is None:
        return {"success": False, "message": "Sözleşme bulunamadı."}
    if info["status"] in ("İPTAL EDİLDİ", "TAMAMLANDI"):
        return {"success": False, "message": "Bu sözleşme zaten kapanmış, tahliye onayı verilemez."}

    ok, err = execute_query(
        text("UPDATE ev_kiralama_sozlesmeleri SET tahliye_onayi = TRUE WHERE sozlesme_no = :no;"),
        params={"no": contract_no},
    )
    if not ok:
        return {"success": False, "message": f"İşlem başarısız: {err}"}
    log_transaction(
        employee_id, department_id, "Tahliye Onayı Verildi",
        f"Sözleşme #{contract_no} için kiracının taşınacağı onaylandı; daire yeni sözleşmeler için müsait sayılacak.",
    )
    return {"success": True, "message": f"#{contract_no} numaralı sözleşme için tahliye onayı verildi. Daire artık yeni sözleşmeler için müsait görünecek."}


def extend_contract(contract_no: str, new_end_date: date, employee_id: str, department_id: str) -> dict:
    """Extend a contract's end date after checking the extension period for unit-booking conflicts, then recompute totals and add installments."""
    df = run_query(
        text(
            "SELECT daire_id, musteri_id, baslangic_tarihi, bitis_tarihi, sozlesme_durumu, aylik_kira_yrd, "
            "total_kira, odenen_toplam_tutar FROM ev_kiralama_sozlesmeleri WHERE sozlesme_no = :no;"
        ),
        params={"no": contract_no},
    )
    if df is None or df.empty:
        return {"success": False, "message": "Sözleşme bulunamadı."}
    row = df.iloc[0]
    old_end = row["bitis_tarihi"]
    old_start = row["baslangic_tarihi"]
    daire_id = row["daire_id"]
    monthly_rent = float(row["aylik_kira_yrd"]) if row["aylik_kira_yrd"] else 0.0

    if not any(k in str(row["sozlesme_durumu"]).upper() for k in ["DEVAM", "BEKLEME", "AKTİF", "AKTIF"]):
        return {"success": False, "message": "Sadece aktif/devam eden ya da beklemedeki sözleşmeler uzatılabilir."}
    if new_end_date <= old_end:
        return {"success": False, "message": "Yeni bitiş tarihi, mevcut bitiş tarihinden sonra olmalıdır."}

    extension_start = old_end + relativedelta(days=1)
    conflict_df = run_query(
        text(
            """
            SELECT sozlesme_no FROM ev_kiralama_sozlesmeleri
            WHERE daire_id = :aid AND sozlesme_no != :no
              AND LOWER(CAST(sozlesme_durumu AS VARCHAR)) IN ('aktif', 'devam ediyor', 'beklemede')
              AND baslangic_tarihi <= :new_end AND bitis_tarihi >= :ext_start;
            """
        ),
        params={"aid": daire_id, "no": contract_no, "new_end": str(new_end_date), "ext_start": str(extension_start)},
    )
    if conflict_df is not None and not conflict_df.empty:
        return {
            "success": False,
            "message": f"Uzatma yapılamıyor: bu daire için {conflict_df.iloc[0]['sozlesme_no']} numaralı başka bir sözleşme bu tarihlerde zaten mevcut.",
        }

    date_diff = relativedelta(new_end_date, old_start)
    full_months = date_diff.years * 12 + date_diff.months
    new_total_price = round((full_months * monthly_rent) + (date_diff.days * (monthly_rent / 30.0)), 2)
    old_total_price = float(row["total_kira"] or 0)
    already_paid = float(row["odenen_toplam_tutar"] or 0)
    additional_amount = round(new_total_price - old_total_price, 2)

    _ensure_expiry_reminder_column()
    ok, err = execute_query(
        text(
            """
            UPDATE ev_kiralama_sozlesmeleri
            SET bitis_tarihi = :new_end, total_kira = :new_total,
                kalan_borc = :new_total - :paid,
                odeme_durumu = CASE
                    WHEN (:new_total - :paid) <= 0 THEN 'ÖDENDİ'
                    WHEN :paid > 0 THEN 'KISMİ ÖDENDİ'
                    ELSE 'ÖDENMEDİ'
                END,
                bitis_hatirlatma_gonderildi = FALSE
            WHERE sozlesme_no = :no;
            """
        ),
        params={"new_end": str(new_end_date), "new_total": new_total_price, "paid": already_paid, "no": contract_no},
    )
    if not ok:
        return {"success": False, "message": f"Sözleşme uzatılamadı: {err}"}

    if additional_amount > 0:
        plan_df = get_payment_plan(contract_no)
        if plan_df is not None and not plan_df.empty:
            max_taksit_no = int(plan_df["taksit_no"].max())
            extra_rows = _calculate_installments(additional_amount, old_end, monthly_rent, "SURE_BAZLI", None)
            _insert_installments(contract_no, max_taksit_no + 1, extra_rows)

    log_transaction(
        employee_id, department_id, "Sözleşme Uzatıldı",
        f"Sözleşme #{contract_no} bitiş tarihi {old_end} -> {new_end_date} olarak uzatıldı. "
        f"Ek tutar: ₺{additional_amount:,.2f}",
    )
    return {
        "success": True,
        "message": f"#{contract_no} numaralı sözleşme {new_end_date} tarihine kadar uzatıldı! Ek tutar: ₺{additional_amount:,.2f}",
    }


def load_analysis_data() -> pd.DataFrame:
    """Return contract-level data joined with unit, building, and employee info for analytics."""
    query = """
        SELECT
            s.sozlesme_no AS contract_id, s.baslangic_tarihi AS start_date, s.bitis_tarihi AS end_date,
            s.aylik_kira_yrd AS monthly_rent, s.total_kira AS total_revenue, s.depozito AS deposit,
            s.sozlesme_durumu AS status, d.daire_id AS apartment_id, d.daire_no AS unit_no, d.oda_sayisi AS room_count,
            a.apartman_adi AS building_name, a.il AS city, a.ilce AS district, c.calisan_id AS employee_id,
            COALESCE(c.ad_soyad, 'Bilinmeyen Çalışan') AS employee_name, COALESCE(c.aylik_maas, 25000) AS employee_salary,
            COALESCE(m.isim, 'Bilinmeyen Müşteri') AS customer_name
        FROM ev_kiralama_sozlesmeleri s
        LEFT JOIN daireler d ON s.daire_id = d.daire_id
        LEFT JOIN apartmanlar a ON d.apartman_id = a.apartman_id
        LEFT JOIN calisan c ON s.islemi_yapan_calisan_id = c.calisan_id
        LEFT JOIN musteriler m ON s.musteri_id = m.musteri_id;
    """
    return run_query(query)


def load_login_logs() -> pd.DataFrame:
    """Return login log entries for the housing department, most recent first."""
    query = """
        SELECT g.log_id, g.calisan_id AS employee_id, COALESCE(c.ad_soyad, 'Bilinmeyen Kullanıcı') AS employee_name,
               g.departman_id AS department_id, g.basarili_mi AS is_success, g.hata_nedeni AS error_reason, g.tarih AS log_time
        FROM giris_loglari g
        LEFT JOIN calisan c ON g.calisan_id = c.calisan_id
        WHERE UPPER(g.departman_id) IN ('D1', '1') OR UPPER(c.departman_id) IN ('D1', '1') OR g.calisan_id IS NULL
        ORDER BY g.tarih DESC;
    """
    return run_query(query)


def load_transaction_logs() -> pd.DataFrame:
    """Ensure the transaction log table exists and return its entries for the housing department, most recent first."""
    execute_query(
        """
        CREATE TABLE IF NOT EXISTS islem_loglari (
            log_id SERIAL PRIMARY KEY, calisan_id VARCHAR(50), departman_id VARCHAR(10),
            islem_tipi VARCHAR(100), detay TEXT, tarih TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    query = """
        SELECT il.log_id, il.tarih AS log_time, il.calisan_id AS employee_id,
               COALESCE(c.ad_soyad, 'Bilinmeyen Çalışan') AS employee_name, il.islem_tipi AS action_type, il.detay AS details
        FROM islem_loglari il
        LEFT JOIN calisan c ON il.calisan_id = c.calisan_id
        WHERE UPPER(il.departman_id) IN ('D1', '1', 'D3', '3')
        ORDER BY il.tarih DESC;
    """
    return run_query(query)


def get_available_apartment_count(as_of_date: Optional[date] = None) -> int:
    """Count units that existed and were not yet retired as of the given date (default today)."""
    as_of_date = as_of_date or date.today()
    df = run_query(
        text(
            "SELECT COUNT(*) as sayi FROM daireler WHERE "
            "(sisteme_ekleme_tarihi IS NULL OR sisteme_ekleme_tarihi <= :d) "
            "AND (pasif_tarihi IS NULL OR pasif_tarihi > :d);"
        ),
        params={"d": str(as_of_date)},
    )
    return int(df.iloc[0]["sayi"]) if not df.empty else 0


def get_occupied_apartment_count(as_of_date: Optional[date] = None) -> int:
    """Count units under an active, ongoing, or pending contract as of the given date (default today)."""
    as_of_date = as_of_date or date.today()
    df = run_query(
        text(
            """
            SELECT COUNT(DISTINCT daire_id) AS sayi
            FROM ev_kiralama_sozlesmeleri
            WHERE LOWER(CAST(sozlesme_durumu AS VARCHAR)) IN ('aktif', 'devam ediyor', 'beklemede')
              AND baslangic_tarihi <= :d
              AND bitis_tarihi >= :d
              AND COALESCE(tahliye_onayi, false) = false;
            """
        ),
        params={"d": str(as_of_date)},
    )
    return int(df.iloc[0]["sayi"]) if df is not None and not df.empty else 0


def get_department_employee_count() -> int:
    """Count employees belonging to the housing department."""
    df = run_query("SELECT COUNT(*) as sayi FROM calisan WHERE departman_id IN ('D1', '1');")
    return int(df.iloc[0]["sayi"]) if not df.empty else 0