"""Backend for the vehicle-vs-housing business comparison page."""
import pandas as pd
from sqlalchemy import text

from database import run_query


def get_comparison_data() -> dict:
    """Aggregate revenue, contract counts, and department salary costs to compare the vehicle and housing businesses."""
    sql_vehicle = """
        SELECT COALESCE(SUM(total_kira), 0) AS revenue, COUNT(sozlesme_no) AS total_contracts,
               COUNT(CASE WHEN LOWER(CAST(sozlesme_durumu AS VARCHAR)) IN ('aktif', 'devam ediyor') THEN 1 END) AS active_contracts
        FROM araba_kiralama_sozlesmeleri;
    """
    sql_housing = """
        SELECT COALESCE(SUM(total_kira), 0) AS revenue, COUNT(sozlesme_no) AS total_contracts,
               COUNT(CASE WHEN LOWER(CAST(sozlesme_durumu AS VARCHAR)) IN ('aktif', 'devam ediyor') THEN 1 END) AS active_contracts
        FROM ev_kiralama_sozlesmeleri;
    """
    sql_salary = """
        SELECT UPPER(CAST(departman_id AS VARCHAR)) AS dep, COALESCE(SUM(aylik_maas), 0) AS total_salary,
               COUNT(calisan_id) AS employee_count
        FROM calisan GROUP BY UPPER(CAST(departman_id AS VARCHAR));
    """

    res_vehicle = run_query(sql_vehicle)
    res_housing = run_query(sql_housing)
    res_salary = run_query(sql_salary)

    vehicle_revenue = float(res_vehicle.iloc[0]["revenue"]) if not res_vehicle.empty else 0.0
    vehicle_contracts = int(res_vehicle.iloc[0]["total_contracts"]) if not res_vehicle.empty else 0
    vehicle_active = int(res_vehicle.iloc[0]["active_contracts"]) if not res_vehicle.empty else 0

    housing_revenue = float(res_housing.iloc[0]["revenue"]) if not res_housing.empty else 0.0
    housing_contracts = int(res_housing.iloc[0]["total_contracts"]) if not res_housing.empty else 0
    housing_active = int(res_housing.iloc[0]["active_contracts"]) if not res_housing.empty else 0

    vehicle_salary, vehicle_employee_count = 0.0, 0
    housing_salary, housing_employee_count = 0.0, 0

    if not res_salary.empty:
        for _, row in res_salary.iterrows():
            dep_code = str(row["dep"])
            if any(k in dep_code for k in ["D2", "2", "ARAC", "ARAÇ"]):
                vehicle_salary += float(row["total_salary"])
                vehicle_employee_count += int(row["employee_count"])
            elif any(k in dep_code for k in ["D1", "1", "EV", "EMLAK"]):
                housing_salary += float(row["total_salary"])
                housing_employee_count += int(row["employee_count"])

    return {
        "vehicle": {
            "revenue": vehicle_revenue, "salary": vehicle_salary, "net_profit": vehicle_revenue - vehicle_salary,
            "total_contracts": vehicle_contracts, "active_contracts": vehicle_active, "employee_count": vehicle_employee_count,
        },
        "housing": {
            "revenue": housing_revenue, "salary": housing_salary, "net_profit": housing_revenue - housing_salary,
            "total_contracts": housing_contracts, "active_contracts": housing_active, "employee_count": housing_employee_count,
        },
    }


def get_customer_analysis_data():
    """Return top customers by contract count/revenue for vehicles, housing, and customers active in both."""
    sql_vehicle_cust = """
        SELECT m.isim AS customer, COUNT(s.sozlesme_no) AS total_contracts,
            COUNT(CASE WHEN LOWER(CAST(s.sozlesme_durumu AS VARCHAR)) IN ('aktif', 'devam ediyor') THEN 1 ELSE NULL END) AS active_contracts,
            COUNT(CASE WHEN LOWER(CAST(s.sozlesme_durumu AS VARCHAR)) NOT IN ('aktif', 'devam ediyor') THEN 1 ELSE NULL END) AS passive_contracts,
            COALESCE(SUM(s.total_kira), 0) AS total_revenue
        FROM araba_kiralama_sozlesmeleri s
        INNER JOIN musteriler m ON s.musteri_id = m.musteri_id
        GROUP BY m.isim ORDER BY total_contracts DESC, total_revenue DESC LIMIT 5;
    """
    sql_housing_cust = """
        SELECT m.isim AS customer, COUNT(s.sozlesme_no) AS total_contracts,
            COUNT(CASE WHEN LOWER(CAST(s.sozlesme_durumu AS VARCHAR)) IN ('aktif', 'devam ediyor') THEN 1 ELSE NULL END) AS active_contracts,
            COUNT(CASE WHEN LOWER(CAST(s.sozlesme_durumu AS VARCHAR)) NOT IN ('aktif', 'devam ediyor') THEN 1 ELSE NULL END) AS passive_contracts,
            COALESCE(SUM(s.total_kira), 0) AS total_revenue
        FROM ev_kiralama_sozlesmeleri s
        INNER JOIN musteriler m ON s.musteri_id = m.musteri_id
        GROUP BY m.isim ORDER BY total_contracts DESC, total_revenue DESC LIMIT 5;
    """
    sql_cross_cust = """
        WITH vehicle_m AS (
            SELECT musteri_id, COUNT(sozlesme_no) AS vehicle_contracts, COALESCE(SUM(total_kira), 0) AS vehicle_revenue
            FROM araba_kiralama_sozlesmeleri GROUP BY musteri_id
        ),
        housing_m AS (
            SELECT musteri_id, COUNT(sozlesme_no) AS housing_contracts, COALESCE(SUM(total_kira), 0) AS housing_revenue
            FROM ev_kiralama_sozlesmeleri GROUP BY musteri_id
        )
        SELECT m.isim AS customer, v.vehicle_contracts, h.housing_contracts,
            (v.vehicle_contracts + h.housing_contracts) AS total_contracts,
            v.vehicle_revenue, h.housing_revenue, (v.vehicle_revenue + h.housing_revenue) AS consolidated_revenue
        FROM vehicle_m v
        INNER JOIN housing_m h ON v.musteri_id = h.musteri_id
        INNER JOIN musteriler m ON v.musteri_id = m.musteri_id
        ORDER BY total_contracts DESC, consolidated_revenue DESC LIMIT 5;
    """
    return run_query(sql_vehicle_cust), run_query(sql_housing_cust), run_query(sql_cross_cust)


