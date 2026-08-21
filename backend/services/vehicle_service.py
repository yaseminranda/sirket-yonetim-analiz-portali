"""Business logic for vehicle fleet management, rental contracts, and related notifications/reporting."""

import re
from datetime import date, timedelta
from typing import Optional, Tuple

import pandas as pd
from sqlalchemy import text

from database import execute_query, run_query
from services import finance_service
from services.notification_service import log_transaction, send_payment_reminder_email, send_sms_notification


def get_customers() -> pd.DataFrame:
    """Return all customers ordered by name."""
    return run_query("SELECT musteri_id, isim, telefon FROM musteriler ORDER BY isim ASC;")


def get_available_vehicles(start_date: date, end_date: date) -> pd.DataFrame:
    """Return active vehicles that have no overlapping non-cancelled/finished contract in the given date range."""
    query = text(
        """
        SELECT
            a.arac_id, a.plaka, am.model_adi AS model, amar.marka_adi AS marka, a.gunluk_ucret
        FROM arabalar a
        LEFT JOIN araba_modelleri am ON a.model_id = am.model_id
        LEFT JOIN araba_markalari amar ON am.marka_id = amar.marka_id
        WHERE a.pasif_tarihi IS NULL
          AND a.arac_id NOT IN (
            SELECT DISTINCT ak.arac_id
            FROM araba_kiralama_sozlesmeleri ak
            WHERE
                LOWER(CAST(ak.sozlesme_durumu AS VARCHAR)) NOT LIKE '%iptal%'
                AND LOWER(CAST(ak.sozlesme_durumu AS VARCHAR)) NOT LIKE '%silindi%'
                AND LOWER(CAST(ak.sozlesme_durumu AS VARCHAR)) NOT LIKE '%tamamlan%'
                AND LOWER(CAST(ak.sozlesme_durumu AS VARCHAR)) NOT LIKE '%bitti%'
                AND (
                    LOWER(CAST(ak.sozlesme_durumu AS VARCHAR)) LIKE '%aktif%'
                    OR LOWER(CAST(ak.sozlesme_durumu AS VARCHAR)) LIKE '%devam%'
                    OR LOWER(CAST(ak.sozlesme_durumu AS VARCHAR)) LIKE '%bekle%'
                )
                AND ak.baslangic_tarihi <= :end_date
                AND ak.bitis_tarihi >= :start_date
        );
        """
    )
    return run_query(query, params={"start_date": str(start_date), "end_date": str(end_date)})


def get_all_contracts() -> pd.DataFrame:
    """Return all rental contracts, first auto-promoting any due PENDING contracts to IN PROGRESS."""
    execute_query(
        text(
            """
            UPDATE araba_kiralama_sozlesmeleri
            SET sozlesme_durumu = 'DEVAM EDIYOR'
            WHERE LOWER(CAST(sozlesme_durumu AS VARCHAR)) LIKE '%bekleme%'
              AND baslangic_tarihi <= CURRENT_DATE;
            """
        )
    )
    query = """
        SELECT
            ak.sozlesme_no, ak.arac_id, a.plaka, amar.marka_adi AS marka, am.model_adi AS model,
            m.isim AS musteri_adi, m.telefon, m.email, ak.musteri_id, c.ad_soyad AS calisan_adi,
            ak.baslangic_tarihi, ak.bitis_tarihi, ak.sure_gun_yrd AS sure_gun,
            ak.total_kira AS toplam_tutar, ak.sozlesme_durumu, ak.kalan_borc
        FROM araba_kiralama_sozlesmeleri ak
        LEFT JOIN arabalar a ON ak.arac_id = a.arac_id
        LEFT JOIN araba_modelleri am ON a.model_id = am.model_id
        LEFT JOIN araba_markalari amar ON am.marka_id = amar.marka_id
        LEFT JOIN musteriler m ON ak.musteri_id = m.musteri_id
        LEFT JOIN calisan c ON ak.islemi_yapan_calisan_id = c.calisan_id
        ORDER BY ak.sozlesme_no DESC;
    """
    return run_query(query)


def _ensure_expiry_reminder_column() -> None:
    """Ensure the flag column used to avoid re-sending contract expiry reminders exists (idempotent)."""
    execute_query(
        text(
            "ALTER TABLE araba_kiralama_sozlesmeleri "
            "ADD COLUMN IF NOT EXISTS bitis_hatirlatma_gonderildi BOOLEAN DEFAULT FALSE;"
        )
    )


