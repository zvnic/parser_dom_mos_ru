"""Клиент публичного API ГИС ЖКХ (dom.gosuslugi.ru).

Источник дополняет dom.mos.ru характеристиками дома и сведениями об УК.
Для поиска по адресу нужен предварительно собранный индекс лицензий
(`ENRICH_GIS_ZHKH_INDEX_PATH`). ФИАС можно передать напрямую из другого
источника, поэтому карточка также умеет работать без индекса.
"""

import json
import re
import ssl
import urllib.parse
import urllib.request
from datetime import date
from functools import lru_cache

from app.core.config import settings

BASE = "https://dom.gosuslugi.ru"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://dom.gosuslugi.ru/",
    "Accept": "application/json, text/plain, */*",
}
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def _http(path: str) -> dict:
    url = path if path.startswith("http") else BASE + path
    request = urllib.request.Request(url, headers=HEADERS)
    last = None
    for attempt in range(settings.request_retries):
        try:
            with urllib.request.urlopen(request, timeout=settings.request_timeout, context=_CTX) as response:
                return json.loads(response.read() or b"{}")
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt + 1 < settings.request_retries:
                continue
    raise last


def address_key(address: str | None) -> str | None:
    """Нормализует адрес для сопоставления с индексом лицензий ГИС ЖКХ."""
    text = str(address or "").lower().replace("ё", "е").replace(".", " ")
    text = re.sub(r"(\d)\s*к\s*(\d)", r"\1 корп \2", text)
    match = re.search(r"\b(?:д|дом|вл|владение)\s*(\d+[а-я]?(?:/\d+[а-я]?)?)", text)
    if not match:
        return None
    house = match.group(1)
    tail = text[match.end():]
    corp = re.search(r"\b(?:корп|корпус|к)\s*(\d+[а-я]?)", tail)
    structure = re.search(r"\b(?:стр|строение|с)\s*(\d+[а-я]?)", tail)
    street = re.sub(r"\b(россия|г|город|москва|ул|улица|проспект|пр-кт|пр|переулок|пер|обл|область)\b", " ", text[:match.start()])
    words = sorted(re.sub(r"[^0-9а-яa-z]+", " ", street).split())
    if not words:
        return None
    return "|".join([" ".join(words), house] + (["к" + corp.group(1)] if corp else []) + (["с" + structure.group(1)] if structure else []))


@lru_cache(maxsize=1)
def _load_index() -> dict:
    path = settings.gis_zhkh_index_path
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as stream:
            return json.load(stream)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _house_guid(fias: str) -> str | None:
    result = _http(f"/homemanagement/api/rest/services/houses/public/houses/searchByFiasHouseCodeList/{urllib.parse.quote(fias)}?useReadOnlyDataSource=true")
    houses = result.get("houseList") or []
    return next((h.get("guid") for h in houses if h.get("guid")), None)


def _name(value):
    if not isinstance(value, dict):
        return value
    key = next((key for key in value if key.endswith("Name") or key in ("houseCondition", "lifeCycleStage")), None)
    return value.get(key) if key else None


def enrich(address: str, fias: str | None = None) -> dict:
    record = None
    if not fias:
        record = _load_index().get(address_key(address) or "")
        if not record:
            return {}
        fias = record.get("fias")
    if not fias:
        return {}
    guid = _house_guid(fias)
    if not guid:
        return {}
    house = _http(f"/homemanagement/api/rest/services/houses/public/1/{guid}")
    organization = house.get("managementOrganization") or {}
    org = _http(f"/ppa/api/rest/services/ppa/public/organizations/orgByGuid?organizationGuid={urllib.parse.quote(organization['guid'])}") if organization.get("guid") else {}
    chief = " ".join(x for x in (org.get("chiefLastName"), org.get("chiefFirstName"), org.get("chiefMiddleName")) if x) or None
    return {
        "fiasId": fias,
        "gisHouseGuid": guid,
        "address": (house.get("address") or {}).get("formattedAddress") or (record or {}).get("address"),
        "buildingType": _name(house.get("houseType")),
        "condition": _name(house.get("houseCondition")),
        "managementType": _name(house.get("houseManagementType")),
        "year": house.get("buildingYear") or house.get("operationYear"),
        "totalSquare": house.get("totalSquare"),
        "residentialSquare": house.get("residentialSquare"),
        "deteriorationPct": house.get("deterioration"),
        "projectSeries": house.get("planSeries"),
        "wallMaterial": house.get("intWallMaterialList"),
        "cadastreNumber": house.get("cadastreNumber"),
        "managementContractDate": house.get("managementContractDate"),
        "managementCompany": organization.get("shortName") or organization.get("fullName") or (record or {}).get("uk_name"),
        "managementCompanyFullName": organization.get("fullName"),
        "managementCompanyInn": org.get("inn") or (record or {}).get("uk_inn"),
        "managementCompanyKpp": org.get("kpp"),
        "managementCompanyOgrn": organization.get("ogrn") or (record or {}).get("uk_ogrn"),
        "managementCompanyPhone": org.get("phone") or organization.get("phone"),
        "managementCompanyEmail": org.get("orgEmail"),
        "managementCompanySite": org.get("url") or organization.get("url"),
        "managementCompanyAddress": org.get("orgAddress") or organization.get("orgAddress"),
        "councilChairman": chief,
        "license": (record or {}).get("license"),
        "managedSince": (record or {}).get("managed_since"),
        "sourceUrl": f"{BASE}/homemanagement/api/rest/services/houses/public/1/{guid}",
    }
