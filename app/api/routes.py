"""REST-эндпоинты микросервиса обогащения."""

from fastapi import APIRouter, HTTPException, Query

from app.api.schemas import BatchRequest, BatchResponse, EnrichmentResult
from app.services.enrichment import BuildingNotFound, enrich, enrich_batch

router = APIRouter(prefix="/api/v1", tags=["enrichment"])


@router.get("/enrich", response_model=EnrichmentResult)
def enrich_address(
    address: str = Query(..., description="Адрес дома, например: 2-я улица Синичкина, дом 22"),
):
    """Обогащает один адрес данными о доме."""
    try:
        return enrich(address)
    except BuildingNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка: {exc}") from exc


@router.post("/enrich/batch", response_model=BatchResponse)
def enrich_addresses_batch(payload: BatchRequest):
    """Обогащает список адресов (каждый результат — отдельная запись)."""
    addresses = [a.strip() for a in payload.addresses if a and a.strip()]
    results = enrich_batch(addresses)

    ok = sum(1 for r in results if r["status"] == "ok")
    not_found = sum(1 for r in results if r["status"] == "not_found")
    error = sum(1 for r in results if r["status"] == "error")

    return BatchResponse(
        results=results, total=len(results), ok=ok, not_found=not_found, error=error
    )
