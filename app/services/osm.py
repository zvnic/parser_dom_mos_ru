"""Подъезды и параметры здания из OpenStreetMap (ODbL)."""

import json
import re
import time
import urllib.parse
import urllib.request

from app.core.config import settings

NOMINATIM = "https://nominatim.openstreetmap.org/search"
OSM_API = "https://api.openstreetmap.org/api/0.6"
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
    value = re.sub(r"^.*?\bМосква,\s*", "", address, flags=re.I)
    value = re.sub(r",\s*(?:кв|квартира)\.?\s*\d+.*$", "", value, flags=re.I)
    for pattern, replacement in (
        (r"\bпр-кт\.?\s*", "проспект "), (r"\bул\.?\s*", "улица "),
        (r"\bб-р\.?\s*", "бульвар "), (r"\bпер\.?\s*", "переулок "),
        (r"\bнаб\.?\s*", "набережная "), (r"\bш\.?\s*", "шоссе "),
        (r"\bпл\.?\s*", "площадь "),
    ):
        value = re.sub(pattern, replacement, value, flags=re.I)
    value = re.sub(r"\bд\.\s*", "дом ", value, flags=re.I)
    value = re.sub(r"\bк\.\s*(?=\d)", "корпус ", value, flags=re.I)
    value = re.sub(r"\bстр\.\s*(?=\d)", "строение ", value, flags=re.I)
    value = re.sub(r"\s+", " ", value).strip(" ,")

    street_types = "улица|бульвар|переулок|набережная|шоссе|площадь|проспект"
    parts = [part.strip() for part in value.split(",") if part.strip()]
    street_index = next((
        index for index, part in enumerate(parts)
        if re.search(rf"\b(?:{street_types})\b", part, flags=re.I)
    ), None)
    if street_index is None:
        return "Москва, " + value

    street = parts[street_index]
    prefix = re.match(rf"^({street_types})\s+(.+)$", street, flags=re.I)
    if prefix:
        street = f"{prefix.group(2)} {prefix.group(1)}"
    building = ", ".join(parts[street_index + 1:])
    house = re.search(r"(?:\bдом\s*)?(\d+[А-ЯA-Z]?)", building, flags=re.I)
    corpus = re.search(r"\b(?:корпус|к)\.?\s*(\d+[А-ЯA-Z]?)", building, flags=re.I)
    structure = re.search(r"\b(?:строение|стр|с)\.?\s*(\d+[А-ЯA-Z]?)", building, flags=re.I)
    if not house:
        return "Москва, " + value
    suffix = f" {house.group(1)}"
    if corpus:
        suffix += f" к{corpus.group(1)}"
    if structure:
        suffix += f" с{structure.group(1)}"
    return f"Москва, {street},{suffix}"


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


def _way_entrances(osm_id: int) -> list[dict]:
    elements = _get(f"{OSM_API}/way/{osm_id}/full.json").get("elements", [])
    return [
        element for element in elements
        if element.get("type") == "node" and (element.get("tags") or {}).get("entrance")
    ]


def _estimated_points(geojson: dict | None, count: int) -> list[tuple[float, float]]:
    if not geojson or count < 1:
        return []
    coordinates = geojson.get("coordinates") or []
    if geojson.get("type") == "Polygon":
        rings = coordinates[:1]
    elif geojson.get("type") == "MultiPolygon":
        rings = [polygon[0] for polygon in coordinates if polygon]
    else:
        rings = []
    edges = [
        (start, end)
        for ring in rings
        for start, end in zip(ring, ring[1:])
        if len(start) >= 2 and len(end) >= 2
    ]
    if not edges:
        return []
    start, end = max(edges, key=lambda edge: (
        (edge[1][0] - edge[0][0]) ** 2 + (edge[1][1] - edge[0][1]) ** 2
    ))
    return [
        (
            start[1] + (end[1] - start[1]) * position / (count + 1),
            start[0] + (end[0] - start[0]) * position / (count + 1),
        )
        for position in range(1, count + 1)
    ]


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
        "polygon_geojson": 1,
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
        if osm_type == "way":
            elements = _way_entrances(osm_id)
        else:
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
        points = _estimated_points(building.get("geojson"), int(entrances_count))
        items = [
            {"number": number + 1, "flatsFrom": None, "flatsTo": None,
             "lat": points[number][0] if number < len(points) else None,
             "lon": points[number][1] if number < len(points) else None,
             "source": "estimate"}
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
