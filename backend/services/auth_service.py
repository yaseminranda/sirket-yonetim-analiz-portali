"""Business logic for authentication: login, account lockout, security question, password reset, and 2FA email codes."""
import random
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from sqlalchemy import text

from config import settings
from database import execute_query, run_query
from services.notification_service import send_login_verification_code, send_security_email


def hash_text(text_val: str) -> str:
    """Hash a plain-text string (password or security answer) with bcrypt."""
    if not text_val:
        return ""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(text_val.encode("utf-8"), salt).decode("utf-8")


def check_hash(plain_text: str, hashed_text: str) -> bool:
    """Check a plain-text value against a bcrypt hash, returning False on any error."""
    if not plain_text or not hashed_text:
        return False
    try:
        return bcrypt.checkpw(plain_text.encode("utf-8"), hashed_text.encode("utf-8"))
    except Exception:
        return False


def _log_login_attempt(employee_id: str, department_id: str, is_successful: bool, error_reason: str = "") -> None:
    """Insert a row into the login attempt log table."""
    query = text(
        """
        INSERT INTO giris_loglari (calisan_id, departman_id, basarili_mi, hata_nedeni, tarih)
        VALUES (:cid, :dept, :success, :error, :log_date);
        """
    )
    execute_query(
        query,
        params={
            "cid": employee_id,
            "dept": department_id,
            "success": is_successful,
            "error": error_reason,
            "log_date": datetime.now(),
        },
    )


def authenticate_user(employee_id: str, password: str) -> dict:
    """Verify employee credentials, enforcing the failed-attempt limit and account lockout.

    Returns a dict: {success, message, user_name?, department_id?, role?}
    """
    try:
        query = text(
            """
            SELECT calisan_id, ad_soyad, departman_id, yetki, sifre_hash, email,
                   COALESCE(hatali_giris_sayisi, 0) AS hatali_giris_sayisi,
                   COALESCE(kilitli_mi, FALSE) AS kilitli_mi,
                   kilit_bitis_zamani
            FROM calisan WHERE calisan_id = :cid;
            """
        )
        df_user = run_query(query, params={"cid": employee_id})

        if df_user is None or df_user.empty:
            _log_login_attempt(employee_id, "UNKNOWN", False, "Geçersiz Çalışan ID")
            return {"success": False, "message": "Geçersiz Çalışan ID veya Şifre."}

        row = df_user.iloc[0]
        emp_id = row["calisan_id"]
        full_name = row["ad_soyad"]
        dept_id = row["departman_id"]
        role = row["yetki"]
        password_hash = row["sifre_hash"]
        employee_email = str(row["email"]).strip() if row["email"] and str(row["email"]).strip() else None
        failed_count = int(row["hatali_giris_sayisi"])
        is_locked = bool(row["kilitli_mi"])
        lock_until = row["kilit_bitis_zamani"]

        if lock_until and isinstance(lock_until, str):
            lock_until = datetime.fromisoformat(lock_until)

        if is_locked or failed_count >= settings.max_failed_attempts:
            if lock_until and datetime.now() < lock_until:
                remaining_mins = int((lock_until - datetime.now()).total_seconds() / 60) + 1
                _log_login_attempt(emp_id, dept_id, False, "Kilitli hesaba erişim denemesi")
                return {
                    "success": False,
                    "message": (
                        f"🔒 Hesabınız Kilitli! {settings.max_failed_attempts} kez hatalı giriş "
                        f"yapıldı. {remaining_mins} dk sonra tekrar deneyin veya "
                        "'Şifremi Unuttum' ile kilidi açın."
                    ),
                }
            elif not lock_until:
                _log_login_attempt(emp_id, dept_id, False, "Kilitli hesaba erişim denemesi")
                return {
                    "success": False,
                    "message": "🔒 Hesabınız Kilitlidir! Lütfen 'Şifremi Unuttum' ile kilidi açınız.",
                }
            else:
                execute_query(
                    text(
                        """
                        UPDATE calisan
                        SET kilitli_mi = FALSE, hatali_giris_sayisi = 0, kilit_bitis_zamani = NULL
                        WHERE calisan_id = :cid;
                        """
                    ),
                    params={"cid": emp_id},
                )
                failed_count = 0

        if check_hash(password, password_hash):
            execute_query(
                text(
                    """
                    UPDATE calisan
                    SET hatali_giris_sayisi = 0, kilitli_mi = FALSE, kilit_bitis_zamani = NULL
                    WHERE calisan_id = :cid;
                    """
                ),
                params={"cid": emp_id},
            )
            _log_login_attempt(emp_id, dept_id, True, "Başarılı Giriş")
            return {
                "success": True,
                "message": f"Hoş geldiniz, {full_name}!",
                "user_name": full_name,
                "employee_id": emp_id,
                "department_id": dept_id,
                "role": role,
            }

        new_failed_count = failed_count + 1

        if new_failed_count >= settings.max_failed_attempts:
            lock_until = datetime.now() + timedelta(minutes=settings.lock_duration_minutes)
            execute_query(
                text(
                    """
                    UPDATE calisan
                    SET hatali_giris_sayisi = :f_count, kilitli_mi = TRUE, kilit_bitis_zamani = :l_until
                    WHERE calisan_id = :cid;
                    """
                ),
                params={"f_count": new_failed_count, "l_until": lock_until, "cid": emp_id},
            )
            _log_login_attempt(
                emp_id, dept_id, False, f"Hesap kilitlendi ({settings.max_failed_attempts} Hatalı Giriş)"
            )

            email_sent = False
            if employee_email:
                email_sent, _ = send_security_email(
                    recipient_email=employee_email,
                    subject="🚨 GÜVENLİK UYARISI: Hesabınız Kilitlendi!",
                    body=(
                        f"Sayın {full_name},\n\nHesabınıza {settings.max_failed_attempts} kez üst üste "
                        f"hatalı şifre girildiği için güvenlik amacıyla "
                        f"{settings.lock_duration_minutes} dakika süreyle kilitlenmiştir.\n\n"
                        "Bu girişi siz yapmadıysanız lütfen sistem yöneticinize bildirin."
                    ),
                )

            return {
                "success": False,
                "message": (
                    f"🚨 {settings.max_failed_attempts} defa hatalı şifre girdiniz. Hesabınız "
                    f"{settings.lock_duration_minutes} dakika süreyle kilitlendi"
                    + (" ve güvenlik e-postası gönderildi!" if email_sent else "!")
                ),
            }

        execute_query(
            text("UPDATE calisan SET hatali_giris_sayisi = :f_count WHERE calisan_id = :cid;"),
            params={"f_count": new_failed_count, "cid": emp_id},
        )
        remaining_attempts = settings.max_failed_attempts - new_failed_count
        _log_login_attempt(emp_id, dept_id, False, f"Hatalı Şifre (Kalan Hak: {remaining_attempts})")
        return {
            "success": False,
            "message": f"Hatalı şifre! Kalan deneme hakkınız: {remaining_attempts}",
        }

    except Exception as exc:  # noqa: BLE001
        return {"success": False, "message": f"Giriş hatası: {exc}"}