def get_expiring_contracts() -> pd.DataFrame:
    """Return open contracts ending within 0-3 days, with customer contact info and days remaining."""
    _ensure_expiry_reminder_column()
    sql = """
        SELECT
            ak.sozlesme_no, ak.musteri_id, m.isim AS musteri_adi, m.telefon, m.email,
            a.plaka, amar.marka_adi AS marka, am.model_adi AS model,
            ak.baslangic_tarihi, ak.bitis_tarihi, ak.sozlesme_durumu,
            COALESCE(ak.bitis_hatirlatma_gonderildi, FALSE) AS hatirlatma_gonderildi
        FROM araba_kiralama_sozlesmeleri ak
        LEFT JOIN arabalar a ON ak.arac_id = a.arac_id
        LEFT JOIN araba_modelleri am ON a.model_id = am.model_id
        LEFT JOIN araba_markalari amar ON am.marka_id = amar.marka_id
        LEFT JOIN musteriler m ON ak.musteri_id = m.musteri_id
        WHERE ak.bitis_tarihi >= CURRENT_DATE
          AND ak.bitis_tarihi <= CURRENT_DATE + INTERVAL '3 days'
          AND UPPER(CAST(ak.sozlesme_durumu AS VARCHAR)) NOT LIKE '%TAMAMLAN%'
          AND UPPER(CAST(ak.sozlesme_durumu AS VARCHAR)) NOT LIKE '%İPTAL%'
          AND UPPER(CAST(ak.sozlesme_durumu AS VARCHAR)) NOT LIKE '%IPTAL%'
          AND UPPER(CAST(ak.sozlesme_durumu AS VARCHAR)) NOT LIKE '%SİLİNDİ%'
          AND UPPER(CAST(ak.sozlesme_durumu AS VARCHAR)) NOT LIKE '%SILINDI%'
          AND UPPER(CAST(ak.sozlesme_durumu AS VARCHAR)) NOT LIKE '%BITTI%'
          AND UPPER(CAST(ak.sozlesme_durumu AS VARCHAR)) NOT LIKE '%BİTTİ%'
        ORDER BY ak.bitis_tarihi ASC;
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
    """Flag a contract as having had its expiry reminder sent."""
    execute_query(
        text("UPDATE araba_kiralama_sozlesmeleri SET bitis_hatirlatma_gonderildi = TRUE WHERE sozlesme_no = :no;"),
        params={"no": contract_no},
    )


def _send_expiry_reminder_for_row(
    row, method: str, employee_id: str, department_id: str
) -> Tuple[bool, str]:
    """Send an expiry reminder (email or SMS) using an already-fetched expiring-contract row, and mark it as sent on success."""
    contract_no = row["sozlesme_no"]
    kalan_gun = int(row["kalan_gun"])
    bitis_str = row["bitis_tarihi"].strftime("%d.%m.%Y")
    arac_str = f"{row['marka']} {row['model']} ({row['plaka']})"

    if method == "email":
        email = row["email"]
        if not email or not str(email).strip():
            return False, f"{row['musteri_adi']} için sistemde kayıtlı bir e-posta adresi bulunamadı."
        subject = f"Araç Kiralama Sözleşmeniz Sona Eriyor - Sözleşme #{contract_no}"
        gun_ifadesi = "bugün sona eriyor" if kalan_gun == 0 else f"bitimine {kalan_gun} gün kaldı"
        body = (
            f"Sayın {row['musteri_adi']},\n\n#{contract_no} numaralı {arac_str} araç kiralama sözleşmenizin "
            f"{gun_ifadesi}.\n\nKalan gün: {kalan_gun} gün\nSözleşme bitiş tarihi: {bitis_str}\n\n"
            "Sözleşmenizi uzatmak/yenilemek isterseniz ya da aracı zamanında teslim etmek için lütfen "
            "bizimle iletişime geçiniz."
        )
        ok, err = send_payment_reminder_email(email, subject, body)
        message = f"📧 Sözleşme bitiş hatırlatma e-postası {email} adresine gönderildi." if ok else (err or "E-posta gönderilemedi.")
    else:
        phone = row["telefon"] or "+90 (555) 000 0000"
        gun_ifadesi = "bugün sona eriyor" if kalan_gun == 0 else f"bitimine {kalan_gun} gün kaldı"
        msg = (
            f"Sayın {row['musteri_adi']}, #{contract_no} numaralı {arac_str} araç kiralama sözleşmenizin "
            f"{gun_ifadesi} (bitiş tarihi: {bitis_str}). Uzatmak ya da aracı teslim etmek için bizimle iletişime geçiniz."
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
    """Send an expiry reminder (email or SMS) for a single expiring contract and mark it as sent on success."""
    df = get_expiring_contracts()
    if df is None or df.empty:
        return False, "Sözleşme bulunamadı ya da bitişine 3 günden az kalan bir sözleşme değil."
    row_df = df[df["sozlesme_no"] == contract_no]
    if row_df.empty:
        return False, "Sözleşme bulunamadı ya da bitişine 3 günden az kalan bir sözleşme değil."
    return _send_expiry_reminder_for_row(row_df.iloc[0], method, employee_id, department_id)


def send_bulk_contract_expiry_reminders(
    employee_id: str = "SYSTEM", department_id: str = "AUTO", method: str = "email"
) -> Tuple[int, int, Optional[str]]:
    """Send an expiry reminder to every contract currently in the expiring list, regardless of prior sends."""
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
    """Send email and SMS expiry reminders once per contract for expiring contracts that have not yet been notified."""
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


def send_overdue_return_notice(
    contract_no: str, method: str = "email", employee_id: str = "SYSTEM", department_id: str = "AUTO"
) -> Tuple[bool, str]:
    """Send an overdue-return notice (email or SMS) for a contract whose end date has passed without completion."""
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
    kapali = any(k in durum for k in ["TAMAMLA", "BITTI", "BİTTİ", "İPTAL", "IPTAL", "SİLİNDİ", "SILINDI"])
    if kalan_gun > 0 or kapali:
        return False, "Bu sözleşmenin teslim süresi henüz dolmamış ya da sözleşme zaten kapatılmış."

    gecikme_gun = abs(kalan_gun)
    bitis_str = bitis.strftime("%d.%m.%Y")
    arac_str = f"{row['marka']} {row['model']} ({row['plaka']})"

    if method == "email":
        email = row.get("email")
        if not email or not str(email).strip():
            return False, f"{row['musteri_adi']} için sistemde kayıtlı bir e-posta adresi bulunamadı."
        subject = f"Aracınızın Teslim Süresi Gecikti - Sözleşme #{contract_no}"
        body = (
            f"Sayın {row['musteri_adi']},\n\n#{contract_no} numaralı {arac_str} kiralık aracınızın teslim "
            f"süresi {gecikme_gun} gün önce ({bitis_str}) dolmuştur.\n\nLütfen aracı en kısa sürede teslim "
            "ediniz, aksi halde ek ücretlendirme uygulanabilir."
        )
        ok, err = send_payment_reminder_email(email, subject, body)
        message = f"📧 Gecikme ihtar e-postası {email} adresine gönderildi." if ok else (err or "E-posta gönderilemedi.")
    else:
        phone = row.get("telefon") or "+90 (555) 000 0000"
        msg = (
            f"Sayın {row['musteri_adi']}, #{contract_no} numaralı kiralık aracınızın ({row['plaka']}) teslim "
            f"süresi {gecikme_gun} gün gecikmiştir. Lütfen aracı acilen teslim ediniz."
        )
        ok, err = send_sms_notification(phone, msg, "GECİKME İHTAR SMS")
        message = f"📲 Gecikme ihtar SMS'i {phone} numarasına gönderildi." if ok else (err or "SMS gönderilemedi.")

    if ok:
        log_transaction(
            employee_id, department_id, "Araç Teslim Gecikme İhtarı Gönderildi",
            f"Sözleşme #{contract_no} için gecikme ihtarı gönderildi ({method}, gecikme: {gecikme_gun} gün).",
        )
    return ok, message


def _next_contract_no() -> str:
    """Generate the next sequential rental contract number in AKS-#### format."""
    res = run_query("SELECT sozlesme_no FROM araba_kiralama_sozlesmeleri;")
    max_num = 0
    if res is not None and not res.empty:
        for no_val in res["sozlesme_no"].dropna():
            parts = str(no_val).strip().split("-")
            if parts and parts[-1].isdigit():
                max_num = max(max_num, int(parts[-1]))
    return f"AKS-{max_num + 1:04d}"


