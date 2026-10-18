"""Каноническая модель объекта (дома) — единый набор основных параметров.

Сводит данные из dom.mos.ru (основной источник) и openaddress.ru
(обогащение) в одну нормализованную структуру.
"""

from pydantic import BaseModel, Field


class ResourceProvider(BaseModel):
    name: str | None = None
    phone: str | None = None
    guid: str | None = None


class BuildingElementWear(BaseModel):
    element: str
    wear_pct: float | None = None


class Entrance(BaseModel):
    number: int | str | None = None
    flats_from: int | None = None
    flats_to: int | None = None
    lat: float | None = None
    lon: float | None = None
    source: str | None = None


class FlatEntrance(BaseModel):
    entrance: int | str | None = None
    source: str | None = None
    lat: float | None = None
    lon: float | None = None


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
    underground_floors_count: int | None = None
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
    management_company_full_name: str | None = None
    management_company_site: str | None = None
    management_company_address: str | None = None
    management_company_license: str | None = None
    management_type: str | None = None
    management_contract_date: str | None = None
    managed_since: str | None = None
    commissioning_year: int | None = None
    non_residential_premises_count: int | None = None
    parking_places_count: int | None = None
    resource_providers: list[ResourceProvider] = Field(default_factory=list)
    building_elements: list[BuildingElementWear] = Field(default_factory=list)
    entrances: list[Entrance] = Field(default_factory=list)
    entrances_coverage: str | None = None
    flat_entrance: FlatEntrance | None = None
    osm_way_id: int | None = None
    field_sources: dict[str, str] = Field(default_factory=dict)
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


def _first(*values):
    """Возвращает первое заполненное значение, не отбрасывая числовой ноль."""
    return next((value for value in values if value is not None), None)


