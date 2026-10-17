"""Pydantic-схемы API."""

from pydantic import BaseModel

from app.services.canonical import CanonicalBuilding


class EnrichmentResult(BaseModel):
    canonical: CanonicalBuilding
    sources: dict[str, str]
    mkdId: str | None = None
    address: str | None = None
    info: dict | None = None
    openaddress: dict = {}
    gis_zhkh: dict = {}
    full: dict | None = None
    alternatives: list[dict] = []
    warnings: list[str] = []


class BatchRequest(BaseModel):
    addresses: list[str]


class BatchItem(BaseModel):
    address: str
    status: str  # ok | not_found | error
    data: EnrichmentResult | None = None
    error: str | None = None


class BatchResponse(BaseModel):
    results: list[BatchItem]
    total: int
    ok: int
    not_found: int
    error: int
