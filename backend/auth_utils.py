"""JWT access token creation and decoding for session authentication."""
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt

from config import settings


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Builds a signed JWT embedding the given user data with an expiry time."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)


def decode_access_token(token: str) -> Optional[dict]:
    """Verifies and decodes a JWT, returning its payload or None if invalid/expired."""
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except JWTError:
        return None