def to_canonical(
    mkd: dict | None = None,
    enrichment: dict | None = None,
    provider_availability: list[dict] | None = None,
    osm: dict | None = None,
) -> CanonicalBuilding:
    mkd = mkd or {}
    enrichment = enrichment or {}
    osm = osm or {}
    passport = enrichment.get("electronicPassport") or {}
    apartments = _first(_int(passport.get("apartments")), _int(enrichment.get("apartments")), _int(osm.get("flatsTotal")))
    entrances_count = _first(_int(osm.get("entrancesCount")), _int(enrichment.get("entrances")))
    lifts = _first(_int(passport.get("lifts")), _int(enrichment.get("lifts")))
    floors = _first(_int(mkd.get("floorsCount")), _int(enrichment.get("floors")), _int(osm.get("levels")))
    total_area = _first(_float(passport.get("buildingAreaSqm")), _float(mkd.get("square")), _float(enrichment.get("totalSquare")))
    residential_area = _first(_float(passport.get("residentialAreaSqm")), _float(mkd.get("residentialSquare")), _float(enrichment.get("residentialSquare")))
    field_sources = {
        "apartments_count": "gis_zhkh_passport" if passport.get("apartments") is not None
        else "openaddress" if enrichment.get("apartments") is not None else "osm",
        "entrances_count": "osm" if osm.get("entrancesCount") is not None else "openaddress",
        "lifts_count": "gis_zhkh_passport" if passport.get("lifts") is not None else "openaddress",
        "floors_count": "dom_mos" if mkd.get("floorsCount") is not None
        else "gis_zhkh" if enrichment.get("floors") is not None else "osm",
        "total_area_sqm": "gis_zhkh_passport" if passport.get("buildingAreaSqm") is not None
        else "dom_mos" if mkd.get("square") is not None else "gis_zhkh",
        "residential_area_sqm": "gis_zhkh_passport" if passport.get("residentialAreaSqm") is not None
        else "dom_mos" if mkd.get("residentialSquare") is not None else "gis_zhkh",
    }
    present = {
        "apartments_count": apartments, "entrances_count": entrances_count,
        "lifts_count": lifts, "floors_count": floors, "total_area_sqm": total_area,
        "residential_area_sqm": residential_area,
    }
    field_sources = {key: source for key, source in field_sources.items() if present[key] is not None}

    return CanonicalBuilding(
        id=_str(mkd.get("id")) or _str(enrichment.get("fiasId")) or "",
        address=mkd.get("fullAddress") or enrichment.get("address") or "",
        postal_index=_str(enrichment.get("postalIndex")),
        okrug=_name(mkd.get("okrug")),
        rayon=_name(mkd.get("rayon")),
        building_type=_name(mkd.get("mkdType")) or _name(enrichment.get("buildingType")),
        construction_year=_int(mkd.get("constructionYear")) or _int(enrichment.get("year")),
        commissioning_year=_first(_int(passport.get("commissioningYear")), _int(enrichment.get("operationYear"))),
        project_series=_name(mkd.get("projectSeriesName")) or _name(enrichment.get("projectSeries")),
        floors_count=floors,
        underground_floors_count=_int(enrichment.get("undergroundFloors")),
        total_area_sqm=total_area,
        residential_area_sqm=residential_area,
        management_company=_name(mkd.get("uoName")) or _name(enrichment.get("managementCompany")),
        management_company_id=_str(mkd.get("uoId")),
        housing_association=_name(mkd.get("zhoName")),
        council_chairman=_name(mkd.get("councilName")),
        apartments_count=apartments,
        entrances_count=entrances_count,
        lifts_count=lifts,
        non_residential_premises_count=_int(passport.get("nonResidentialPremises")),
        parking_places_count=_int(passport.get("parkingPlaces")),
        condition=_name(enrichment.get("state")) or _name(enrichment.get("condition")),
        source_url=_str(enrichment.get("sourceUrl")),
        fias_id=_str(enrichment.get("fiasId")),
        gis_house_guid=_str(enrichment.get("gisHouseGuid")),
        deterioration_pct=_float(enrichment.get("deteriorationPct")),
        wall_material=_name(enrichment.get("wallMaterial")),
        cadastre_number=_str(passport.get("cadastreNumber")) or _str(enrichment.get("cadastreNumber")),
        management_company_inn=_str(enrichment.get("managementCompanyInn")),
        management_company_kpp=_str(enrichment.get("managementCompanyKpp")),
        management_company_ogrn=_str(enrichment.get("managementCompanyOgrn")),
        management_company_phone=_str(enrichment.get("managementCompanyPhone")),
        management_company_email=_str(enrichment.get("managementCompanyEmail")),
        management_company_full_name=_name(enrichment.get("managementCompanyFullName")),
        management_company_site=_str(enrichment.get("managementCompanySite")),
        management_company_address=_name(enrichment.get("managementCompanyAddress")),
        management_company_license=_str(enrichment.get("managementCompanyLicense")),
        management_type=_name(enrichment.get("managementType")),
        management_contract_date=_str(enrichment.get("managementContractDate")),
        managed_since=_str(enrichment.get("managedSince")),
        resource_providers=[
            ResourceProvider(**item) for item in enrichment.get("resourceProviders") or []
        ],
        building_elements=[
            BuildingElementWear(
                element=item.get("element", ""), wear_pct=_float(item.get("wearPct"))
            )
            for item in passport.get("elements") or []
            if item.get("element")
        ],
        entrances=[
            Entrance(
                number=item.get("number"), flats_from=_int(item.get("flatsFrom")),
                flats_to=_int(item.get("flatsTo")), lat=_float(item.get("lat")),
                lon=_float(item.get("lon")), source=_str(item.get("source")),
            )
            for item in osm.get("entrances") or []
        ],
        entrances_coverage=_str(osm.get("coverage")),
        flat_entrance=FlatEntrance(**{
            "entrance": osm["flatEntrance"].get("entrance"),
            "source": osm["flatEntrance"].get("source"),
            "lat": osm["flatEntrance"].get("lat"),
            "lon": osm["flatEntrance"].get("lon"),
        }) if osm.get("flatEntrance") else None,
        osm_way_id=_int(osm.get("osmWay")),
        field_sources=field_sources,
        city_fias_id=_str(enrichment.get("cityFiasId")),
        region_fias_id=_str(enrichment.get("regionFiasId")),
        city=_name(enrichment.get("city")),
        street=_name(enrichment.get("streetWithType") or enrichment.get("street")),
        house_number=_str(enrichment.get("house")),
        provider_availability=provider_availability or [],
    )
