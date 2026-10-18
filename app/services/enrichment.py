"""Оркестрация обогащения: сводит данные из dom.mos.ru и openaddress.ru."""

import re
import time
from concurrent.futures import ThreadPoolExecutor

from app.core.cache import cache_get, cache_set
from app.core.config import settings
from app.services import dom_mos
from app.services.canonical import to_canonical
from app.services.openaddress import enrich as openaddress_enrich
from app.services.gis_zhkh import enrich as gis_zhkh_enrich
from app.services.gis_zhkh import passport as gis_zhkh_passport
from app.services.osm import entrances as osm_entrances
from app.services.beeline import check_connection as beeline_check_connection
from app.services.beeline import resolve_address


class BuildingNotFound(Exception):
    """Ни один источник не вернул данные по адресу."""


def enrich(address: str) -> dict:
    """Обогащает адрес данными о доме.

    Устойчиво к сбоям источников: если dom.mos.ru или openaddress.ru
    не отвечает или не знает адрес — возвращаем данные из другого,
    а проблему фиксируем в `sources`/`warnings`.
    """
    cached = cache_get(address)
    if cached is not None:
        return cached

    session = dom_mos._get_session()
    clean_address = dom_mos.strip_apartment(address)
    house_number = dom_mos.extract_house_number(clean_address)
    corpus = dom_mos.extract_corpus(clean_address)
    query = dom_mos.normalize(clean_address)

    sources = {
        "dom_mos": "error", "openaddress": "error", "gis_zhkh": "error",
        "gis_zhkh_passport": "disabled", "osm": "disabled", "beeline": "error",
    }
    warnings: list[str] = []
    mkd: dict | None = None
    mkd_id: str | None = None
    alternatives: list[dict] = []

    # 1. dom.mos.ru (основной источник)
    try:
        found = dom_mos.search_buildings(query, session)
        if not found:
            fallback = dom_mos.normalize(
                re.sub(r"\b\d+[а-яёА-ЯЁ]*\b", " ", clean_address)
            )
            if fallback:
                found = dom_mos.search_buildings(fallback, session)
        if not found:
            sources["dom_mos"] = "no_data"
            warnings.append("dom.mos.ru: по адресу ничего не найдено")
        else:
            top = dom_mos.pick_best(found, house_number, corpus)
            mkd_id = top["mkdId"]
            mkd = dom_mos.fetch_building(top["mkdId"], top["mkdResponseType"], session)
            alternatives = [
                {"mkdId": b["mkdId"], "address": b["address"]} for b in found[1:]
            ]
            sources["dom_mos"] = "ok"
    except Exception as exc:  # noqa: BLE001
        sources["dom_mos"] = "error"
        warnings.append(f"dom.mos.ru недоступен: {exc}")

    # 2. openaddress.ru (обогащение)
    enrichment: dict = {}
    try:
        # передаём исходный адрес: dom.mos.normalize() ломает порядковые
        # «2-я»/«1-я» в названиях улиц, а для ФИАС-поиска нужен точный запрос
        enrichment = openaddress_enrich(address, house_number)
        if enrichment:
            sources["openaddress"] = "ok"
        else:
            sources["openaddress"] = "no_data"
            warnings.append("openaddress.ru: по адресу ничего не найдено")
    except Exception as exc:  # noqa: BLE001
        sources["openaddress"] = "error"
        warnings.append(f"openaddress.ru недоступен: {exc}")

    # 3. ГИС ЖКХ — характеристики дома и реквизиты текущей УК
    gis_zhkh: dict = {}
    try:
        gis_zhkh = gis_zhkh_enrich(address, (enrichment or {}).get("fiasId"))
        if gis_zhkh:
            sources["gis_zhkh"] = "ok"
        elif not (enrichment or {}).get("fiasId") and not settings.gis_zhkh_index_path:
            sources["gis_zhkh"] = "no_data"
            warnings.append(
                "ГИС ЖКХ: нет ФИАС дома (индекс не настроен, openaddress не вернул ФИАС)"
            )
        else:
            sources["gis_zhkh"] = "no_data"
            warnings.append("ГИС ЖКХ: по адресу ничего не найдено")
    except Exception as exc:  # noqa: BLE001
        sources["gis_zhkh"] = "error"
        warnings.append(f"ГИС ЖКХ недоступен: {exc}")

    # 4. Официальный электронный паспорт МКД: квартиры, лифты и элементы дома.
    electronic_passport: dict = {}
    if settings.gis_zhkh_passport_enabled and gis_zhkh.get("gisHouseGuid"):
        try:
            electronic_passport = gis_zhkh_passport(gis_zhkh["gisHouseGuid"])
            gis_zhkh["electronicPassport"] = electronic_passport
            sources["gis_zhkh_passport"] = "ok"
        except Exception as exc:  # noqa: BLE001
            sources["gis_zhkh_passport"] = "error"
            warnings.append(f"Электронный паспорт ГИС ЖКХ недоступен: {exc}")
    elif settings.gis_zhkh_passport_enabled:
        sources["gis_zhkh_passport"] = "skipped"

    # 5. OSM: реальные входы и диапазоны квартир; расчет явно помечается estimate.
    osm: dict = {}
    if settings.osm_enabled:
        try:
            osm = osm_entrances(
                (mkd or {}).get("fullAddress") or gis_zhkh.get("address") or address,
                flats_total=_as_int(electronic_passport.get("apartments")),
                entrances_count=_as_int((enrichment or {}).get("entrances")),
                flat=_as_int(_extract_flat(address)),
            )
            if osm.get("found"):
                sources["osm"] = "ok"
            else:
                sources["osm"] = "no_data"
                warnings.append(f"OpenStreetMap: {osm.get('reason', 'данные о подъездах не найдены')}")
        except Exception as exc:  # noqa: BLE001
            sources["osm"] = "error"
            warnings.append(f"OpenStreetMap недоступен: {exc}")

    merged = {**enrichment, **gis_zhkh}
    beeline: dict = {}
    try:
        beeline_payload = _beeline_payload(address, house_number, mkd, merged)
        beeline = beeline_check_connection(beeline_payload)
        sources["beeline"] = "ok" if beeline.get("checked") else "skipped"
        if beeline.get("status") == "skipped":
            warnings.append(f"Beeline: {beeline.get('reason')}")
    except Exception as exc:  # noqa: BLE001
        sources["beeline"] = "error"
        warnings.append(f"Beeline недоступен: {exc}")

    providers = [beeline] if beeline else []

    # Ни один источник не дал данных
    if mkd is None and not enrichment and not gis_zhkh:
        raise BuildingNotFound(
            "; ".join(warnings) or f"По адресу {address!r} ничего не найдено"
        )

    result: dict = {
        "canonical": to_canonical(mkd, merged, providers, osm),
        "sources": sources,
        "warnings": warnings,
        "openaddress": enrichment,
        "gis_zhkh": gis_zhkh,
        "gis_zhkh_passport": electronic_passport,
        "osm": osm,
        "beeline": beeline,
        "mkdId": mkd_id,
        "address": (mkd or {}).get("fullAddress") or enrichment.get("address") or gis_zhkh.get("address"),
        "info": None,
        "full": mkd,
        "alternatives": alternatives,
    }

    if mkd is not None:
        result["info"] = {
            "Год постройки": mkd.get("constructionYear"),
            "Серия проекта": mkd.get("projectSeriesName"),
            "Этажность": mkd.get("floorsCount"),
            "Общая площадь": mkd.get("square"),
            "Общая площадь жилых помещений": mkd.get("residentialSquare"),
        }

    # Кэшируем только полностью успешные результаты: если хоть один источник
    # вернул error или no_data, не кэшируем — иначе устаревший/неполный ответ
    # застрянет в Redis и замаскирует реальные проблемы источников.
    if all(status in ("ok", "disabled", "skipped") for status in sources.values()):
        cache_set(address, result)

    return result


