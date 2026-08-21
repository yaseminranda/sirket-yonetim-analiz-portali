"""
Streamlit tarafının ayarları. Sadece backend API adresini tutar.
Streamlit artık veritabanı bilgisi TAŞIMAZ.
"""
import os

# Docker Compose içinde servis adı üzerinden erişilir (örn. "http://backend:8000").
# Local (Docker'sız) çalıştırmada varsayılan olarak localhost kullanılır.
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