def set_security_question(employee_id: str, current_password: str, question: str, answer: str) -> dict:
    """Set the employee's security question/answer after verifying the current password, and email a notice."""
    df_user = run_query(
        text("SELECT sifre_hash, ad_soyad, email FROM calisan WHERE calisan_id = :cid;"),
        params={"cid": employee_id},
    )

    if df_user is None or df_user.empty:
        return {"success": False, "message": "Sistemde bu ID'ye sahip çalışan bulunamadı."}

    if not check_hash(current_password, df_user.iloc[0]["sifre_hash"]):
        return {"success": False, "message": "Mevcut şifreniz hatalı!"}

    full_name = df_user.iloc[0]["ad_soyad"]
    raw_email = df_user.iloc[0]["email"]
    email = str(raw_email).strip() if raw_email and str(raw_email).strip() else None

    answer_hash = hash_text(answer.lower())
    ok, err = execute_query(
        text(
            """
            UPDATE calisan
            SET guvenlik_sorusu = :question, guvenlik_cevabi_hash = :chash
            WHERE calisan_id = :cid;
            """
        ),
        params={"question": question, "chash": answer_hash, "cid": employee_id},
    )
    if ok:
        if email:
            send_security_email(
                recipient_email=email,
                subject="🔐 Güvenlik Sorunuz Güncellendi",
                body=(
                    f"Sayın {full_name},\n\nHesabınızın güvenlik sorusu/cevabı az önce güncellendi.\n\n"
                    "Bu değişikliği siz yapmadıysanız lütfen sistem yöneticinize bildirin."
                ),
            )
        return {"success": True, "message": "Güvenlik sorunuz ve cevabınız başarıyla kaydedildi!"}
    return {"success": False, "message": f"Kayıt sırasında veritabanı hatası oluştu: {err}"}