def _beeline_payload(address: str, house_number: str | None, mkd: dict | None, data: dict) -> dict:
    """Собирает минимальный payload из данных первых источников."""
    # get-address Beeline точнее fuzzy-поиска: отдаёт правильный ФИАС дома и
    # существующие номера квартир (нумерация не всегда начинается с 1).
    resolved = resolve_address(address) or {}
    city_fias = (
        resolved.get("city_fias_id")
        or _find_value((mkd, data), "cityFiasId")
        or settings.beeline_city_fias_id
    )
    region_fias = (
        resolved.get("region_fias_id")
        or _find_value((mkd, data), "regionFiasId")
        or settings.beeline_region_fias_id
    )
    house_fias = resolved.get("house_fias_id") or data.get("fiasId")
    street = (
        resolved.get("street_with_type")
        or data.get("streetWithType")
        or _find_value((mkd, data), "streetWithType")
    )
    city = (
        resolved.get("city")
        or data.get("city")
        or _find_value((mkd, data), "city")
        or settings.beeline_city
    )
    payload = {
        "city": city,
        "cityFiasId": city_fias,
        "cityWithType": (
            resolved.get("city_with_type")
            or data.get("cityWithType")
            or settings.beeline_city_with_type
        ),
        "houseFiasId": house_fias,
        "houseType": "д",
        "house": resolved.get("house") or data.get("house") or house_number,
        "flat": resolved.get("flat") or data.get("flat") or _extract_flat(address) or "1",
        "flatType": "кв",
        "regionFiasId": region_fias,
        "streetWithType": street or _street_from_address(address),
    }
    if resolved.get("block"):
        payload["block"] = resolved["block"]
        payload["blockType"] = resolved.get("block_type") or "к"
    return payload