def _next_id(table: str, id_column: str, prefix: str) -> str:
    """Generate the next ID for a table by scanning all existing IDs (regardless of prefix) for the largest embedded number."""
    df = run_query(f"SELECT {id_column} FROM {table};")
    max_num = 0
    if df is not None and not df.empty:
        for val in df[id_column].dropna():
            digits = re.sub(r"[^0-9]", "", str(val).strip())
            if digits:
                max_num = max(max_num, int(digits))
    return f"{prefix}{max_num + 1}"


def _next_vehicle_id() -> str:
    """Generate the next vehicle ID matching the existing 'a1', 'a2', ... format by scanning all current IDs."""
    df = run_query("SELECT arac_id FROM arabalar;")
    max_num = 0
    if df is not None and not df.empty:
        for val in df["arac_id"].dropna():
            digits = re.sub(r"[^0-9]", "", str(val).strip())
            if digits:
                max_num = max(max_num, int(digits))
    return f"A{max_num + 1}"


def get_brands() -> pd.DataFrame:
    """Return all vehicle brands ordered by name."""
    return run_query("SELECT marka_id, marka_adi FROM araba_markalari ORDER BY marka_adi ASC;")


def get_models(marka_id: str) -> pd.DataFrame:
    """Return all models for the given brand ordered by name."""
    return run_query(
        text("SELECT model_id, model_adi FROM araba_modelleri WHERE marka_id = :mid ORDER BY model_adi ASC;"),
        params={"mid": marka_id},
    )


def get_all_fleet() -> pd.DataFrame:
    """Return the full fleet including retired vehicles, for the vehicle-management listing."""
    query = """
        SELECT a.arac_id, a.plaka, amar.marka_adi AS marka, am.model_adi AS model,
               a.gunluk_ucret, a.musaitlik_durumu, a.sisteme_ekleme_tarihi, a.pasif_tarihi
        FROM arabalar a
        LEFT JOIN araba_modelleri am ON a.model_id = am.model_id
        LEFT JOIN araba_markalari amar ON am.marka_id = amar.marka_id
        ORDER BY a.pasif_tarihi NULLS FIRST, a.sisteme_ekleme_tarihi DESC;
    """
    return run_query(query)


def add_vehicle(
    marka_id: Optional[str],
    new_marka_adi: Optional[str],
    model_id: Optional[str],
    new_model_adi: Optional[str],
    plaka: str,
    gunluk_ucret: float,
    employee_id: str,
    department_id: str,
) -> dict:
    """Add a new vehicle to the fleet, optionally creating a new brand and/or model first."""
    if not plaka or gunluk_ucret is None or gunluk_ucret <= 0:
        return {"success": False, "message": "Plaka ve geçerli (0'dan büyük) bir günlük ücret girilmelidir."}

    plaka_df = run_query(text("SELECT arac_id FROM arabalar WHERE UPPER(plaka) = UPPER(:p);"), params={"p": plaka})
    if plaka_df is not None and not plaka_df.empty:
        return {"success": False, "message": f"'{plaka}' plakalı bir araç zaten sistemde kayıtlı."}

    if marka_id is None:
        if not new_marka_adi:
            return {"success": False, "message": "Mevcut bir marka seçmediyseniz yeni marka adını girmelisiniz."}
        marka_id = _next_id("araba_markalari", "marka_id", "M")
        ok, err = execute_query(
            text("INSERT INTO araba_markalari (marka_id, marka_adi) VALUES (:id, :adi);"),
            params={"id": marka_id, "adi": new_marka_adi},
        )
        if not ok:
            return {"success": False, "message": f"Yeni marka eklenemedi: {err}"}

    if model_id is None:
        if not new_model_adi:
            return {"success": False, "message": "Mevcut bir model seçmediyseniz yeni model adını girmelisiniz."}
        model_id = _next_id("araba_modelleri", "model_id", "MO")
        ok, err = execute_query(
            text("INSERT INTO araba_modelleri (model_id, marka_id, model_adi) VALUES (:id, :mid, :adi);"),
            params={"id": model_id, "mid": marka_id, "adi": new_model_adi},
        )
        if not ok:
            return {"success": False, "message": f"Yeni model eklenemedi: {err}"}

    arac_id = _next_vehicle_id()
    ok, err = execute_query(
        text(
            "INSERT INTO arabalar (arac_id, model_id, plaka, musaitlik_durumu, gunluk_ucret, sisteme_ekleme_tarihi) "
            "VALUES (:id, :mid, :plaka, 'Müsait', :ucret, :bugun);"
        ),
        params={
            "id": arac_id, "mid": model_id, "plaka": plaka.upper(), "ucret": gunluk_ucret,
            "bugun": date.today().strftime("%Y-%m-%d"),
        },
    )
    if not ok:
        return {"success": False, "message": f"Araç eklenemedi: {err}"}

    log_transaction(employee_id, department_id, "Yeni Araç Eklendi", f"Yeni araç filoya eklendi: {plaka} (ID: {arac_id})")
    return {"success": True, "message": f"'{plaka}' plakalı araç filoya başarıyla eklendi! (Araç ID: {arac_id})"}


