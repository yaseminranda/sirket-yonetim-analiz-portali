"""Backend logic for the daily revenue distribution and occupancy rate chart on the vehicle and housing analytics pages."""
from datetime import date

import numpy as np
import pandas as pd
from fastapi import HTTPException
from sqlalchemy import text

from database import run_query

MAX_DAILY_REVENUE_RANGE_DAYS = 366

_CATEGORY_CONFIG = {
    "ARAC": {"contract_table": "araba_kiralama_sozlesmeleri", "fleet_table": "arabalar"},
    "EV": {"contract_table": "ev_kiralama_sozlesmeleri", "fleet_table": "daireler"},
}


def _daily_fleet_size(fleet_table: str, date_index: pd.DatetimeIndex) -> np.ndarray:
    """Compute, for each day in the range, how many fleet items were actually active (added and not yet deactivated) using a vectorized sweep-line approach."""
    n = len(date_index)
    if n == 0:
        return np.zeros(0, dtype=int)

    df = run_query(text(f"SELECT sisteme_ekleme_tarihi, pasif_tarihi FROM {fleet_table};"))
    if df is None or df.empty:
        return np.zeros(n, dtype=int)

    range_start, range_end = date_index[0], date_index[-1]
    delta = np.zeros(n + 1, dtype=int)

    for _, row in df.iterrows():
        ekleme_raw = row.get("sisteme_ekleme_tarihi")
        pasif_raw = row.get("pasif_tarihi")
        ekleme = pd.to_datetime(ekleme_raw) if pd.notna(ekleme_raw) else range_start
        pasif = pd.to_datetime(pasif_raw) if pd.notna(pasif_raw) else None

        active_start = max(ekleme, range_start)
        active_end = (pasif - pd.Timedelta(days=1)) if pasif is not None else range_end
        active_end = min(active_end, range_end)
        if active_start > active_end:
            continue

        s_idx = (active_start - range_start).days
        e_idx = (active_end - range_start).days
        delta[s_idx] += 1
        delta[e_idx + 1] -= 1

    return np.cumsum(delta[:n])


def get_daily_revenue_and_occupancy(category: str, start_date: date, end_date: date) -> list:
    """For each day in the given date range, return the fixed daily revenue share, occupancy rate (%), occupied count, and total fleet/portfolio size."""
    config = _CATEGORY_CONFIG[category]

    if (end_date - start_date).days > MAX_DAILY_REVENUE_RANGE_DAYS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Tarih aralığı en fazla {MAX_DAILY_REVENUE_RANGE_DAYS} gün olabilir. "
                "Lütfen daha dar bir tarih aralığı seçin."
            ),
        )

    contracts_df = run_query(
        text(
            f"""
            SELECT sozlesme_no, baslangic_tarihi, bitis_tarihi, total_kira
            FROM {config['contract_table']}
            WHERE bitis_tarihi >= :start_date AND baslangic_tarihi <= :end_date;
            """
        ),
        params={"start_date": start_date, "end_date": end_date},
    )

    date_index = pd.date_range(start_date, end_date, freq="D")
    n = len(date_index)
    revenue_arr = np.zeros(n)
    occupied_arr = np.zeros(n, dtype=int)

    if contracts_df is not None and not contracts_df.empty:
        contracts_df = contracts_df.copy()
        contracts_df["baslangic_tarihi"] = pd.to_datetime(contracts_df["baslangic_tarihi"])
        contracts_df["bitis_tarihi"] = pd.to_datetime(contracts_df["bitis_tarihi"])
        range_start, range_end = date_index[0], date_index[-1]

        for _, row in contracts_df.iterrows():
            c_start, c_end = row["baslangic_tarihi"], row["bitis_tarihi"]
            if pd.isna(c_start) or pd.isna(c_end):
                continue
            total = float(row["total_kira"] or 0)
            sure_gun = (c_end - c_start).days + 1
            if sure_gun <= 0 or total <= 0:
                continue
            gunluk_pay = total / sure_gun

            overlap_start = max(c_start, range_start)
            overlap_end = min(c_end, range_end)
            if overlap_start > overlap_end:
                continue

            s_idx = (overlap_start - range_start).days
            e_idx = (overlap_end - range_start).days
            revenue_arr[s_idx : e_idx + 1] += gunluk_pay
            occupied_arr[s_idx : e_idx + 1] += 1

    fleet_sizes = _daily_fleet_size(config["fleet_table"], date_index)
    occupancy_rate = np.divide(
        occupied_arr, fleet_sizes, out=np.zeros(n), where=fleet_sizes > 0
    ) * 100

    today = pd.Timestamp(date.today())
    rows = []
    for i, d in enumerate(date_index):
        rows.append(
            {
                "tarih": d.strftime("%Y-%m-%d"),
                "gunluk_gelir": round(float(revenue_arr[i]), 2),
                "aktiflik_orani": round(float(occupancy_rate[i]), 2),
                "dolu_sayisi": int(occupied_arr[i]),
                "toplam_filo": int(fleet_sizes[i]),
                "gelecek_mi": bool(d > today),
            }
        )
    return rows
