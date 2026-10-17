"""Каноническая модель объекта (дома) — единый набор основных параметров.

Сводит данные из dom.mos.ru (основной источник) и openaddress.ru
(обогащение) в одну нормализованную структуру.
"""

from pydantic import BaseModel, Field


class CanonicalBuilding(BaseModel):
    """Основные параметры дома в едином каноническом виде."""

    id: str
    address: str
    postal_index: str | None = None
    okrug: str | None = None
    rayon: str | None = None
    building_type: str | None = None
    construction_year: int | None = None
    project_series: str | None = None
    floors_count: int | None = None
    total_area_sqm: float | None = None
    residential_area_sqm: float | None = None
    management_company: str | None = None
    management_company_id: str | None = None
    housing_association: str | None = None
    council_chairman: str | None = None
    apartments_count: int | None = None
    entrances_count: int | None = None
    lifts_count: int | None = None
    condition: str | None = None
    source_url: str | None = None
    fias_id: str | None = None
    gis_house_guid: str | None = None
    deterioration_pct: float | None = None
    wall_material: str | None = None
    cadastre_number: str | None = None
    management_company_inn: str | None = None
    management_company_kpp: str | None = None
    management_company_ogrn: str | None = None
    management_company_phone: str | None = None
    management_company_email: str | None = None
    city_fias_id: str | None = None
    region_fias_id: str | None = None
    city: str | None = None
    street: str | None = None
    house_number: str | None = None
    provider_availability: list[dict] = Field(default_factory=list)


def _int(value) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(str(value))
    except (ValueError, TypeError):
        return None


def _float(value) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (ValueError, TypeError):
        return None


def _str(value) -> str | None:
    return str(value) if value not in (None, "") else None


def _name(value) -> str | None:
    """Отбрасывает служебные заглушки типа «Отсутствует»."""
    if value in (None, "", "Отсутствует"):
        return None
    return str(value)


def to_canonical(mkd: dict | None = None, enrichment: dict | None = None, provider_availability: list[dict] | None = None) -> CanonicalBuilding:
    mkd = mkd or {}
    enrichment = enrichment or {}

    return CanonicalBuilding(
        id=_str(mkd.get("id")) or _str(enrichment.get("fiasId")) or "",
        address=mkd.get("fullAddress") or enrichment.get("address") or "",
        postal_index=_str(enrichment.get("postalIndex")),
        okrug=_name(mkd.get("okrug")),
        rayon=_name(mkd.get("rayon")),
        building_type=_name(mkd.get("mkdType")) or _name(enrichment.get("buildingType")),
        construction_year=_int(mkd.get("constructionYear")) or _int(enrichment.get("year")),
        project_series=_name(mkd.get("projectSeriesName")) or _name(enrichment.get("projectSeries")),
        floors_count=_int(mkd.get("floorsCount")) or _int(enrichment.get("floors")),
        total_area_sqm=_float(mkd.get("square")) or _float(enrichment.get("totalSquare")),
        residential_area_sqm=_float(mkd.get("residentialSquare")) or _float(enrichment.get("residentialSquare")),
        management_company=_name(mkd.get("uoName")) or _name(enrichment.get("managementCompany")),
        management_company_id=_str(mkd.get("uoId")),
        housing_association=_name(mkd.get("zhoName")),
        council_chairman=_name(mkd.get("councilName")),
        apartments_count=_int(enrichment.get("apartments")),
        entrances_count=_int(enrichment.get("entrances")),
        lifts_count=_int(enrichment.get("lifts")),
        condition=_name(enrichment.get("state")) or _name(enrichment.get("condition")),
        source_url=_str(enrichment.get("sourceUrl")),
        fias_id=_str(enrichment.get("fiasId")),
        gis_house_guid=_str(enrichment.get("gisHouseGuid")),
        deterioration_pct=_float(enrichment.get("deteriorationPct")),
        wall_material=_name(enrichment.get("wallMaterial")),
        cadastre_number=_str(enrichment.get("cadastreNumber")),
        management_company_inn=_str(enrichment.get("managementCompanyInn")),
        management_company_kpp=_str(enrichment.get("managementCompanyKpp")),
        management_company_ogrn=_str(enrichment.get("managementCompanyOgrn")),
        management_company_phone=_str(enrichment.get("managementCompanyPhone")),
        management_company_email=_str(enrichment.get("managementCompanyEmail")),
        city_fias_id=_str(enrichment.get("cityFiasId")),
        region_fias_id=_str(enrichment.get("regionFiasId")),
        city=_name(enrichment.get("city")),
        street=_name(enrichment.get("streetWithType") or enrichment.get("street")),
        house_number=_str(enrichment.get("house")),
        provider_availability=provider_availability or [],
    )