def retire_vehicle(arac_id: str, retire_date: date, employee_id: str, department_id: str) -> dict:
    """Retire a vehicle from the fleet by setting its retirement date, so date-based fleet counts stay accurate."""
    df = run_query(text("SELECT plaka, pasif_tarihi, sisteme_ekleme_tarihi FROM arabalar WHERE arac_id = :id;"), params={"id": arac_id})
    if df is None or df.empty:
        return {"success": False, "message": "Araç bulunamadı."}
    if df.iloc[0]["pasif_tarihi"] is not None:
        return {"success": False, "message": "Bu araç zaten pasif (filodan çıkarılmış) durumda."}
    ekleme_tarihi = df.iloc[0]["sisteme_ekleme_tarihi"]
    if ekleme_tarihi is not None and retire_date < ekleme_tarihi:
        return {"success": False, "message": "Çıkış tarihi, aracın sisteme eklendiği tarihten önce olamaz."}

    ok, err = execute_query(
        text("UPDATE arabalar SET pasif_tarihi = :d, musaitlik_durumu = 'Pasif' WHERE arac_id = :id;"),
        params={"d": str(retire_date), "id": arac_id},
    )
    if not ok:
        return {"success": False, "message": f"İşlem başarısız: {err}"}
    log_transaction(
        employee_id, department_id, "Araç Filodan Çıkarıldı",
        f"Araç filodan çıkarıldı: {df.iloc[0]['plaka']} (ID: {arac_id}), tarih: {retire_date}",
    )
    return {"success": True, "message": f"Araç ({df.iloc[0]['plaka']}) filodan başarıyla çıkarıldı."}


def _add_driver_to_contract(
    contract_no: str, sira: int, name: Optional[str], phone: Optional[str], email: Optional[str], tc: Optional[str]
) -> None:
    """Create a driver record and link it to a contract as an additional driver, if a name was provided."""
    if not name:
        return
    max_res = run_query("SELECT COALESCE(MAX(sofor_id), 0) AS max_id FROM soforler;")
    new_id = int(max_res.iloc[0]["max_id"]) + 1 if max_res is not None and not max_res.empty else 1
    execute_query(
        text(
            "INSERT INTO soforler (sofor_id, ad_soyad, telefon, email, tc_kimlik_no) "
            "VALUES (:id, :name, :phone, :email, :tc);"
        ),
        params={"id": new_id, "name": name, "phone": phone or "", "email": email or "", "tc": tc or ""},
    )
    execute_query(
        text("INSERT INTO arac_sozlesme_soforler (sozlesme_no, sofor_id, sira) VALUES (:no, :sofor_id, :sira);"),
        params={"no": contract_no, "sofor_id": new_id, "sira": sira},
    )


def _check_driver_fields_complete(
    name: Optional[str], phone: Optional[str], email: Optional[str], tc: Optional[str], label: str
) -> Optional[str]:
    """Validate that an optional additional driver's fields are either all empty or all filled in."""
    values = [name, phone, email, tc]
    filled = [v for v in values if v and str(v).strip()]
    if 0 < len(filled) < len(values):
        return (
            f"{label} bilgilerini eklemeye başladıysanız Ad Soyad, Telefon, E-posta ve Kimlik No "
            "alanlarının tümünü doldurmanız gerekiyor (ya da bu şoförü tamamen boş bırakabilirsiniz)."
        )
    return None


