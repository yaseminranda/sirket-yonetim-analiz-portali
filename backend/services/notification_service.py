"""Backend service for sending SMS/email notifications (payment reminders, security alerts, login codes) and logging transactions."""
import logging
import smtplib
from datetime import datetime
from email.message import EmailMessage
from typing import Optional, Tuple

import httpx
from sqlalchemy import text

from config import settings
from database import execute_query

logger = logging.getLogger("notifications")


def send_sms_notification(phone_number: str, message: str, notification_type: str = "BİLDİRİM") -> Tuple[bool, Optional[str]]:
    """Send an SMS notification (e.g. payment reminder); fails with a clear message if no SMS provider is configured in `.env`."""
    logger.info("[SMS -> %s] (%s) %s", phone_number, notification_type, message)

    if not settings.sms_api_url or not settings.sms_api_key:
        return False, (
            "SMS gönderimi şu anda devre dışı: sistemde henüz gerçek bir SMS sağlayıcısı "
            "yapılandırılmamış. Bu bildirim yerine e-posta ile gönderebilir ya da sistem "
            "yöneticinizin .env dosyasına SMS ayarlarını eklemesini bekleyebilirsiniz."
        )

    try:
        response = httpx.post(
            settings.sms_api_url,
            json={
                "api_key": settings.sms_api_key,
                "sender": settings.sms_sender_id,
                "phone": phone_number,
                "message": message,
            },
            timeout=15,
        )
        response.raise_for_status()
        logger.info("[SMS -> %s] Gönderildi.", phone_number)
        return True, None
    except Exception as exc:
        logger.error("SMS gönderilemedi: %s", exc)
        return False, f"SMS gönderilemedi: {exc}"


def send_payment_reminder_email(recipient_email: str, subject: str, body: str) -> Tuple[bool, Optional[str]]:
    """Send a payment reminder as a real email using the configured SMTP settings."""
    if not settings.smtp_user or not settings.smtp_password:
        logger.error("SMTP ayarları (.env: SMTP_USER / SMTP_PASSWORD) tanımlı değil, hatırlatma e-postası gönderilemedi.")
        return False, "E-posta gönderim ayarları henüz yapılandırılmamış. Lütfen sistem yöneticisine başvurun."

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_user
    msg["To"] = recipient_email
    msg.set_content(body)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
        logger.info("[E-POSTA -> %s] Ödeme hatırlatması gönderildi.", recipient_email)
        return True, None
    except Exception as exc:
        logger.error("Ödeme hatırlatma e-postası gönderilemedi: %s", exc)
        return False, f"E-posta gönderilemedi: {exc}"


def send_email_notification(
    recipient_email: str, subject: str, content: str, notification_type: str = "GÜVENLİK"
) -> None:
    """Log-only stub that simulates sending an email; kept for backward compatibility, superseded by `send_security_email()`."""
    logger.info("[E-POSTA -> %s] (%s) Konu: %s", recipient_email, notification_type, subject)


def send_security_email(recipient_email: str, subject: str, body: str) -> Tuple[bool, Optional[str]]:
    """Send a real security-related email (account lock, password reset, security question change) via SMTP."""
    if not settings.smtp_user or not settings.smtp_password:
        logger.error("SMTP ayarları (.env: SMTP_USER / SMTP_PASSWORD) tanımlı değil, güvenlik e-postası gönderilemedi.")
        return False, "E-posta gönderim ayarları henüz yapılandırılmamış. Lütfen sistem yöneticisine başvurun."

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_user
    msg["To"] = recipient_email
    msg.set_content(body)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
        logger.info("[E-POSTA -> %s] Güvenlik bildirimi gönderildi. Konu: %s", recipient_email, subject)
        return True, None
    except Exception as exc:
        logger.error("Güvenlik e-postası gönderilemedi: %s", exc)
        return False, f"E-posta gönderilemedi: {exc}"


def send_login_verification_code(recipient_email: str, code: str) -> Tuple[bool, Optional[str]]:
    """Send the one-time 2FA login verification code via SMTP email; fails with a clear message if SMTP is not configured."""
    if not settings.smtp_user or not settings.smtp_password:
        logger.error("SMTP ayarları (.env: SMTP_USER / SMTP_PASSWORD) tanımlı değil, doğrulama kodu gönderilemedi.")
        return False, "E-posta gönderim ayarları henüz yapılandırılmamış. Lütfen sistem yöneticisine başvurun."

    msg = EmailMessage()
    msg["Subject"] = "Giriş Doğrulama Kodunuz"
    msg["From"] = settings.smtp_user
    msg["To"] = recipient_email
    msg.set_content(
        "Şirket Yönetim & Analiz Portalı'na giriş için doğrulama kodunuz:\n\n"
        f"    {code}\n\n"
        f"Bu kod {settings.login_verification_code_expire_minutes} dakika boyunca geçerlidir. "
        "Bu girişi siz yapmadıysanız lütfen sistem yöneticinize bildirin."
    )

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
        logger.info("[E-POSTA -> %s] Doğrulama kodu gönderildi.", recipient_email)
        return True, None
    except Exception as exc:
        logger.error("Doğrulama kodu e-postası gönderilemedi: %s", exc)
        return False, f"E-posta gönderilemedi: {exc}"


def log_transaction(employee_id: str, department_id: str, action_type: str, details: str) -> None:
    """Write a transaction record (new contract, new customer, cancellation, etc.) to the database log."""
    query = text(
        """
        CREATE TABLE IF NOT EXISTS islem_loglari (
            log_id SERIAL PRIMARY KEY,
            calisan_id VARCHAR(50),
            departman_id VARCHAR(10),
            islem_tipi VARCHAR(100),
            detay TEXT,
            tarih TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO islem_loglari (calisan_id, departman_id, islem_tipi, detay, tarih)
        VALUES (:cid, :dept, :action, :details, :log_date);
        """
    )
    params = {
        "cid": employee_id,
        "dept": department_id,
        "action": action_type,
        "details": details,
        "log_date": datetime.now(),
    }
    execute_query(query, params=params)
