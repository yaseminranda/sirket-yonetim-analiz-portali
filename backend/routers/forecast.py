"""API endpoints for AI-based revenue/demand forecasting, cancellation risk, and capacity analysis (General Manager only)."""
from fastapi import APIRouter, Depends, Query

from dependencies import require_gm_only
from schemas import CancellationRiskRequest
from services import ml_service

router = APIRouter(prefix="/forecast", tags=["Yapay Zekâ Tahmin"])


@router.get("/revenue-demand")
def get_revenue_demand_forecast(
    months: int = Query(6, ge=1, le=60), user: dict = Depends(require_gm_only)
):
    """Trains the revenue/demand model and returns predictions for the requested number of future months."""
    models, monthly_data = ml_service.train_revenue_demand_model()
    if models is None:
        message = monthly_data if isinstance(monthly_data, str) else "Yeterli geçmiş veri bulunamadı."
        return {"available": False, "message": message, "predictions": []}

    df_pred = ml_service.predict_future(models, future_months=months)
    return {"available": True, "predictions": df_pred.to_dict(orient="records") if not df_pred.empty else []}


@router.post("/cancellation-risk")
def get_cancellation_risk(payload: CancellationRiskRequest, user: dict = Depends(require_gm_only)):
    """Trains the cancellation model and returns the predicted cancellation risk for the given contract parameters."""
    model = ml_service.train_cancellation_model()
    risk = ml_service.predict_cancellation_risk(model, payload.duration, payload.price, payload.category)
    return {"risk_percentage": risk}


@router.get("/capacity-investment")
def get_capacity_investment(user: dict = Depends(require_gm_only)):
    """Returns capacity and investment analysis results."""
    return ml_service.analyze_capacity_and_investment()
