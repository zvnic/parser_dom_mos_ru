"""Клиент публичных API ГИС ЖКХ.

Возвращает карточку дома, сведения об управляющей организации и официальный
электронный паспорт МКД. Для поиска по адресу может использовать локальный
индекс реестра лицензий; при наличии ФИАС индекс не требуется.
"""

import json
import re
import ssl
import time
import urllib.parse
import urllib.request
from functools import lru_cache

from app.core.config import settings

BASE = "https://dom.gosuslugi.ru"
DC_API = BASE + "/dc/api"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://dom.gosuslugi.ru/",
    "Accept": "application/json, text/plain, */*",
}
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

_ABBREVIATIONS = (
    r"\bулица\b|\bул\b", r"\bпроспект\b|\bпр-кт\b|\bпросп\b|\bпр\b",
    r"\bпереулок\b|\bпер\b", r"\bшоссе\b|\bш\b",
    r"\bбульвар\b|\bб-р\b|\bбул\b", r"\bпроезд\b|\bпр-д\b",
    r"\bнабережная\b|\bнаб\b", r"\bплощадь\b|\bпл\b",
    r"\bтупик\b|\bтуп\b", r"\bаллея\b|\bал\b",
)
_ADDRESS_NOISE = (
    r"\b(россия|г|город|москва|московская|обл|область|муниципальный|округ|"
    r"район|р-н|вн\s*тер\s*г|поселение|пос|п|мкр|микрорайон|км)\b"
)


def _request(url: str, *, body: dict | None = None, token: str | None = None, raw: bool = False):
    headers = dict(HEADERS)
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        url.replace("http://", "https://"), data=data, headers=headers,
        method="POST" if body is not None else "GET",
    )
    last: Exception | None = None
    for attempt in range(max(1, settings.request_retries)):
        try:
            with urllib.request.urlopen(
                request, timeout=settings.request_timeout, context=_CTX
            ) as response:
                content = response.read()
                return content if raw else json.loads(content or b"{}")
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt + 1 < max(1, settings.request_retries):
                time.sleep(min(2 ** attempt, 4))
    assert last is not None
    raise last


def _http(path: str):
    return _request(path if path.startswith("http") else BASE + path)


def address_key(address: str | None) -> str | None:
    """Нормализует адрес для сопоставления с индексом лицензий ГИС ЖКХ."""
    text = str(address or "").lower().replace("ё", "е")
    text = re.sub(r"^\s*\d{6}\s*,?", "", text)
    text = re.sub(r"(\d)\s*к\s*(\d)", r"\1 корп \2", text)
    text = text.replace(".", " ").replace(",", " , ")
    match = re.search(r"\b(?:д|дом|вл|владение)\s*(\d+[а-я]?(?:/\d+[а-я]?)?)", text)
    if not match:
        return None
    house = match.group(1)
    tail = text[match.end():]
    corpus = re.search(r"\b(?:корп|корпус|к)\s*(\d+[а-я]?)", tail)
    structure = re.search(r"\b(?:стр|строение|с)\s*(\d+[а-я]?)", tail)
    parts = [part.strip() for part in text[:match.start()].split(",") if part.strip()]
    street = parts[-1] if parts else ""
    for pattern in _ABBREVIATIONS:
        street = re.sub(pattern, " ", street)
    street = re.sub(_ADDRESS_NOISE, " ", street)
    words = sorted(re.sub(r"[^0-9а-яa-z]+", " ", street).split())
    if not words:
        return None
    return "|".join(
        [" ".join(words), house]
        + (["к" + corpus.group(1)] if corpus else [])
        + (["с" + structure.group(1)] if structure else [])
    )


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
    result = _http(
        "/homemanagement/api/rest/services/houses/public/houses/"
        f"searchByFiasHouseCodeList/{urllib.parse.quote(fias)}?useReadOnlyDataSource=true"
    )
    return next((house.get("guid") for house in result.get("houseList") or [] if house.get("guid")), None)


def _name(value):
    if not isinstance(value, dict):
        return value
    key = next(
        (key for key in value if key.endswith("Name") or key in ("houseCondition", "lifeCycleStage")),
        None,
    )
    return value.get(key) if key else None


