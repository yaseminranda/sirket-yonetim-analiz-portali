"""Unit tests for installment plan calculation logic in the housing service."""

import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

from services.housing_service import _calculate_installments


def test_sure_bazli_installments_sum_to_total():
    """Verify that duration-based installments sum to the total contract price."""
    rows = _calculate_installments(
        total_price=15500.0, start_date=date(2026, 1, 1), monthly_rent=5000.0,
        plan_type="SURE_BAZLI", installment_amount=None,
    )
    assert len(rows) == 4
    assert [t for _, t in rows] == [5000.0, 5000.0, 5000.0, 500.0]
    assert round(sum(t for _, t in rows), 2) == 15500.0


def test_tutar_bazli_installments_sum_to_total():
    """Verify that amount-based installments sum to the total price, with the last installment absorbing the remainder."""
    rows = _calculate_installments(
        total_price=15500.0, start_date=date(2026, 1, 1), monthly_rent=5000.0,
        plan_type="TUTAR_BAZLI", installment_amount=2000.0,
    )
    assert len(rows) == 8
    assert round(sum(t for _, t in rows), 2) == 15500.0
    assert rows[-1][1] == 1500.0


def test_installments_are_monthly_spaced():
    """Verify that generated installment dates are spaced one month apart."""
    rows = _calculate_installments(
        total_price=10000.0, start_date=date(2026, 3, 15), monthly_rent=5000.0,
        plan_type="SURE_BAZLI", installment_amount=None,
    )
    dates = [d for d, _ in rows]
    assert dates == [date(2026, 3, 15), date(2026, 4, 15)]


def test_zero_total_price_returns_no_installments():
    """Verify that a zero total price produces no installments."""
    assert _calculate_installments(0.0, date(2026, 1, 1), 5000.0, "SURE_BAZLI", None) == []


def test_tutar_bazli_missing_amount_falls_back_to_sure_bazli():
    """Verify that amount-based plans without an installment amount fall back to duration-based calculation."""
    rows = _calculate_installments(
        total_price=5000.0, start_date=date(2026, 1, 1), monthly_rent=5000.0,
        plan_type="TUTAR_BAZLI", installment_amount=None,
    )
    assert len(rows) == 1
    assert rows[0][1] == 5000.0


def test_single_month_contract_creates_one_installment():
    """Verify that a one-month contract produces exactly one installment."""
    rows = _calculate_installments(5000.0, date(2026, 1, 1), 5000.0, "SURE_BAZLI", None)
    assert len(rows) == 1
    assert rows[0] == (date(2026, 1, 1), 5000.0)
