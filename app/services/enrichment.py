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
    house_number = dom_mos.extract_house_number(address)
    query = dom_mos.normalize(address)

    sources = {"dom_mos": "error", "openaddress": "error", "gis_zhkh": "error"}
    warnings: list[str] = []
    mkd: dict | None = None
    mkd_id: str | None = None
    alternatives: list[dict] = []

    # 1. dom.mos.ru (основной источник)
    try:
        found = dom_mos.search_buildings(query, session)
        if not found:
            fallback = dom_mos.normalize(re.sub(r"\b\d+[а-яёА-ЯЁ]*\b", " ", address))
            if fallback:
                found = dom_mos.search_buildings(fallback, session)
        if not found:
            sources["dom_mos"] = "no_data"
            warnings.append("dom.mos.ru: по адресу ничего не найдено")
        else:
            top = dom_mos.pick_best(found, house_number)
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

    # Ни один источник не дал данных
    if mkd is None and not enrichment and not gis_zhkh:
        raise BuildingNotFound(
            "; ".join(warnings) or f"По адресу {address!r} ничего не найдено"
        )

    result: dict = {
        "canonical": to_canonical(mkd, {**enrichment, **gis_zhkh}),
        "sources": sources,
        "warnings": warnings,
        "openaddress": enrichment,
        "gis_zhkh": gis_zhkh,
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

    # Кэшируем только полные/недеградированные результаты (без ошибок источников)
    if all(status != "error" for status in sources.values()):
        cache_set(address, result)

    return result


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
