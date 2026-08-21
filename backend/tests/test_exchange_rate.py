"""Unit tests for the exchange rate service's TCMB XML fetching and parsing logic."""

import os
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

from services import exchange_rate_service as ers

SAMPLE_TCMB_XML = """<?xml version="1.0" encoding="ISO-8859-9"?>
<Tarih_Date Tarih="14.08.2026" Date="08/14/2026" Bulten_No="2026/155">
<Currency CrossOrder="0" Kod="USD" CurrencyCode="USD">
<Unit>1</Unit>
<Isim>ABD DOLARI</Isim>
<CurrencyName>US DOLLAR</CurrencyName>
<ForexBuying>34.1234</ForexBuying>
<ForexSelling>34.2345</ForexSelling>
<BanknoteBuying>34.1000</BanknoteBuying>
<BanknoteSelling>34.2600</BanknoteSelling>
</Currency>
<Currency CrossOrder="1" Kod="EUR" CurrencyCode="EUR">
<Unit>1</Unit>
<Isim>EURO</Isim>
<CurrencyName>EURO</CurrencyName>
<ForexBuying>37.5000</ForexBuying>
<ForexSelling>37.6500</ForexSelling>
<BanknoteBuying>37.4000</BanknoteBuying>
<BanknoteSelling>37.7000</BanknoteSelling>
</Currency>
<Currency CrossOrder="2" Kod="JPY" CurrencyCode="JPY">
<Unit>100</Unit>
<Isim>JAPON YENI</Isim>
<CurrencyName>JAPANESE YEN</CurrencyName>
<ForexBuying>0.2200</ForexBuying>
<ForexSelling>0.2300</ForexSelling>
<BanknoteBuying>0.2100</BanknoteBuying>
<BanknoteSelling>0.2350</BanknoteSelling>
</Currency>
</Tarih_Date>
""".encode("ascii")


def _mock_response(content: bytes, status_code: int = 200):
    """Build a mock httpx response object with the given content and status code."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = content
    return resp


def test_fetch_from_tcmb_parses_supported_currencies():
    """Verify that TCMB XML is parsed correctly for supported currencies and unsupported ones are excluded."""
    with patch("services.exchange_rate_service.httpx.get", return_value=_mock_response(SAMPLE_TCMB_XML)):
        result = ers._fetch_from_tcmb(date(2026, 8, 14))
    assert result is not None
    assert result["USD"]["satis"] == 34.2345
    assert result["USD"]["alis"] == 34.1234
    assert result["EUR"]["satis"] == 37.65
    assert "JPY" not in result


def test_fetch_from_tcmb_handles_http_error():
    """Verify that an HTTP error response from TCMB results in None being returned."""
    with patch("services.exchange_rate_service.httpx.get", return_value=_mock_response(b"", status_code=500)):
        result = ers._fetch_from_tcmb(date(2026, 8, 14))
    assert result is None


def test_fetch_from_tcmb_handles_malformed_xml():
    """Verify that malformed XML content results in None being returned instead of raising."""
    with patch("services.exchange_rate_service.httpx.get", return_value=_mock_response(b"not-xml-at-all")):
        result = ers._fetch_from_tcmb(date(2026, 8, 14))
    assert result is None


def test_get_exchange_rate_try_returns_one():
    """Verify that the exchange rate for TRY returns the default rate constant."""
    assert ers.get_exchange_rate(date(2026, 8, 14), "try") == ers.VARSAYILAN_KUR
