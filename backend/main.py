"""FastAPI application entry point: creates the app, wires routers, error handlers, and the background reminder scheduler."""
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

logger = logging.getLogger("scheduler")

app = FastAPI(
    title="Şirket Yönetim & Analiz Portalı API",
    description="Araç ve ev kiralama, ödeme yönetimi, karşılaştırma ve yapay zekâ tahmin servisleri.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc: RequestValidationError):
    """Converts pydantic validation errors into a single readable message instead of FastAPI's raw error list."""
    messages = []
    for err in exc.errors():
        msg = err.get("msg", "Geçersiz değer.")
        if msg.startswith("Value error, "):
            msg = msg[len("Value error, "):]
        if msg not in messages:
            messages.append(msg)
    return JSONResponse(status_code=422, content={"detail": " ".join(messages) if messages else "Geçersiz istek."})


@app.get("/health", tags=["Sistem"])
def health_check():
    """Simple liveness check endpoint."""
    return {"status": "ok"}


from routers import auth, comparison, contracts, customers, finance, forecast, housing, vehicles  # noqa: E402
from services import finance_service, housing_service, vehicle_service  # noqa: E402

app.include_router(auth.router)
app.include_router(vehicles.router)
app.include_router(housing.router)
app.include_router(finance.router)
app.include_router(comparison.router)
app.include_router(forecast.router)
app.include_router(customers.router)
app.include_router(contracts.router)


scheduler = BackgroundScheduler(timezone="Europe/Istanbul")


def _run_daily_reminder_sweep() -> None:
    """Sends bulk payment reminder SMS/notifications as a scheduled daily job."""
    try:
        success_count, fail_count, first_error = finance_service.send_bulk_reminders(
            employee_id="SYSTEM", department_id="AUTO", method="sms"
        )
        logger.info(
            "Otomatik ödeme hatırlatma görevi tamamlandı: %s başarılı, %s başarısız. %s",
            success_count, fail_count, f"(İlk hata: {first_error})" if first_error else "",
        )
    except Exception:
        logger.exception("Otomatik ödeme hatırlatma görevi çalıştırılırken hata oluştu.")


def _run_daily_housing_expiry_sweep() -> None:
    """Sends one-time contract-expiry reminders for housing contracts ending within 30 days, as a scheduled daily job."""
    try:
        success_count, fail_count, first_error = housing_service.run_daily_expiry_reminder_sweep(
            employee_id="SYSTEM", department_id="AUTO"
        )
        logger.info(
            "Otomatik sözleşme bitiş hatırlatma görevi tamamlandı: %s başarılı, %s başarısız. %s",
            success_count, fail_count, f"(İlk hata: {first_error})" if first_error else "",
        )
    except Exception:
        logger.exception("Otomatik sözleşme bitiş hatırlatma görevi çalıştırılırken hata oluştu.")


def _run_daily_vehicle_expiry_sweep() -> None:
    """Sends one-time contract-expiry reminders for vehicle contracts ending within 0-3 days, as a scheduled daily job."""
    try:
        success_count, fail_count, first_error = vehicle_service.run_daily_expiry_reminder_sweep(
            employee_id="SYSTEM", department_id="AUTO"
        )
        logger.info(
            "Otomatik araç sözleşme bitiş hatırlatma görevi tamamlandı: %s başarılı, %s başarısız. %s",
            success_count, fail_count, f"(İlk hata: {first_error})" if first_error else "",
        )
    except Exception:
        logger.exception("Otomatik araç sözleşme bitiş hatırlatma görevi çalıştırılırken hata oluştu.")


@app.on_event("startup")
def start_scheduler() -> None:
    """Registers and starts the daily reminder jobs when the app starts up."""
    if not scheduler.running:
        scheduler.add_job(_run_daily_reminder_sweep, "cron", hour=9, minute=0, id="daily_reminder_sweep", replace_existing=True)
        scheduler.add_job(
            _run_daily_housing_expiry_sweep, "cron", hour=9, minute=0, id="daily_housing_expiry_sweep", replace_existing=True
        )
        scheduler.add_job(
            _run_daily_vehicle_expiry_sweep, "cron", hour=9, minute=0, id="daily_vehicle_expiry_sweep", replace_existing=True
        )
        scheduler.start()


@app.on_event("shutdown")
def stop_scheduler() -> None:
    """Stops the background scheduler when the app shuts down."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
