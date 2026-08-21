"""Unit tests for finance service helpers: category-to-table mapping and invoice Excel generation."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

from services.finance_service import generate_invoice_excel, _table_for_category


def test_table_for_category_ev():
    """Verify that the EV category maps to the housing rental contracts table."""
    assert _table_for_category("EV") == "ev_kiralama_sozlesmeleri"


def test_table_for_category_arac():
    """Verify that the ARAC category maps to the vehicle rental contracts table."""
    assert _table_for_category("ARAC") == "araba_kiralama_sozlesmeleri"


def test_generate_invoice_excel_returns_valid_buffer():
    """Verify that generating an invoice returns a non-empty, valid xlsx byte buffer."""
    buffer = generate_invoice_excel(
        contract_no="AKS-0001", customer_name="Test Müşteri", amount=1000.0,
        transaction_type="Test Ödeme", remaining_balance=500.0,
    )
    content = buffer.read()
    assert len(content) > 0
    assert content[:2] == b"PK"