def create_contract(
    customer_id: Optional[int],
    new_customer_name: Optional[str],
    new_customer_phone: Optional[str],
    new_customer_email: Optional[str],
    new_customer_tc: Optional[str],
    vehicle_id: str,
    start_date: date,
    end_date: date,
    down_payment_confirmed: bool,
    employee_id: str,
    department_id: str,
    driver1_name: Optional[str] = None,
    driver1_phone: Optional[str] = None,
    driver1_email: Optional[str] = None,
    driver1_tc: Optional[str] = None,
    driver2_name: Optional[str] = None,
    driver2_phone: Optional[str] = None,
    driver2_email: Optional[str] = None,
    driver2_tc: Optional[str] = None,
) -> dict:
    """Create a new vehicle rental contract, enforcing the 50% down payment rule and re-checking availability."""
    if end_date < start_date:
        return {"success": False, "message": "Bitiş tarihi başlangıç tarihinden önce olamaz!"}

    driver_error = _check_driver_fields_complete(
        driver1_name, driver1_phone, driver1_email, driver1_tc, "1. Ek Şoför"
    ) or _check_driver_fields_complete(
        driver2_name, driver2_phone, driver2_email, driver2_tc, "2. Ek Şoför"
    )
    if driver_error:
        return {"success": False, "message": driver_error}

    vehicle_df = run_query(
        text("SELECT gunluk_ucret FROM arabalar WHERE arac_id = :vid;"), params={"vid": vehicle_id}
    )
    if vehicle_df is None or vehicle_df.empty:
        return {"success": False, "message": "Seçilen araç bulunamadı."}
    daily_rate = float(vehicle_df.iloc[0]["gunluk_ucret"])

    total_days = (end_date - start_date).days or 1
    total_price = round(total_days * daily_rate, 2)
    min_down_payment = round(total_price * 0.50, 2)

    if not down_payment_confirmed:
        return {"success": False, "message": "Kiralamayı başlatmak için en az %50 ön ödeme alınması zorunludur!"}

    overlap_df = run_query(
        text(
            """
            SELECT COUNT(*) AS adet
            FROM araba_kiralama_sozlesmeleri
            WHERE arac_id = :vid
              AND LOWER(CAST(sozlesme_durumu AS VARCHAR)) NOT LIKE '%iptal%'
              AND LOWER(CAST(sozlesme_durumu AS VARCHAR)) NOT LIKE '%silindi%'
              AND LOWER(CAST(sozlesme_durumu AS VARCHAR)) NOT LIKE '%tamamlan%'
              AND LOWER(CAST(sozlesme_durumu AS VARCHAR)) NOT LIKE '%bitti%'
              AND baslangic_tarihi <= :end_date
              AND bitis_tarihi >= :start_date;
            """
        ),
        params={"vid": vehicle_id, "start_date": str(start_date), "end_date": str(end_date)},
    )
    if overlap_df is not None and not overlap_df.empty and int(overlap_df.iloc[0]["adet"]) > 0:
        return {
            "success": False,
            "message": (
                "Üzgünüz, seçtiğiniz araç bu tarihler için az önce başka bir çalışan "
                "tarafından kiralandı. Lütfen müsait araç listesini yenileyip tekrar deneyin."
            ),
        }

    if customer_id is None:
        if not new_customer_name or not new_customer_phone or not new_customer_email or not new_customer_tc:
            return {
                "success": False,
                "message": "Yeni müşterinin Ad Soyad, Telefon, E-posta ve Kimlik No (TC/Pasaport) bilgilerinin tümünü doldurun.",
            }
        max_res = run_query("SELECT COALESCE(MAX(musteri_id), 0) AS max_id FROM musteriler;")
        new_id = int(max_res.iloc[0]["max_id"]) + 1 if max_res is not None and not max_res.empty else 1
        ok, err = execute_query(
            text(
                "INSERT INTO musteriler (musteri_id, isim, telefon, email, tc_kimlik_no, kayit_tarihi) "
                "VALUES (:id, :name, :phone, :email, :tc, :today);"
            ),
            params={
                "id": new_id,
                "name": new_customer_name,
                "phone": new_customer_phone,
                "email": new_customer_email,
                "tc": new_customer_tc,
                "today": date.today().strftime("%Y-%m-%d"),
            },
        )
        if not ok:
            return {"success": False, "message": f"Müşteri eklenemedi: {err}"}
        customer_id = new_id
        log_transaction(employee_id, department_id, "Yeni Müşteri Eklendi", f"Yeni müşteri eklendi: {new_customer_name} (ID: {customer_id})")

    contract_no = _next_contract_no()
    initial_status = "BEKLEMEDE" if start_date > date.today() else "DEVAM EDIYOR"
    remaining = total_price - min_down_payment

    ok, err = execute_query(
        text(
            """
            INSERT INTO araba_kiralama_sozlesmeleri
            (sozlesme_no, musteri_id, arac_id, islemi_yapan_calisan_id, baslangic_tarihi, bitis_tarihi,
             sure_gun_yrd, total_kira, sozlesme_durumu, odenen_toplam_tutar, kalan_borc, odeme_durumu)
            VALUES (:no, :cust, :vid, :emp, :start, :end, :days, :total, :status, :down, :remaining, 'KISMİ ÖDENDİ');
            """
        ),
        params={
            "no": contract_no, "cust": customer_id, "vid": vehicle_id, "emp": employee_id,
            "start": str(start_date), "end": str(end_date), "days": total_days, "total": total_price,
            "status": initial_status, "down": min_down_payment, "remaining": remaining,
        },
    )
    if not ok:
        return {"success": False, "message": f"Sözleşme oluşturulamadı: {err}"}

    execute_query(text("UPDATE arabalar SET musaitlik_durumu = 'Kirada' WHERE arac_id = :vid;"), params={"vid": vehicle_id})

    finance_service.record_payment(
        contract_no=contract_no, category="ARAC", customer_id=customer_id, amount_paid=min_down_payment,
        payment_type="ÖN_ÖDEME", description="Araç kiralama başlangıcı %50 ön ödeme tahsilatı",
    )

    _add_driver_to_contract(contract_no, 1, driver1_name, driver1_phone, driver1_email, driver1_tc)
    _add_driver_to_contract(contract_no, 2, driver2_name, driver2_phone, driver2_email, driver2_tc)

    log_transaction(
        employee_id, department_id, "Yeni Sözleşme Oluşturuldu",
        f"Sözleşme #{contract_no} oluşturuldu. Toplam Tutar: ₺{total_price:,.2f}, Alınan Ön Ödeme: ₺{min_down_payment:,.2f}",
    )

    return {
        "success": True,
        "message": f"#{contract_no} numaralı araç sözleşmesi başarıyla oluşturuldu!",
        "contract_no": contract_no,
        "total_price": total_price,
        "down_payment": min_down_payment,
    }


