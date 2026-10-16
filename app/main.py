"""Точка входа микросервиса обогащения данных о домах."""

from fastapi import FastAPI

from app.api.routes import router
from app.core.config import settings
from app.core.logging import setup_logging

setup_logging(settings.log_level)

app = FastAPI(
    title="Dom Moscow Enrichment Service",
    description=(
        "Микросервис обогащения данных о домах по адресу: объединяет "
        "dom.mos.ru (основной источник) и openaddress.ru (ФИАС) в единую "
        "каноническую модель. Интегрируется с CRM по REST API."
    ),
    version="1.0.0",
)

app.include_router(router)


@app.get("/health", tags=["health"])
def health():
    """Liveness-пробник."""
    return {"status": "ok"}


@app.get("/ready", tags=["health"])
def ready():
    """Readiness-пробник."""
    return {"status": "ready"}
