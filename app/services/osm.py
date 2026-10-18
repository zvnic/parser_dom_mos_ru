"""Подъезды и параметры здания из OpenStreetMap (ODbL)."""

import json
import re
import time
import urllib.parse
import urllib.request

from app.core.config import settings

NOMINATIM = "https://nominatim.openstreetmap.org/search"
OVERPASS = "https://overpass-api.de/api/interpreter"


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


def _query_address(address: str) -> str:
    value = re.sub(r"^.*?Москва,\s*", "", address, flags=re.I)
    value = re.sub(r",\s*(?:кв|квартира)\.?\s*\d+.*$", "", value, flags=re.I)
    for pattern, replacement in (
        (r"\bпр-кт\.?", "проспект"), (r"\bул\.?", "улица"),
        (r"\bб-р\.?", "бульвар"), (r"\bпер\.?", "переулок"),
        (r"\bнаб\.?", "набережная"), (r"\bш\.?", "шоссе"),
        (r"\bпл\.?", "площадь"),
    ):
        value = re.sub(pattern, replacement, value, flags=re.I)
    value = re.sub(r"\bд\.?\s*", "", value, flags=re.I)
    value = re.sub(r"\s+к\s*(\d+)", r" к\1", value, flags=re.I)
    value = re.sub(r"\s+стр\.?\s*(\d+)", r" с\1", value, flags=re.I)
    return "Москва, " + value.strip(" ,")


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
        "format": "json", "limit": 1, "extratags": 1,
        "countrycodes": "ru", "q": _query_address(address),
    })
    hits = _get(NOMINATIM + "?" + query)
    # Публичный Nominatim требует не более одного запроса в секунду.
    time.sleep(1.1)
    building = next(
        (hit for hit in hits if hit.get("osm_type") == "way" and hit.get("class") == "building"), None
    )
    if not building:
        return {"found": False, "address": address, "reason": "здание не найдено в OSM"}
    way = int(building["osm_id"])
    tags = building.get("extratags") or {}
    overpass_query = f'[out:json][timeout:60];way({way});node(w)["entrance"];out body;'
    elements = _get(
        OVERPASS, urllib.parse.urlencode({"data": overpass_query}).encode()
    ).get("elements", [])
    osm_flats = _number(tags.get("building:flats"))
    total = flats_total or osm_flats
    items = []
    for node in elements:
        node_tags = node.get("tags") or {}
        range_ = _flat_range(node_tags.get("addr:flats"))
        reference = node_tags.get("ref")
        number = int(reference) if str(reference or "").isdigit() else reference
        if not range_ and not number:
            continue
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
        "found": True, "address": address, "osmWay": way,
        "levels": _number(tags.get("building:levels")), "flatsTotal": total,
        "flatsSource": "passport" if flats_total else ("osm" if osm_flats else None),
        "entrancesCount": len(items) or None, "entrances": items,
        "flatEntrance": flat_entrance,
        "coverage": "osm" if items and osm_count == len(items) else "partial" if osm_count else "estimate" if items and total else "none",
        "source": "OpenStreetMap (© участники OpenStreetMap, ODbL)",
    }
