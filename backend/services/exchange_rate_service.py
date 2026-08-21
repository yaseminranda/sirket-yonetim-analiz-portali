"""Fetches TCMB (Turkish Central Bank) exchange rates and caches them in the `doviz_kurlari` table, using the Forex Selling rate and falling back to the nearest previous business day when unavailable."""
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from typing import Optional

import httpx
from sqlalchemy import text

from database import execute_query, run_query

TCMB_TODAY_URL = "https://www.tcmb.gov.tr/kurlar/today.xml"
TCMB_HISTORY_URL = "https://www.tcmb.gov.tr/kurlar/{yyyymm}/{ddmmyyyy}.xml"

SUPPORTED_CURRENCIES = {"USD", "EUR", "GBP"}
MAX_GERI_ARAMA_GUNU = 7
VARSAYILAN_KUR = {"alis": 1.0, "satis": 1.0, "efektif_satis": 1.0}


def _fetch_from_tcmb(target_date: date) -> Optional[dict]:
    """Fetch the TCMB rates XML for the given date; returns {currency_code: {alis, satis, efektif_satis}} or None if unavailable."""
    if target_date == date.today():
        url = TCMB_TODAY_URL
    else:
        url = TCMB_HISTORY_URL.format(
            yyyymm=target_date.strftime("%Y%m"), ddmmyyyy=target_date.strftime("%d%m%Y")
        )
    try:
        resp = httpx.get(url, timeout=10, follow_redirects=True)
        if resp.status_code != 200 or not resp.content:
            return None
        root = ET.fromstring(resp.content)
    except (httpx.HTTPError, ET.ParseError):
        return None

    def _to_float(text_val: Optional[str]) -> Optional[float]:
        """Parse a locale-formatted numeric string (comma decimal) into a float."""
        if not text_val:
            return None
        try:
            return float(text_val.replace(",", "."))
        except ValueError:
            return None

    result = {}
    for currency_el in root.findall("Currency"):
        code = currency_el.get("CurrencyCode")
        if code not in SUPPORTED_CURRENCIES:
            continue
        forex_selling = currency_el.find("ForexSelling")
        forex_buying = currency_el.find("ForexBuying")
        banknote_selling = currency_el.find("BanknoteSelling")
        result[code] = {
            "alis": _to_float(forex_buying.text if forex_buying is not None else None),
            "satis": _to_float(forex_selling.text if forex_selling is not None else None),
            "efektif_satis": _to_float(banknote_selling.text if banknote_selling is not None else None),
        }
    return result or None


def _get_cached_rate(target_date: date, currency: str) -> Optional[dict]:
    """Look up a previously cached exchange rate for the given date and currency."""
    df = run_query(
        text("SELECT alis, satis, efektif_satis FROM doviz_kurlari WHERE tarih = :d AND doviz_cinsi = :c;"),
        params={"d": str(target_date), "c": currency},
    )
    if df is None or df.empty:
        return None
    row = df.iloc[0]
    if row["satis"] is None:
        return None
    return {"alis": row["alis"], "satis": row["satis"], "efektif_satis": row["efektif_satis"]}


def _cache_rates(target_date: date, rates: dict) -> None:
    """Insert fetched rates into the cache table, skipping any currency without a selling rate."""
    for code, values in rates.items():
        if values.get("satis") is None:
            continue
        execute_query(
            text(
                """
                INSERT INTO doviz_kurlari (tarih, doviz_cinsi, alis, satis, efektif_satis)
                VALUES (:d, :c, :alis, :satis, :efektif_satis)
                ON CONFLICT (tarih, doviz_cinsi) DO NOTHING;
                """
            ),
            params={"d": str(target_date), "c": code, **values},
        )


def get_exchange_rate(target_date: date, currency: str) -> dict:
    """Return the exchange rate for a date/currency: check the cache, then fetch from TCMB (searching back up to 7 days if needed), falling back to a safe default of 1.0."""
    currency = currency.upper()
    if currency == "TRY":
        return dict(VARSAYILAN_KUR)

    cached = _get_cached_rate(target_date, currency)
    if cached:
        return cached

    search_date = target_date
    for _ in range(MAX_GERI_ARAMA_GUNU):
        rates = _fetch_from_tcmb(search_date)
        if rates:
            _cache_rates(search_date, rates)
            if currency in rates and rates[currency].get("satis") is not None:
                if search_date != target_date:
                    _cache_rates(target_date, rates)
                return rates[currency]
        search_date = search_date - timedelta(days=1)

    return dict(VARSAYILAN_KUR)
