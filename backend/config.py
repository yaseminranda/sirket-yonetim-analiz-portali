"""Application settings loaded from environment variables (.env)."""
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Holds database, JWT, security, 2FA, and SMS configuration read from the environment."""
    database_url: str

    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 120
    remember_me_expire_days: int = 30

    max_failed_attempts: int = 5
    lock_duration_minutes: int = 15

    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    login_verification_code_expire_minutes: int = 5

    sms_api_url: Optional[str] = None
    sms_api_key: Optional[str] = None
    sms_sender_id: Optional[str] = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