def request_security_question_code(employee_id: str, current_password: str) -> dict:
    """Step 1 of the security-question-change flow: verify the current password and send an email code."""
    df_user = run_query(text("SELECT sifre_hash FROM calisan WHERE calisan_id = :cid;"), params={"cid": employee_id})
    if df_user is None or df_user.empty:
        return {"success": False, "message": "Sistemde bu ID'ye sahip çalışan bulunamadı."}
    if not check_hash(current_password, df_user.iloc[0]["sifre_hash"]):
        return {"success": False, "message": "Mevcut şifreniz hatalı!"}
    return generate_and_send_login_code(employee_id)


def confirm_security_question_change(
    employee_id: str, current_password: str, question: str, answer: str, code: str
) -> dict:
    """Step 2 of the security-question-change flow: verify the email code, then persist the new question/answer."""
    code_result = verify_login_code(employee_id, code)
    if not code_result["success"]:
        return code_result
    return set_security_question(employee_id, current_password, question, answer)


def get_security_question(employee_id: str) -> dict:
    """First step of password reset: fetch the employee's stored security question."""
    df = run_query(
        text("SELECT guvenlik_sorusu, guvenlik_cevabi_hash FROM calisan WHERE calisan_id = :cid;"),
        params={"cid": employee_id},
    )
    if df is None or df.empty:
        return {"found": False, "message": "Sistemde bu ID'ye sahip çalışan bulunamadı."}

    question = df.iloc[0]["guvenlik_sorusu"]
    answer_hash = df.iloc[0]["guvenlik_cevabi_hash"]
    if not question or not answer_hash:
        return {
            "found": False,
            "message": "Bu hesaba tanımlı bir güvenlik sorusu bulunamadı. Lütfen önce tanımlayın.",
        }
    return {"found": True, "question": question}


def reset_password(employee_id: str, security_answer: str, new_password: str) -> dict:
    """Verify the security answer, then reset the password and clear account lockout, emailing a notice."""
    df = run_query(
        text("SELECT guvenlik_cevabi_hash, ad_soyad, email FROM calisan WHERE calisan_id = :cid;"),
        params={"cid": employee_id},
    )
    if df is None or df.empty:
        return {"success": False, "message": "Sistemde bu ID'ye sahip çalışan bulunamadı."}

    answer_hash = df.iloc[0]["guvenlik_cevabi_hash"]
    if not answer_hash or not check_hash(security_answer.strip().lower(), answer_hash):
        return {"success": False, "message": "Güvenlik sorusunun cevabı yanlış!"}

    full_name = df.iloc[0]["ad_soyad"]
    raw_email = df.iloc[0]["email"]
    email = str(raw_email).strip() if raw_email and str(raw_email).strip() else None

    new_hash = hash_text(new_password)
    ok, err = execute_query(
        text(
            """
            UPDATE calisan
            SET sifre_hash = :hash, hatali_giris_sayisi = 0, kilitli_mi = FALSE, kilit_bitis_zamani = NULL
            WHERE calisan_id = :cid;
            """
        ),
        params={"hash": new_hash, "cid": employee_id},
    )
    if ok:
        if email:
            send_security_email(
                recipient_email=email,
                subject="🔐 Şifreniz Sıfırlandı",
                body=(
                    f"Sayın {full_name},\n\nHesabınızın şifresi az önce 'Şifremi Unuttum' akışıyla "
                    "sıfırlandı ve hesap kilidiniz açıldı.\n\n"
                    "Bu işlemi siz yapmadıysanız lütfen hemen sistem yöneticinize bildirin."
                ),
            )
        return {"success": True, "message": "Şifreniz ve hesabınız başarıyla güncellendi!"}
    return {"success": False, "message": f"Şifre güncellenirken hata oluştu: {err}"}


def request_password_reset_code(employee_id: str, security_answer: str) -> dict:
    """Step 1 of the password-reset flow: verify the security answer and send an email code."""
    df = run_query(
        text("SELECT guvenlik_cevabi_hash FROM calisan WHERE calisan_id = :cid;"),
        params={"cid": employee_id},
    )
    if df is None or df.empty:
        return {"success": False, "message": "Sistemde bu ID'ye sahip çalışan bulunamadı."}
    answer_hash = df.iloc[0]["guvenlik_cevabi_hash"]
    if not answer_hash or not check_hash(security_answer.strip().lower(), answer_hash):
        return {"success": False, "message": "Güvenlik sorusunun cevabı yanlış!"}
    return generate_and_send_login_code(employee_id)


def confirm_password_reset(employee_id: str, security_answer: str, new_password: str, code: str) -> dict:
    """Step 2 of the password-reset flow: verify the email code, then apply the new password."""
    code_result = verify_login_code(employee_id, code)
    if not code_result["success"]:
        return code_result
    return reset_password(employee_id, security_answer, new_password)