def enrich(address: str, fias: str | None = None) -> dict:
    """Получает публичную карточку дома и управляющей организации."""
    record = None
    if not fias:
        index = _load_index()
        key = address_key(address) or ""
        record = index.get(key)
        if not record and key:
            record = next((value for item_key, value in index.items() if item_key.startswith(key + "|")), None)
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
    org = (
        _http(
            "/ppa/api/rest/services/ppa/public/organizations/orgByGuid?organizationGuid="
            + urllib.parse.quote(organization["guid"])
        )
        if organization.get("guid") else {}
    )
    chief = " ".join(
        value for value in (
            org.get("chiefLastName"), org.get("chiefFirstName"), org.get("chiefMiddleName")
        ) if value
    ) or None
    resources = [
        {"name": item.get("shortName") or item.get("fullName"), "phone": item.get("phone"), "guid": item.get("guid")}
        for item in house.get("resourceProvisionOrganizationList") or []
    ]
    return {
        "fiasId": fias, "gisHouseGuid": guid,
        "address": (house.get("address") or {}).get("formattedAddress") or (record or {}).get("address"),
        "buildingType": _name(house.get("houseType")),
        "condition": _name(house.get("houseCondition")),
        "managementType": _name(house.get("houseManagementType")),
        "year": house.get("buildingYear") or house.get("operationYear"),
        "operationYear": house.get("operationYear"),
        "floors": house.get("floorCount"),
        "undergroundFloors": house.get("undergroundFloorCount"),
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
        "managementCompanyLicense": (record or {}).get("license"),
        "license": (record or {}).get("license"),
        "managedSince": (record or {}).get("managed_since"),
        "resourceProviders": resources,
        "sourceUrl": f"{BASE}/homemanagement/api/rest/services/houses/public/1/{guid}",
    }


def _number(value):
    try:
        number = float(str(value).replace(",", ".").replace(" ", ""))
        return int(number) if number.is_integer() else number
    except (TypeError, ValueError):
        return None


def passport(house_guid: str) -> dict:
    """Получает официальный электронный паспорт МКД через анонимную сессию."""
    link = _http(f"/homemanagement/api/rest/services/passports/container-link/{house_guid}")["url"]
    document_token = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)["token"][0]
    access_token = _request(DC_API + "/auth_microservice/create_anonymous_session", body={})["accessToken"]
    menu_response = _request(
        f"{DC_API}/document_container_generator/get_menu?token={urllib.parse.quote(document_token)}",
        token=access_token,
    )
    menu = json.loads(menu_response["menu"])
    jwt = next(
        item["jwt"] for section in menu["menuSections"] for group in section["subgroup"]
        for item in group["menuItems"] if item["type"] == "webview"
    )
    task = _request(
        DC_API + "/document_container_generator/prepare_copy_dc_front",
        body={"jwt": jwt}, token=access_token,
    )["taskJwt"]
    status = {}
    for _ in range(settings.gis_zhkh_passport_poll_attempts):
        status = _request(
            DC_API + "/document_container_generator/status_result_task_front",
            body={"taskJwt": task, "needProtocol": True}, token=access_token,
        )
        if status.get("taskStatus") == 2:
            break
        if status.get("taskStatus") == 3:
            raise RuntimeError("ГИС ЖКХ не смог сформировать электронный паспорт")
        time.sleep(min(float(status.get("recommendedRequestRetryDelaySecond") or 2), 10))
    else:
        raise TimeoutError("электронный паспорт не сформирован за отведенное время")

    content = _request(f"{status['baseUrl']}/viewer/content?token={urllib.parse.quote(status['token'])}")
    document = next(item for item in content["items"] if item.get("file_type") == "html")
    html = _request(
        f"{document['url']}?token={urllib.parse.quote(status['token'])}", raw=True
    ).decode("utf-8", "ignore")
    rows = []
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I):
        cells = [
            re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", cell)).strip()
            for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.S | re.I)
        ]
        if len(cells) >= 2 and re.match(r"^\d+(\.\d+)*$", cells[0]):
            rows.append(cells)
    by_name = {row[1].lower(): row[-1] for row in rows if len(row) >= 4}

    def pick(*terms):
        return next((value for name, value in by_name.items() if all(term in name for term in terms)), None)

    elements: list[dict] = []
    current: dict | None = None
    for row in rows:
        if len(row) >= 4 and "наименование конструктивного элемента" in row[1].lower():
            current = {"element": row[-1]}
            elements.append(current)
        elif current is not None and len(row) >= 4 and "износ" in row[1].lower():
            current["wearPct"] = _number(row[-1])

    generated_match = re.search(r"Дата и время формирования:\s*(?:<[^>]+>)*\s*([^<]+)", html)
    return {
        "houseGuid": house_guid,
        "generatedAt": generated_match.group(1).strip() if generated_match else None,
        "apartments": _number(pick("жилых помещений", "квартир")),
        "nonResidentialPremises": _number(pick("нежилых помещений")),
        "lifts": _number(pick("количество лифтов")),
        "parkingPlaces": _number(pick("машино-места")),
        "buildingAreaSqm": _number(pick("площадь здания")),
        "residentialAreaSqm": _number(pick("общая площадь жилых помещений")),
        "commissioningYear": pick("год ввода"),
        "cadastreNumber": pick("кадастровый номер"),
        "elements": elements,
        "source": "ГИС ЖКХ, электронный паспорт МКД",
    }
