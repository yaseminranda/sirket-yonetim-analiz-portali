"""Unit tests for the ML service's cancellation risk and future prediction helpers."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

from services.ml_service import predict_cancellation_risk, predict_future


def test_predict_cancellation_risk_with_no_model():
    """Verify that a default risk score of 5.0 is returned when no model is available."""
    risk = predict_cancellation_risk(None, duration=10, price=1000, category="ARAC")
    assert risk == 5.0


def test_predict_future_with_none_model_returns_empty():
    """Verify that predict_future returns an empty DataFrame when given no model or an insufficient-data message."""
    df = predict_future(None, future_months=3)
    assert df.empty

    df2 = predict_future("Yetersiz veri mesajı", future_months=3)
    assert df2.empty