def _get_contract_vehicle_status(contract_no: str) -> Optional[dict]:
    """Return the vehicle ID and uppercased status for a contract, or None if it does not exist."""
    df = run_query(
        text("SELECT arac_id, sozlesme_durumu FROM araba_kiralama_sozlesmeleri WHERE sozlesme_no = :no;"),
        params={"no": contract_no},
    )
    if df is None or df.empty:
        return None
    return {"vehicle_id": df.iloc[0]["arac_id"], "status": str(df.iloc[0]["sozlesme_durumu"]).upper()}


def complete_contract(contract_no: str, employee_id: str, department_id: str) -> dict:
    """Mark a contract as completed and free up its vehicle, blocking completion while debt remains unpaid."""
    info = _get_contract_vehicle_status(contract_no)
    if info is None:
        return {"success": False, "message": "Sözleşme bulunamadı."}

    has_debt, debt_amount = finance_service.check_debt_status(contract_no, "ARAC")
    if has_debt:
        return {"success": False, "message": f"Araç teslim alınamaz! Ödenmemiş ₺{debt_amount:,.2f} borç bulunuyor."}

    ok1, _ = execute_query(
        text("UPDATE araba_kiralama_sozlesmeleri SET sozlesme_durumu = 'TAMAMLANDI' WHERE sozlesme_no = :no;"),
        params={"no": contract_no},
    )
    ok2, _ = execute_query(
        text("UPDATE arabalar SET musaitlik_durumu = 'Müsait' WHERE arac_id = :vid;"),
        params={"vid": info["vehicle_id"]},
    )
    if ok1 and ok2:
        log_transaction(employee_id, department_id, "Sözleşme Tamamlandı", f"Araç kiralama sözleşmesi tamamlandı #{contract_no}")
        return {"success": True, "message": f"#{contract_no} numaralı sözleşme TAMAMLANDI, araç teslim alındı!"}
    return {"success": False, "message": "Sözleşme kapatılırken veritabanı hatası oluştu."}


def cancel_contract(contract_no: str, employee_id: str, department_id: str) -> dict:
    """Cancel a contract that has not yet started, reversing any payments already made and freeing the vehicle."""
    info = _get_contract_vehicle_status(contract_no)
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

    cust_df = run_query(
        text("SELECT musteri_id FROM araba_kiralama_sozlesmeleri WHERE sozlesme_no = :no;"), params={"no": contract_no}
    )
    if cust_df is not None and not cust_df.empty:
        finance_service.reverse_contract_payments(contract_no, "ARAC", int(cust_df.iloc[0]["musteri_id"]))

    ok1, _ = execute_query(
        text(
            "UPDATE araba_kiralama_sozlesmeleri SET sozlesme_durumu = 'İPTAL EDİLDİ', total_kira = 0, "
            "kalan_borc = 0 WHERE sozlesme_no = :no;"
        ),
        params={"no": contract_no},
    )
    ok2, _ = execute_query(
        text("UPDATE arabalar SET musaitlik_durumu = 'Müsait' WHERE arac_id = :vid;"),
        params={"vid": info["vehicle_id"]},
    )
    if ok1 and ok2:
        log_transaction(employee_id, department_id, "Sözleşme İptal Edildi", f"Araç kiralama sözleşmesi iptal edildi #{contract_no}")
        return {"success": True, "message": f"#{contract_no} numaralı sözleşme İPTAL EDİLDİ!"}
    return {"success": False, "message": "İptal işlemi sırasında hata oluştu."}