def _mask_email(email: str) -> str:
    """Mask the local part of an email address for display (e.g. 'y****a@gmail.com')."""
    try:
        local, domain = email.split("@", 1)
        if len(local) <= 2:
            masked_local = local[0] + "*"
        else:
            masked_local = local[0] + "*" * (len(local) - 2) + local[-1]
        return f"{masked_local}@{domain}"
    except ValueError:
        return email


def _get_employee_email(employee_id: str) -> Optional[str]:
    """Look up an employee's registered email address, if any."""
    df = run_query(text("SELECT email FROM calisan WHERE calisan_id = :cid;"), params={"cid": employee_id})
    if df is None or df.empty:
        return None
    email = df.iloc[0]["email"]
    return str(email).strip() if email and str(email).strip() else None


def get_employee_profile(employee_id: str) -> Optional[dict]:
    """Fetch the current name/department/role for an employee, to embed in a JWT after login verification."""
    df = run_query(
        text("SELECT calisan_id, ad_soyad, departman_id, yetki FROM calisan WHERE calisan_id = :cid;"),
        params={"cid": employee_id},
    )
    if df is None or df.empty:
        return None
    row = df.iloc[0]
    return {
        "employee_id": row["calisan_id"],
        "user_name": row["ad_soyad"],
        "department_id": row["departman_id"],
        "role": row["yetki"],
    }


def generate_and_send_login_code(employee_id: str) -> dict:
    """Generate a 6-digit one-time code, email it to the employee, and store it with an expiry time."""
    email = _get_employee_email(employee_id)
    if not email:
        return {
            "success": False,
            "message": (
                "Bu hesaba tanımlı bir e-posta adresi bulunamadı, doğrulama kodu "
                "gönderilemedi. Lütfen sistem yöneticinize başvurun."
            ),
        }

    code = f"{random.randint(0, 999999):06d}"
    expires_at = datetime.now() + timedelta(minutes=settings.login_verification_code_expire_minutes)

    execute_query(
        text(
            """
            CREATE TABLE IF NOT EXISTS giris_dogrulama_kodlari (
                id SERIAL PRIMARY KEY,
                calisan_id VARCHAR(50) NOT NULL,
                kod VARCHAR(10) NOT NULL,
                olusturulma_zamani TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                son_kullanma_zamani TIMESTAMP NOT NULL,
                kullanildi BOOLEAN DEFAULT FALSE
            );
            """
        )
    )
    ok, err = execute_query(
        text(
            """
            INSERT INTO giris_dogrulama_kodlari (calisan_id, kod, son_kullanma_zamani)
            VALUES (:cid, :kod, :exp);
            """
        ),
        params={"cid": employee_id, "kod": code, "exp": expires_at},
    )
    if not ok:
        return {"success": False, "message": f"Doğrulama kodu oluşturulamadı: {err}"}

    sent_ok, send_err = send_login_verification_code(email, code)
    if not sent_ok:
        return {"success": False, "message": send_err or "Doğrulama kodu e-postası gönderilemedi."}

    return {
        "success": True,
        "message": f"📧 Doğrulama kodu {_mask_email(email)} adresine gönderildi.",
    }


def verify_login_code(employee_id: str, code: str) -> dict:
    """Validate the code entered by the user against the most recently generated code for that employee."""
    df = run_query(
        text(
            """
            SELECT id, kod, son_kullanma_zamani, kullanildi
            FROM giris_dogrulama_kodlari
            WHERE calisan_id = :cid
            ORDER BY olusturulma_zamani DESC
            LIMIT 1;
            """
        ),
        params={"cid": employee_id},
    )
    if df is None or df.empty:
        return {"success": False, "message": "Önce giriş bilgilerinizi tekrar girip yeni bir kod isteyin."}

    row = df.iloc[0]
    if bool(row["kullanildi"]):
        return {"success": False, "message": "Bu kod zaten kullanılmış. Lütfen yeniden giriş yapıp yeni bir kod isteyin."}

    expires_at = row["son_kullanma_zamani"]
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at)
    if datetime.now() > expires_at:
        return {"success": False, "message": "Kodun süresi dolmuş. Lütfen yeniden giriş yapıp yeni bir kod isteyin."}

    if str(row["kod"]).strip() != str(code).strip():
        return {"success": False, "message": "Girdiğiniz kod hatalı. Lütfen tekrar deneyin."}

    execute_query(
        text("UPDATE giris_dogrulama_kodlari SET kullanildi = TRUE WHERE id = :id;"),
        params={"id": int(row["id"])},
    )
    return {"success": True, "message": "Kod doğrulandı."}
