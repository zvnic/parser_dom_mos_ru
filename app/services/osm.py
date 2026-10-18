"""Подъезды и параметры здания из OpenStreetMap (ODbL)."""

import json
import re
import threading
import time
import urllib.parse
import urllib.request

from app.core.config import settings
from app.services.osm_query import building_parts, query_variants

NOMINATIM = "https://nominatim.openstreetmap.org/search"
OSM_API = "https://api.openstreetmap.org/api/0.6"
OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
_NOMINATIM_LOCK = threading.Lock()
_NOMINATIM_LAST_REQUEST = 0.0


def _get(url: str, data: bytes | None = None, timeout: float | None = None):
    request = urllib.request.Request(url, data=data, headers={"User-Agent": settings.osm_user_agent})
    last: Exception | None = None
    for attempt in range(max(1, settings.request_retries)):
        try:
            with urllib.request.urlopen(
                request, timeout=timeout or settings.request_timeout
            ) as response:
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
            return _get(endpoint, data, timeout=settings.osm_overpass_timeout)
        except Exception as exc:  # noqa: BLE001
            last = exc
    assert last is not None
    raise last


def _query_address(address: str) -> str:
    return query_variants(address)[0]


def _nominatim_get(url: str):
    """Соблюдает общий лимит публичного Nominatim для процесса."""
    global _NOMINATIM_LAST_REQUEST
    with _NOMINATIM_LOCK:
        wait = 1.1 - (time.monotonic() - _NOMINATIM_LAST_REQUEST)
        if wait > 0:
            time.sleep(wait)
        try:
            return _get(url, timeout=settings.osm_nominatim_timeout)
        finally:
            _NOMINATIM_LAST_REQUEST = time.monotonic()


def _compact(value) -> str:
    return re.sub(r"[^0-9а-яё]", "", str(value or "").lower())


def _building_matches(hit: dict, expected: tuple[str | None, str | None, str | None]) -> bool:
    """Отбрасывает явно соседний дом, если Nominatim вернул его номер."""
    address = hit.get("address") or {}
    tags = hit.get("extratags") or {}
    actual = address.get("house_number") or tags.get("addr:housenumber")
    house, corpus, structure = expected
    if not actual or not house:
        return True
    actual_house, actual_corpus, actual_structure = building_parts(f"д {actual}")
    if _compact(actual_house) != _compact(house):
        return False
    if corpus and actual_corpus and _compact(actual_corpus) != _compact(corpus):
        return False
    if structure and actual_structure and _compact(actual_structure) != _compact(structure):
        return False
    return True


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
    alternate_addresses: list[str] | None = None,
) -> dict:
    """Возвращает подъезды, диапазоны квартир и координаты дверей."""
    candidates = [address, *(alternate_addresses or [])]
    variants: list[tuple[str, tuple[str | None, str | None, str | None]]] = []
    seen: set[str] = set()
    for candidate in candidates:
        expected = building_parts(candidate)
        for variant in query_variants(candidate):
            key = variant.casefold()
            if key not in seen:
                seen.add(key)
                variants.append((variant, expected))
    variants = variants[:max(1, settings.osm_max_query_variants)]

    building, last_error = None, None
    for variant, expected in variants:
        query = urllib.parse.urlencode({
            "format": "jsonv2", "limit": 5, "extratags": 1, "entrances": 1,
            "polygon_geojson": 1, "addressdetails": 1,
            "countrycodes": "ru", "q": variant,
        })
        try:
            hits = _nominatim_get(NOMINATIM + "?" + query)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue
        building = next(
            (hit for hit in hits if _building_hit([hit]) and _building_matches(hit, expected)),
            None,
        )
        if building:
            break
    if not building:
        if last_error is not None:
            return {
                "found": False, "address": address, "retry": True,
                "reason": f"OpenStreetMap временно недоступен: {last_error}",
            }
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
