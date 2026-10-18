"""Pydantic-схемы API."""

from pydantic import BaseModel, Field

from app.services.canonical import CanonicalBuilding


class EnrichmentResult(BaseModel):
    canonical: CanonicalBuilding
    sources: dict[str, str]
    mkdId: str | None = None
    address: str | None = None
    info: dict | None = None
    openaddress: dict = Field(default_factory=dict)
    gis_zhkh: dict = Field(default_factory=dict)
    gis_zhkh_passport: dict = Field(default_factory=dict)
    osm: dict = Field(default_factory=dict)
    beeline: dict = Field(default_factory=dict)
    full: dict | None = None
    alternatives: list[dict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


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