def get_monthly_trend_data() -> dict:
    """Build a monthly time series of revenue and net profit (revenue minus current department salary cost) for vehicles and housing."""
    sql_vehicle_monthly = """
        SELECT DATE_TRUNC('month', baslangic_tarihi) AS ay, COALESCE(SUM(total_kira), 0) AS ciro
        FROM araba_kiralama_sozlesmeleri
        GROUP BY DATE_TRUNC('month', baslangic_tarihi)
        ORDER BY ay ASC;
    """
    sql_housing_monthly = """
        SELECT DATE_TRUNC('month', baslangic_tarihi) AS ay, COALESCE(SUM(total_kira), 0) AS ciro
        FROM ev_kiralama_sozlesmeleri
        GROUP BY DATE_TRUNC('month', baslangic_tarihi)
        ORDER BY ay ASC;
    """
    sql_salary = """
        SELECT UPPER(CAST(departman_id AS VARCHAR)) AS dep, COALESCE(SUM(aylik_maas), 0) AS total_salary
        FROM calisan GROUP BY UPPER(CAST(departman_id AS VARCHAR));
    """
    res_v = run_query(sql_vehicle_monthly)
    res_h = run_query(sql_housing_monthly)
    res_salary = run_query(sql_salary)

    vehicle_salary, housing_salary = 0.0, 0.0
    if res_salary is not None and not res_salary.empty:
        for _, row in res_salary.iterrows():
            dep_code = str(row["dep"])
            if any(k in dep_code for k in ["D2", "2", "ARAC", "ARAÇ"]):
                vehicle_salary += float(row["total_salary"])
            elif any(k in dep_code for k in ["D1", "1", "EV", "EMLAK"]):
                housing_salary += float(row["total_salary"])

    def _build_series(res: pd.DataFrame, salary: float) -> list:
        """Convert a monthly revenue DataFrame into a list of records with computed net profit."""
        if res is None or res.empty:
            return []
        rows = []
        for _, row in res.iterrows():
            if pd.isna(row["ay"]):
                continue
            ciro = float(row["ciro"])
            rows.append({"ay": row["ay"].strftime("%Y-%m"), "ciro": ciro, "net_kar": ciro - salary})
        return rows

    return {"vehicle": _build_series(res_v, vehicle_salary), "housing": _build_series(res_h, housing_salary)}


def get_gm_security_logs() -> pd.DataFrame:
    """Return login/security log entries for admin, general manager, and D3-department accounts."""
    query = text(
        """
        SELECT
            l.tarih AS "Tarih / Saat", l.calisan_id AS "Çalışan ID", c.ad_soyad AS "Yönetici Ad Soyad",
            c.yetki AS "Yetki",
            CASE
                WHEN l.basarili_mi = TRUE THEN '✅ Başarılı Giriş'
                WHEN l.hata_nedeni LIKE '%kilitlendi%' THEN '🚨 Hesap Kilitlendi'
                ELSE '❌ Hatalı Giriş'
            END AS "Durum",
            l.hata_nedeni AS "Açıklama / Hata Detayı"
        FROM giris_loglari l
        INNER JOIN calisan c ON l.calisan_id = c.calisan_id
        WHERE UPPER(c.yetki) IN ('ADMIN', 'GENEL MÜDÜR', 'GENEL MUDUR', 'YÖNETİCİ', 'YONETICI')
           OR UPPER(CAST(l.departman_id AS VARCHAR)) = 'D3'
        ORDER BY l.tarih DESC;
        """
    )
    return run_query(query)