def extend_contract(contract_no: str, new_end_date: date, employee_id: str, department_id: str) -> dict:
    """Extend a contract's end date, blocking the extension if the vehicle is already booked in the new period."""
    df = run_query(
        text(
            "SELECT arac_id, baslangic_tarihi, bitis_tarihi, sozlesme_durumu, total_kira, "
            "odenen_toplam_tutar FROM araba_kiralama_sozlesmeleri WHERE sozlesme_no = :no;"
        ),
        params={"no": contract_no},
    )
    if df is None or df.empty:
        return {"success": False, "message": "Sözleşme bulunamadı."}
    row = df.iloc[0]
    old_start, old_end = row["baslangic_tarihi"], row["bitis_tarihi"]
    arac_id = row["arac_id"]

    if not any(k in str(row["sozlesme_durumu"]).upper() for k in ["DEVAM", "BEKLEME", "AKTİF", "AKTIF"]):
        return {"success": False, "message": "Sadece aktif/devam eden ya da beklemedeki sözleşmeler uzatılabilir."}
    if new_end_date <= old_end:
        return {"success": False, "message": "Yeni bitiş tarihi, mevcut bitiş tarihinden sonra olmalıdır."}

    vehicle_df = run_query(text("SELECT gunluk_ucret FROM arabalar WHERE arac_id = :vid;"), params={"vid": arac_id})
    if vehicle_df is None or vehicle_df.empty:
        return {"success": False, "message": "Araç bulunamadı."}
    daily_rate = float(vehicle_df.iloc[0]["gunluk_ucret"])

    extension_start = old_end + timedelta(days=1)
    conflict_df = run_query(
        text(
            """
            SELECT sozlesme_no FROM araba_kiralama_sozlesmeleri
            WHERE arac_id = :vid AND sozlesme_no != :no
              AND LOWER(CAST(sozlesme_durumu AS VARCHAR)) IN ('aktif', 'devam ediyor', 'beklemede')
              AND baslangic_tarihi <= :new_end AND bitis_tarihi >= :ext_start;
            """
        ),
        params={"vid": arac_id, "no": contract_no, "new_end": str(new_end_date), "ext_start": str(extension_start)},
    )
    if conflict_df is not None and not conflict_df.empty:
        return {
            "success": False,
            "message": f"Uzatma yapılamıyor: bu araç için {conflict_df.iloc[0]['sozlesme_no']} numaralı başka bir sözleşme bu tarihlerde zaten mevcut.",
        }

    total_days = (new_end_date - old_start).days or 1
    new_total_price = round(total_days * daily_rate, 2)
    already_paid = float(row["odenen_toplam_tutar"] or 0)
    additional_amount = round(new_total_price - float(row["total_kira"] or 0), 2)

    _ensure_expiry_reminder_column()
    ok, err = execute_query(
        text(
            """
            UPDATE araba_kiralama_sozlesmeleri
            SET bitis_tarihi = :new_end, sure_gun_yrd = :days, total_kira = :new_total,
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
        params={
            "new_end": str(new_end_date), "days": total_days, "new_total": new_total_price,
            "paid": already_paid, "no": contract_no,
        },
    )
    if not ok:
        return {"success": False, "message": f"Sözleşme uzatılamadı: {err}"}

    log_transaction(
        employee_id, department_id, "Sözleşme Uzatıldı",
        f"Sözleşme #{contract_no} bitiş tarihi {old_end} -> {new_end_date} olarak uzatıldı. "
        f"Ek tutar: ₺{additional_amount:,.2f}",
    )
    return {
        "success": True,
        "message": f"#{contract_no} numaralı sözleşme {new_end_date} tarihine kadar uzatıldı! Ek tutar: ₺{additional_amount:,.2f}",
    }


def change_vehicle(
    contract_no: str, new_vehicle_id: str, change_date: date, reason: str, employee_id: str, department_id: str
) -> dict:
    """Swap the vehicle on an active contract by closing it at the change date and opening a linked follow-up contract."""
    old_df = run_query(
        text(
            "SELECT musteri_id, arac_id, baslangic_tarihi, bitis_tarihi, sozlesme_durumu "
            "FROM araba_kiralama_sozlesmeleri WHERE sozlesme_no = :no;"
        ),
        params={"no": contract_no},
    )
    if old_df is None or old_df.empty:
        return {"success": False, "message": "Sözleşme bulunamadı."}
    old = old_df.iloc[0]
    old_start, old_end = old["baslangic_tarihi"], old["bitis_tarihi"]
    musteri_id = int(old["musteri_id"])

    if not any(k in str(old["sozlesme_durumu"]).upper() for k in ["DEVAM", "BEKLEME", "AKTİF", "AKTIF"]):
        return {"success": False, "message": "Sadece aktif/devam eden ya da beklemedeki sözleşmelerde araç değişimi yapılabilir."}
    if not (old_start <= change_date <= old_end):
        return {"success": False, "message": "Değişim tarihi, sözleşmenin başlangıç-bitiş aralığında olmalıdır."}
    if new_vehicle_id == old["arac_id"]:
        return {"success": False, "message": "Yeni araç, mevcut araçla aynı olamaz."}

    available_df = get_available_vehicles(change_date, old_end)
    if available_df is None or available_df.empty or new_vehicle_id not in available_df["arac_id"].values:
        return {"success": False, "message": "Seçilen araç, bu tarih aralığında müsait değil."}

    ok1, err1 = execute_query(
        text(
            "UPDATE araba_kiralama_sozlesmeleri SET bitis_tarihi = :change_date, "
            "sure_gun_yrd = :days WHERE sozlesme_no = :no;"
        ),
        params={"change_date": str(change_date), "days": str((change_date - old_start).days or 1), "no": contract_no},
    )
    if not ok1:
        return {"success": False, "message": f"Eski sözleşme güncellenemedi: {err1}"}

    new_contract_no = _next_contract_no()
    new_days = (old_end - change_date).days or 1
    initial_status = "BEKLEMEDE" if change_date > date.today() else "DEVAM EDIYOR"
    ok2, err2 = execute_query(
        text(
            """
            INSERT INTO araba_kiralama_sozlesmeleri
            (sozlesme_no, musteri_id, arac_id, islemi_yapan_calisan_id, baslangic_tarihi, bitis_tarihi,
             sure_gun_yrd, total_kira, sozlesme_durumu, odenen_toplam_tutar, kalan_borc, odeme_durumu,
             onceki_sozlesme_no, degisim_nedeni)
            VALUES
            (:no, :cust, :vid, :emp, :start, :end, :days, 0, :status, 0, 0, 'ÖDENDİ', :onceki, :neden);
            """
        ),
        params={
            "no": new_contract_no, "cust": musteri_id, "vid": new_vehicle_id, "emp": employee_id,
            "start": str(change_date), "end": str(old_end), "days": str(new_days), "status": initial_status,
            "onceki": contract_no, "neden": reason or "",
        },
    )
    if not ok2:
        return {"success": False, "message": f"Yeni sözleşme oluşturulamadı: {err2}"}

    execute_query(text("UPDATE arabalar SET musaitlik_durumu = 'Müsait' WHERE arac_id = :vid;"), params={"vid": old["arac_id"]})
    execute_query(text("UPDATE arabalar SET musaitlik_durumu = 'Kirada' WHERE arac_id = :vid;"), params={"vid": new_vehicle_id})

    execute_query(
        text(
            """
            INSERT INTO arac_sozlesme_soforler (sozlesme_no, sofor_id, sira)
            SELECT :new_no, sofor_id, sira FROM arac_sozlesme_soforler WHERE sozlesme_no = :old_no;
            """
        ),
        params={"new_no": new_contract_no, "old_no": contract_no},
    )

    log_transaction(
        employee_id, department_id, "Araç Değişimi Yapıldı",
        f"Sözleşme #{contract_no} için araç değişimi: yeni sözleşme #{new_contract_no}, "
        f"eski araç {old['arac_id']} -> yeni araç {new_vehicle_id}. Neden: {reason or '-'}",
    )
    return {
        "success": True,
        "message": f"Araç değişimi tamamlandı! Yeni sözleşme: #{new_contract_no}",
        "contract_no": new_contract_no,
    }


def load_analysis_data() -> pd.DataFrame:
    """Return a denormalized dataset of contracts joined with vehicle, employee, and customer info for analytics."""
    query = """
        SELECT
            ak.sozlesme_no AS contract_id, ak.baslangic_tarihi AS start_date, ak.bitis_tarihi AS end_date,
            ak.sure_gun_yrd AS rental_duration, ak.total_kira AS total_price, ak.sozlesme_durumu AS contract_status,
            a.arac_id AS vehicle_id, a.plaka AS plate, a.gunluk_ucret AS daily_rate,
            am.model_adi AS model, amar.marka_adi AS brand,
            c.calisan_id AS employee_id, c.departman_id AS department_id,
            COALESCE(c.ad_soyad, 'Bilinmeyen Çalışan') AS employee_name,
            COALESCE(c.aylik_maas, 0) AS employee_salary,
            m.musteri_id AS customer_id, COALESCE(m.isim, 'Bilinmeyen Müşteri') AS customer_name
        FROM araba_kiralama_sozlesmeleri ak
        LEFT JOIN arabalar a ON ak.arac_id = a.arac_id
        LEFT JOIN araba_modelleri am ON a.model_id = am.model_id
        LEFT JOIN araba_markalari amar ON am.marka_id = amar.marka_id
        LEFT JOIN calisan c ON ak.islemi_yapan_calisan_id = c.calisan_id
        LEFT JOIN musteriler m ON ak.musteri_id = m.musteri_id;
    """
    return run_query(query)


def load_login_logs() -> pd.DataFrame:
    """Return login log entries for department D2 (or unassigned) users, most recent first."""
    query = """
        SELECT gl.log_id, gl.calisan_id AS employee_id, COALESCE(c.ad_soyad, 'Bilinmeyen Kullanıcı') AS employee_name,
               gl.departman_id AS department_id, gl.basarili_mi AS is_success, gl.hata_nedeni AS error_reason,
               gl.tarih AS log_time
        FROM giris_loglari gl
        LEFT JOIN calisan c ON gl.calisan_id = c.calisan_id
        WHERE UPPER(c.departman_id) IN ('D2', '2') OR UPPER(gl.departman_id) IN ('D2', '2') OR gl.calisan_id IS NULL
        ORDER BY gl.tarih DESC;
    """
    return run_query(query)


def load_transaction_logs() -> pd.DataFrame:
    """Ensure the transaction log table exists and return log entries for departments D2/D3, most recent first."""
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
        WHERE UPPER(il.departman_id) IN ('D2', '2', 'D3', '3')
        ORDER BY il.tarih DESC;
    """
    return run_query(query)


def get_available_vehicle_count(as_of_date: Optional[date] = None) -> int:
    """Return the count of vehicles present in the fleet (added and not yet retired) as of the given date, default today."""
    as_of_date = as_of_date or date.today()
    df = run_query(
        text(
            "SELECT COUNT(*) as sayi FROM arabalar WHERE "
            "(sisteme_ekleme_tarihi IS NULL OR sisteme_ekleme_tarihi <= :d) "
            "AND (pasif_tarihi IS NULL OR pasif_tarihi > :d);"
        ),
        params={"d": str(as_of_date)},
    )
    return int(df.iloc[0]["sayi"]) if not df.empty else 0


def get_occupied_vehicle_count(as_of_date: Optional[date] = None) -> int:
    """Return the count of distinct vehicles under an active (non-cancelled/completed) contract as of the given date."""
    as_of_date = as_of_date or date.today()
    df = run_query(
        text(
            """
            SELECT COUNT(DISTINCT arac_id) AS sayi
            FROM araba_kiralama_sozlesmeleri
            WHERE LOWER(CAST(sozlesme_durumu AS VARCHAR)) NOT LIKE '%iptal%'
              AND LOWER(CAST(sozlesme_durumu AS VARCHAR)) NOT LIKE '%silindi%'
              AND LOWER(CAST(sozlesme_durumu AS VARCHAR)) NOT LIKE '%tamamlan%'
              AND LOWER(CAST(sozlesme_durumu AS VARCHAR)) NOT LIKE '%bitti%'
              AND baslangic_tarihi <= :d
              AND bitis_tarihi >= :d;
            """
        ),
        params={"d": str(as_of_date)},
    )
    return int(df.iloc[0]["sayi"]) if df is not None and not df.empty else 0


def get_department_employee_count() -> int:
    """Return the number of employees belonging to department D2."""
    df = run_query("SELECT COUNT(*) as sayi FROM calisan WHERE departman_id IN ('D2', '2');")
    return int(df.iloc[0]["sayi"]) if not df.empty else 0