def _extract_flat(address: str) -> str | None:
    """Извлекает номер квартиры/помещения из адреса."""
    m = re.search(r"\b(?:кв|квартира|кварт|офис|помещение)\s*\.?\s*(\d+)", address, re.IGNORECASE)
    return m.group(1) if m else None


def _as_int(value) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _find_value(objects: tuple[dict | None, ...], key: str):
    for obj in objects:
        if isinstance(obj, dict) and obj.get(key):
            return obj[key]
        if isinstance(obj, dict):
            for value in obj.values():
                if isinstance(value, dict) and value.get(key):
                    return value[key]
    return None


def _street_from_address(address: str) -> str | None:
    value = re.sub(r"\b(?:г|город|москва|дом|д)\.?\s*", "", address, flags=re.IGNORECASE)
    value = re.sub(r"[,].*$", "", value).strip()
    return value or None


def enrich_batch(
    addresses: list[str], concurrency: int | None = None, delay: float | None = None
) -> list[dict]:
    """Обогащает список адресов, возвращая по записи на каждый адрес.

    Каждая запись: {"address", "status": ok|not_found|error, "data", "error"}.
    Параллелизм ограничен, чтобы не попасть под блокировку DDoS-Guard.
    """
    concurrency = settings.batch_concurrency if concurrency is None else concurrency
    delay = settings.batch_delay if delay is None else delay

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = []
        for addr in addresses:
            futures.append(pool.submit(enrich, addr))
            if delay:
                time.sleep(delay)

        for addr, fut in zip(addresses, futures):
            try:
                results.append({"address": addr, "status": "ok", "data": fut.result()})
            except BuildingNotFound as exc:
                results.append(
                    {"address": addr, "status": "not_found", "data": None, "error": str(exc)}
                )
            except Exception as exc:  # noqa: BLE001
                results.append(
                    {"address": addr, "status": "error", "data": None, "error": str(exc)}
                )

    return results
