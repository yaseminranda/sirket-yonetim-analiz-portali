"""
Eski sifreleme.py'nin backend/scripts altına taşınmış hali.

Kullanım (backend klasöründeyken):
    python -m scripts.seed_default_passwords

Sabit kodlanmış veritabanı bağlantısı kaldırıldı; artık backend'in kendi
.env / config.py'sindeki DATABASE_URL kullanılıyor.
"""
import sys
from pathlib import Path

# backend/ klasörünü sys.path'e ekle ki config/database import edilebilsin
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bcrypt
from sqlalchemy import text

from database import engine

DEFAULT_PASSWORD = "Sirket123!"
DEFAULT_QUESTION = "En sevdiğiniz renk nedir?"
DEFAULT_ANSWER = "Mavi"


def hash_data(data: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(data.encode("utf-8"), salt).decode("utf-8")


def load_default_passwords():
    """Şifre hash'i olmayan tüm çalışanlara varsayılan şifre ve güvenlik sorusu atar."""
    password_h = hash_data(DEFAULT_PASSWORD)
    answer_h = hash_data(DEFAULT_ANSWER.lower().strip())

    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                UPDATE calisan
                SET sifre_hash = :password, guvenlik_sorusu = :question, guvenlik_cevabi_hash = :answer
                WHERE sifre_hash IS NULL;
                """
            ),
            {"password": password_h, "question": DEFAULT_QUESTION, "answer": answer_h},
        )
        print(f"✅ {result.rowcount} çalışana varsayılan şifre ({DEFAULT_PASSWORD}) ve güvenlik sorusu tanımlandı.")


if __name__ == "__main__":
    load_default_passwords()
