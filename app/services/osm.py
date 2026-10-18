"""Подъезды и параметры здания из OpenStreetMap (ODbL)."""

import json
import re
import time
import urllib.parse
import urllib.request

from app.core.config import settings

NOMINATIM = "https://nominatim.openstreetmap.org/search"
OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)


def _get(url: str, data: bytes | None = None):
    request = urllib.request.Request(url, data=data, headers={"User-Agent": settings.osm_user_agent})
    last: Exception | None = None
    for attempt in range(max(1, settings.request_retries)):
        try:
            with urllib.request.urlopen(request, timeout=settings.request_timeout) as response:
                return json.loads(response.read())
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt + 1 < max(1, settings.request_retries):
                time.sleep(min(2 ** attempt, 4))
    assert last is not None
    raise last


def _overpass_get(data: bytes):
    last: Exception | None = None
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            return _get(endpoint, data)
        except Exception as exc:  # noqa: BLE001
            last = exc
    assert last is not None
    raise last


def _query_address(address: str) -> str:
    value = re.sub(r"^.*?Москва,\s*", "", address, flags=re.I)
    value = re.sub(r",\s*(?:кв|квартира)\.?\s*\d+.*$", "", value, flags=re.I)
    for pattern, replacement in (
        (r"\bпр-кт\.?\s*", "проспект "), (r"\bул\.?\s*", "улица "),
        (r"\bб-р\.?\s*", "бульвар "), (r"\bпер\.?\s*", "переулок "),
        (r"\bнаб\.?\s*", "набережная "), (r"\bш\.?\s*", "шоссе "),
        (r"\bпл\.?\s*", "площадь "),
    ):
        value = re.sub(pattern, replacement, value, flags=re.I)
    value = re.sub(r"\bд\.?\s*", "", value, flags=re.I)
    value = re.sub(r"\s+к\s*(\d+)", r" к\1", value, flags=re.I)
    value = re.sub(r"\s+стр\.?\s*(\d+)", r" с\1", value, flags=re.I)
    return "Москва, " + re.sub(r"\s+", " ", value).strip(" ,")


def _building_hit(hits: list[dict]) -> dict | None:
    return next((
        hit for hit in hits
        if hit.get("osm_type") in {"way", "relation"}
        and (hit.get("class") or hit.get("category")) == "building"
    ), None)


def _entrance_query(osm_type: str, osm_id: int) -> str:
    if osm_type == "relation":
        return (
            f'[out:json][timeout:60];rel({osm_id});'
            '(node(r)["entrance"];way(r);node(w)["entrance"];);out body;'
        )
    return f'[out:json][timeout:60];way({osm_id});node(w)["entrance"];out body;'


def _number(value):
    try:
        number = float(str(value).replace(",", ".").replace(" ", ""))
        return int(number) if number.is_integer() else number
    except (TypeError, ValueError):
        return None


def _flat_range(value):
    match = re.match(r"^\s*(\d+)\s*[-–]\s*(\d+)\s*$", str(value or ""))
    return (int(match.group(1)), int(match.group(2))) if match else None


def entrances(
    address: str, *, flats_total: int | None = None,
    entrances_count: int | None = None, flat: int | None = None,
) -> dict:
    """Возвращает подъезды, диапазоны квартир и координаты дверей."""
    query = urllib.parse.urlencode({
        "format": "jsonv2", "limit": 5, "extratags": 1, "entrances": 1,
        "countrycodes": "ru", "q": _query_address(address),
    })
    hits = _get(NOMINATIM + "?" + query)
    # Публичный Nominatim требует не более одного запроса в секунду.
    time.sleep(1.1)
    building = _building_hit(hits)
    if not building:
        return {"found": False, "address": address, "reason": "здание не найдено в OSM"}
    osm_id = int(building["osm_id"])
    osm_type = building["osm_type"]
    tags = building.get("extratags") or {}
    elements = building.get("entrances") or []
    if not elements:
        overpass_query = _entrance_query(osm_type, osm_id)
        elements = _overpass_get(
            urllib.parse.urlencode({"data": overpass_query}).encode()
        ).get("elements", [])
    osm_flats = _number(tags.get("building:flats"))
    total = flats_total or osm_flats
    items = []
    for node in elements:
        node_tags = node.get("tags") or node.get("extratags") or {}
        range_ = _flat_range(node_tags.get("addr:flats"))
        reference = node_tags.get("ref")
        number = int(reference) if str(reference or "").isdigit() else reference
        items.append({
            "number": number,
            "flatsFrom": range_[0] if range_ else None,
            "flatsTo": range_[1] if range_ else None,
            "lat": node.get("lat"), "lon": node.get("lon"),
            "source": "osm" if range_ else None,
        })
    items.sort(key=lambda item: (
        item["number"] if isinstance(item["number"], int) else 10**6, item["flatsFrom"] or 0
    ))
    if not items and entrances_count:
        items = [
            {"number": number + 1, "flatsFrom": None, "flatsTo": None,
             "lat": None, "lon": None, "source": None}
            for number in range(int(entrances_count))
        ]
    if items and total and not all(item["flatsFrom"] for item in items):
        per_entrance = total / len(items)
        for index, item in enumerate(items):
            if not item["flatsFrom"]:
                item["flatsFrom"] = round(index * per_entrance) + 1
                item["flatsTo"] = round((index + 1) * per_entrance)
                item["source"] = "estimate"
    osm_count = sum(1 for item in items if item["source"] == "osm")
    flat_entrance = next((
        {"entrance": item["number"], "source": item["source"], "lat": item["lat"], "lon": item["lon"]}
        for item in items
        if flat is not None and item["flatsFrom"] and item["flatsFrom"] <= flat <= item["flatsTo"]
    ), None)
    return {
        "found": True, "address": address,
        "osmType": osm_type, "osmId": osm_id,
        "osmWay": osm_id if osm_type == "way" else None,
        "levels": _number(tags.get("building:levels")), "flatsTotal": total,
        "flatsSource": "passport" if flats_total else ("osm" if osm_flats else None),
        "entrancesCount": len(items) or None, "entrances": items,
        "flatEntrance": flat_entrance,
        "coverage": "osm" if items and osm_count == len(items) else "partial" if osm_count else "estimate" if items and total else "none",
        "source": "OpenStreetMap (© участники OpenStreetMap, ODbL)",
    }
