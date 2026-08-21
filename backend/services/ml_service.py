"""Machine-learning service: trains and applies models for revenue/demand forecasting, cancellation risk, and capacity planning."""
from datetime import datetime

import pandas as pd
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor

from database import run_query


def get_historical_data() -> pd.DataFrame:
    """Load and combine vehicle and housing contract history into a single DataFrame with derived fields for modeling."""
    sql_vehicle = """
        SELECT
            ak.sozlesme_no AS contract_no, ak.baslangic_tarihi AS start_date, ak.bitis_tarihi AS end_date,
            ak.sure_gun_yrd AS duration, ak.total_kira AS price, ak.sozlesme_durumu AS status,
            'ARAC' AS category, amar.marka_adi || ' ' || am.model_adi AS product_type, a.arac_id AS product_id
        FROM araba_kiralama_sozlesmeleri ak
        LEFT JOIN arabalar a ON ak.arac_id = a.arac_id
        LEFT JOIN araba_modelleri am ON a.model_id = am.model_id
        LEFT JOIN araba_markalari amar ON am.marka_id = amar.marka_id;
    """
    sql_housing = """
        SELECT
            ek.sozlesme_no AS contract_no, ek.baslangic_tarihi AS start_date, ek.bitis_tarihi AS end_date,
            (ek.bitis_tarihi - ek.baslangic_tarihi) AS duration, ek.total_kira AS price, ek.sozlesme_durumu AS status,
            'EV' AS category, d.oda_sayisi || ' Daire' AS product_type, d.daire_id AS product_id
        FROM ev_kiralama_sozlesmeleri ek
        LEFT JOIN daireler d ON ek.daire_id = d.daire_id;
    """
    df_vehicle = run_query(sql_vehicle)
    df_housing = run_query(sql_housing)
    df_combined = pd.concat([df_vehicle, df_housing], ignore_index=True)

    if not df_combined.empty:
        df_combined["start_date"] = pd.to_datetime(df_combined["start_date"])
        df_combined["end_date"] = pd.to_datetime(df_combined["end_date"])
        df_combined["price"] = pd.to_numeric(df_combined["price"], errors="coerce").fillna(0)
        df_combined["duration"] = pd.to_numeric(df_combined["duration"], errors="coerce").fillna(1)
        df_combined["year_month"] = df_combined["start_date"].dt.to_period("M")
        status_str = df_combined["status"].astype(str).str.upper()
        df_combined["is_cancelled"] = status_str.str.contains("İPTAL|IPTAL|SİLİNDİ|SILINDI", regex=True).astype(int)

    return df_combined


def train_revenue_demand_model():
    """Train random forest models to predict monthly revenue and demand per category from historical contracts."""
    df = get_historical_data()
    if df.empty:
        return None, None

    df_valid = df[df["is_cancelled"] == 0].copy()
    monthly = (
        df_valid.groupby(["year_month", "category"])
        .agg(total_revenue=("price", "sum"), total_demand=("contract_no", "count"))
        .reset_index()
    )

    if monthly.empty or len(monthly) < 3:
        return None, "Yetersiz geçmiş veri (En az 3 aylık veri gerekli)."

    monthly["year"] = monthly["year_month"].dt.year
    monthly["month"] = monthly["year_month"].dt.month
    monthly["category_code"] = monthly["category"].astype("category").cat.codes

    X = monthly[["year", "month", "category_code"]]
    y_revenue = monthly["total_revenue"]
    y_demand = monthly["total_demand"]

    model_revenue = RandomForestRegressor(n_estimators=100, random_state=42)
    model_demand = RandomForestRegressor(n_estimators=100, random_state=42)
    model_revenue.fit(X, y_revenue)
    model_demand.fit(X, y_demand)

    return (model_revenue, model_demand), monthly


def predict_future(model_tuple, future_months: int = 6) -> pd.DataFrame:
    """Use trained revenue/demand models to forecast future months for each category."""
    if not model_tuple or isinstance(model_tuple, str):
        return pd.DataFrame()

    model_revenue, model_demand = model_tuple
    today = datetime.today()
    future_rows = []

    for i in range(1, future_months + 1):
        target_date = pd.date_range(start=today, periods=i + 1, freq="MS")[-1]
        year, month = target_date.year, target_date.month

        for cat_code, cat_name in [(0, "ARAC"), (1, "EV")]:
            pred_rev = model_revenue.predict([[year, month, cat_code]])[0]
            pred_demand = model_demand.predict([[year, month, cat_code]])[0]
            future_rows.append(
                {
                    "Tarih": target_date.strftime("%Y-%m"), "Kategori": cat_name,
                    "Tahmini Ciro (₺)": max(0, round(pred_rev, 2)),
                    "Tahmini Kiralama Adedi": max(1, int(round(pred_demand))),
                }
            )
    return pd.DataFrame(future_rows)


