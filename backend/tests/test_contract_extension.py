"""Unit tests for vehicle and housing contract extension logic, including conflict detection."""

import os
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

from services import housing_service, vehicle_service


def _empty_df():
    """Return an empty pandas DataFrame, used to simulate no matching rows in mocked queries."""
    return pd.DataFrame()



def test_vehicle_extend_blocked_by_conflicting_contract():
    """Verify that extending a vehicle contract is blocked when a conflicting contract exists."""
    contract_df = pd.DataFrame([{
        "arac_id": "ARC-0001", "baslangic_tarihi": date(2026, 1, 1), "bitis_tarihi": date(2026, 2, 1),
        "sozlesme_durumu": "DEVAM EDIYOR", "total_kira": 3000.0, "odenen_toplam_tutar": 3000.0,
    }])
    vehicle_df = pd.DataFrame([{"gunluk_ucret": 100.0}])
    conflict_df = pd.DataFrame([{"sozlesme_no": "AKS-0099"}])

    with patch("services.vehicle_service.run_query", side_effect=[contract_df, vehicle_df, conflict_df]), \
         patch("services.vehicle_service.execute_query") as mock_exec, \
         patch("services.vehicle_service.log_transaction"):
        result = vehicle_service.extend_contract("AKS-0001", date(2026, 3, 1), "C1", "D2")

    assert result["success"] is False
    assert "AKS-0099" in result["message"]
    mock_exec.assert_not_called()


def test_vehicle_extend_succeeds_without_conflict():
    """Verify that extending a vehicle contract succeeds and updates the database when there is no conflict."""
    contract_df = pd.DataFrame([{
        "arac_id": "ARC-0001", "baslangic_tarihi": date(2026, 1, 1), "bitis_tarihi": date(2026, 2, 1),
        "sozlesme_durumu": "DEVAM EDIYOR", "total_kira": 3100.0, "odenen_toplam_tutar": 3100.0,
    }])
    vehicle_df = pd.DataFrame([{"gunluk_ucret": 100.0}])

    with patch("services.vehicle_service.run_query", side_effect=[contract_df, vehicle_df, _empty_df()]), \
         patch("services.vehicle_service.execute_query", return_value=(True, None)) as mock_exec, \
         patch("services.vehicle_service.log_transaction"):
        result = vehicle_service.extend_contract("AKS-0001", date(2026, 3, 1), "C1", "D2")

    assert result["success"] is True
    assert mock_exec.call_count == 2


def test_vehicle_extend_rejects_earlier_or_equal_end_date():
    """Verify that extending a vehicle contract to an earlier or equal end date is rejected."""
    contract_df = pd.DataFrame([{
        "arac_id": "ARC-0001", "baslangic_tarihi": date(2026, 1, 1), "bitis_tarihi": date(2026, 2, 1),
        "sozlesme_durumu": "DEVAM EDIYOR", "total_kira": 3000.0, "odenen_toplam_tutar": 3000.0,
    }])
    with patch("services.vehicle_service.run_query", side_effect=[contract_df]):
        result = vehicle_service.extend_contract("AKS-0001", date(2026, 2, 1), "C1", "D2")
    assert result["success"] is False



def test_housing_extend_blocked_by_conflicting_contract():
    """Verify that extending a housing contract is blocked when a conflicting contract exists."""
    contract_df = pd.DataFrame([{
        "daire_id": "DAI-0001", "musteri_id": 1, "baslangic_tarihi": date(2026, 1, 1),
        "bitis_tarihi": date(2026, 2, 1), "sozlesme_durumu": "DEVAM EDİYOR",
        "aylik_kira_yrd": 5000.0, "total_kira": 5000.0, "odenen_toplam_tutar": 5000.0,
    }])
    conflict_df = pd.DataFrame([{"sozlesme_no": "KS-0099"}])

    with patch("services.housing_service.run_query", side_effect=[contract_df, conflict_df]), \
         patch("services.housing_service.execute_query") as mock_exec, \
         patch("services.housing_service.log_transaction"):
        result = housing_service.extend_contract("KS-0001", date(2026, 3, 1), "C1", "D1")

    assert result["success"] is False
    assert "KS-0099" in result["message"]
    mock_exec.assert_not_called()


def test_housing_extend_succeeds_and_extends_payment_plan():
    """Verify that extending a housing contract succeeds and extends its associated payment plan."""
    contract_df = pd.DataFrame([{
        "daire_id": "DAI-0001", "musteri_id": 1, "baslangic_tarihi": date(2026, 1, 1),
        "bitis_tarihi": date(2026, 2, 1), "sozlesme_durumu": "DEVAM EDİYOR",
        "aylik_kira_yrd": 5000.0, "total_kira": 5000.0, "odenen_toplam_tutar": 5000.0,
    }])
    existing_plan_df = pd.DataFrame([{
        "id": 1, "sozlesme_no": "KS-0001", "taksit_no": 1, "planlanan_tarih": date(2026, 1, 1),
        "planlanan_tutar": 5000.0, "odenen_tutar": 5000.0, "durum": "ÖDENDİ",
    }])

    with patch("services.housing_service.run_query", side_effect=[contract_df, _empty_df(), existing_plan_df]), \
         patch("services.housing_service.execute_query", return_value=(True, None)) as mock_exec, \
         patch("services.housing_service.log_transaction"):
        result = housing_service.extend_contract("KS-0001", date(2026, 3, 1), "C1", "D1")

    assert result["success"] is True
    assert mock_exec.call_count >= 2


def test_housing_extend_rejects_earlier_or_equal_end_date():
    """Verify that extending a housing contract to an earlier or equal end date is rejected."""
    contract_df = pd.DataFrame([{
        "daire_id": "DAI-0001", "musteri_id": 1, "baslangic_tarihi": date(2026, 1, 1),
        "bitis_tarihi": date(2026, 2, 1), "sozlesme_durumu": "DEVAM EDİYOR",
        "aylik_kira_yrd": 5000.0, "total_kira": 5000.0, "odenen_toplam_tutar": 5000.0,
    }])
    with patch("services.housing_service.run_query", side_effect=[contract_df]):
        result = housing_service.extend_contract("KS-0001", date(2026, 1, 15), "C1", "D1")
    assert result["success"] is False
