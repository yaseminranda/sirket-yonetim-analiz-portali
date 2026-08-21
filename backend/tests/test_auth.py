"""Unit tests for password hashing and JWT access token helpers."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

from services.auth_service import check_hash, hash_text
from auth_utils import create_access_token, decode_access_token


def test_hash_and_check_roundtrip():
    """Verify that hashing a password and checking it against the hash succeeds."""
    plain = "Sirket123!"
    hashed = hash_text(plain)
    assert hashed != plain
    assert check_hash(plain, hashed) is True


def test_check_hash_wrong_password():
    """Verify that checking a hash against a different plaintext password fails."""
    hashed = hash_text("DogruSifre1!")
    assert check_hash("YanlisSifre1!", hashed) is False


def test_hash_text_empty_string():
    """Verify that hashing an empty string returns an empty string."""
    assert hash_text("") == ""


def test_jwt_roundtrip():
    """Verify that a JWT access token can be created and decoded back to its original claims."""
    token = create_access_token({"sub": "C1", "role": "GENEL MÜDÜR", "department_id": "D3"})
    payload = decode_access_token(token)
    assert payload is not None
    assert payload["sub"] == "C1"
    assert payload["role"] == "GENEL MÜDÜR"


def test_jwt_invalid_token_returns_none():
    """Verify that decoding a malformed token returns None instead of raising an error."""
    assert decode_access_token("gecersiz.token.degeri") is None