def train_cancellation_model():
    """Train a random forest classifier to predict contract cancellation likelihood."""
    df = get_historical_data()
    if df.empty or len(df) < 5:
        return None

    df["category_code"] = df["category"].astype("category").cat.codes
    X = df[["duration", "price", "category_code"]]
    y = df["is_cancelled"]

    model_cancel = RandomForestClassifier(n_estimators=100, random_state=42)
    model_cancel.fit(X, y)
    return model_cancel


def predict_cancellation_risk(model, duration: float, price: float, category: str) -> float:
    """Predict the cancellation risk percentage for a contract with the given attributes."""
    if model is None:
        return 5.0
    cat_code = 0 if category == "ARAC" else 1
    prob = model.predict_proba([[duration, price, cat_code]])[0][1]
    return round(prob * 100, 1)


def analyze_capacity_and_investment() -> dict:
    """Compute current fleet/portfolio occupancy rates and generate investment recommendations based on demand."""
    total_vehicle_df = run_query("SELECT COUNT(*) as sayi FROM arabalar;")
    total_house_df = run_query("SELECT COUNT(*) as sayi FROM daireler;")

    total_vehicles = int(total_vehicle_df.iloc[0]["sayi"]) if not total_vehicle_df.empty else 1
    total_houses = int(total_house_df.iloc[0]["sayi"]) if not total_house_df.empty else 1

    today_str = datetime.today().strftime("%Y-%m-%d")
    sql_active_vehicles = (
        f"SELECT COUNT(DISTINCT arac_id) as sayi FROM araba_kiralama_sozlesmeleri "
        f"WHERE baslangic_tarihi <= '{today_str}' AND bitis_tarihi >= '{today_str}' "
        f"AND LOWER(sozlesme_durumu) NOT LIKE '%iptal%';"
    )
    sql_active_houses = (
        f"SELECT COUNT(DISTINCT daire_id) as sayi FROM ev_kiralama_sozlesmeleri "
        f"WHERE baslangic_tarihi <= '{today_str}' AND bitis_tarihi >= '{today_str}' "
        f"AND LOWER(sozlesme_durumu) NOT LIKE '%iptal%';"
    )

    res_v = run_query(sql_active_vehicles)
    res_h = run_query(sql_active_houses)
    active_vehicles = int(res_v.iloc[0]["sayi"]) if not res_v.empty else 0
    active_houses = int(res_h.iloc[0]["sayi"]) if not res_h.empty else 0

    vehicle_occupancy = min(100.0, round((active_vehicles / total_vehicles) * 100, 1))
    house_occupancy = min(100.0, round((active_houses / total_houses) * 100, 1))

    df_hist = get_historical_data()
    df_valid = df_hist[df_hist["is_cancelled"] == 0] if not df_hist.empty else df_hist

    top_vehicle = (
        df_valid[df_valid["category"] == "ARAC"]["product_type"].mode().iloc[0]
        if not df_valid.empty and not df_valid[df_valid["category"] == "ARAC"].empty else "SUV Araç"
    )
    top_house = (
        df_valid[df_valid["category"] == "EV"]["product_type"].mode().iloc[0]
        if not df_valid.empty and not df_valid[df_valid["category"] == "EV"].empty else "2+1 Daire"
    )

    recommendations = []
    if vehicle_occupancy >= 70.0:
        recommendations.append({
            "Kategori": "🚗 Araç Filosu", "Durum": "🔴 Kapasite Yetersiz / Yüksek Doluluk",
            "Doluluk Oranı": f"%{vehicle_occupancy}",
            "Tavsiye": f"Gelecek dönem yüksek talep beklenmektedir. Filoya 2 Adet **{top_vehicle}** eklenmesi önerilir.",
            "Aciliyet": "Yüksek",
        })
    else:
        recommendations.append({
            "Kategori": "🚗 Araç Filosu", "Durum": "🟢 Kapasite Yeterli", "Doluluk Oranı": f"%{vehicle_occupancy}",
            "Tavsiye": "Mevcut araç filosu önümüzdeki dönem talebini karşılamak için yeterlidir.", "Aciliyet": "Düşük",
        })

    if house_occupancy >= 70.0:
        recommendations.append({
            "Kategori": "🏠 Konut Portföyü", "Durum": "🔴 Kapasite Yetersiz / Yüksek Doluluk",
            "Doluluk Oranı": f"%{house_occupancy}",
            "Tavsiye": f"Konut doluluğu kritik seviyededir. Portföye 1 Adet **{top_house}** eklenmesi önerilir.",
            "Aciliyet": "Yüksek",
        })
    else:
        recommendations.append({
            "Kategori": "🏠 Konut Portföyü", "Durum": "🟢 Kapasite Yeterli", "Doluluk Oranı": f"%{house_occupancy}",
            "Tavsiye": "Mevcut daire portföyü önümüzdeki dönem talebini karşılamak için yeterlidir.", "Aciliyet": "Düşük",
        })

    return {
        "arac_doluluk": vehicle_occupancy, "ev_doluluk": house_occupancy,
        "toplam_arac": total_vehicles, "toplam_ev": total_houses,
        "aktif_arac": active_vehicles, "aktif_ev": active_houses,
        "oneriler": recommendations,
    }
