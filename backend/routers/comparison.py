"""API endpoints for cross-department comparison, trend, top-customer, and security-log data (General Manager D3 only)."""
from fastapi import APIRouter, Depends

from dependencies import require_gm_with_dept3
from services import comparison_service

router = APIRouter(prefix="/comparison", tags=["Karşılaştırma"])


@router.get("/summary")
def get_summary(user: dict = Depends(require_gm_with_dept3)):
    """Returns the overall comparison summary data."""
    return comparison_service.get_comparison_data()


@router.get("/monthly-trend")
def get_monthly_trend(user: dict = Depends(require_gm_with_dept3)):
    """Returns monthly trend comparison data."""
    return comparison_service.get_monthly_trend_data()


@router.get("/top-customers")
def get_top_customers(user: dict = Depends(require_gm_with_dept3)):
    """Returns top-customer analysis for vehicle, housing, and cross-department segments."""
    df_v, df_h, df_c = comparison_service.get_customer_analysis_data()
    return {
        "vehicle": df_v.to_dict(orient="records") if df_v is not None and not df_v.empty else [],
        "housing": df_h.to_dict(orient="records") if df_h is not None and not df_h.empty else [],
        "cross": df_c.to_dict(orient="records") if df_c is not None and not df_c.empty else [],
    }


@router.get("/gm-logs")
def get_gm_logs(user: dict = Depends(require_gm_with_dept3)):
    """Returns General Manager security access logs."""
    df = comparison_service.get_gm_security_logs()
    if df is None or df.empty:
        return []
    df = df.copy()
    df["Tarih / Saat"] = df["Tarih / Saat"].astype(str)
    return df.to_dict(orient="records")
