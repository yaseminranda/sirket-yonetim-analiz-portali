"""Frontend-side validation helpers for customer/driver data entry forms."""
import re
from typing import Optional

_PHONE_REGEX = re.compile(r"^0\d{10}$")
_EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PASSPORT_REGEX = re.compile(r"^[A-Za-z0-9]{5,20}$")


def clean_phone(value: str) -> Optional[str]:
    """Strip whitespace, dashes and parentheses and return the phone number if it is valid, else None."""
    cleaned = re.sub(r"[\s\-()]", "", value or "")
    if not _PHONE_REGEX.match(cleaned):
        return None
    return cleaned


def is_valid_phone(value: str) -> bool:
    """Return True if the given value is a valid phone number."""
    return clean_phone(value) is not None


def is_valid_email(value: str) -> bool:
    """Return True if the given value is a valid email address."""
    return bool(_EMAIL_REGEX.match((value or "").strip()))


def is_valid_identity_no(value: str) -> bool:
    """Return True if the value is a valid Turkish national ID number or a passport number."""
    cleaned = (value or "").strip().replace(" ", "")
    if len(cleaned) == 11 and cleaned.isdigit():
        return cleaned[0] != "0"
    return bool(_PASSPORT_REGEX.match(cleaned))
