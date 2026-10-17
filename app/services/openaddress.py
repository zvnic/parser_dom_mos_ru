"""Обогащение данных о доме из openaddress.ru (ФИАС/ГАР).

Дополнительный источник: по улице и номеру дома находим страницу дома
(/houses/{house_fias_id}) и достаём почтовый индекс, УК, число квартир,
подъездов, лифтов и т.п.
"""

import re

from bs4 import BeautifulSoup
from curl_cffi import requests

from app.core.config import settings

BASE = "https://openaddress.ru"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_session: requests.Session | None = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session(impersonate="chrome")
    return _session


def _get(url: str, params: dict | None = None) -> str:
    resp = _get_session().get(
        url, params=params, headers={"user-agent": UA}, timeout=settings.request_timeout
    )
    resp.raise_for_status()
    return resp.text


def _tokenize(text: str) -> set[str]:
    """Токенизирует адрес для сопоставления: «2-я» → «2я», «ё» → «е»."""
    s = text.lower().replace("ё", "е")
    s = re.sub(r"(\d+)-([а-я])", r"\1\2", s)
    return set(re.findall(r"[0-9а-яa-z]+", s))


def search_street(query: str) -> dict | None:
    """Ищет улицу на openaddress.ru, возвращает {name, url, index}.

    Среди выдачи выбирается результат, лучше всего совпадающий с запросом
    (иначе для «2-я Синичкина» сервис мог бы вернуть «1-я Синичкина»).
    """
    html = _get(f"{BASE}/search", params={"search-fias-address": query, "search-type": "fias"})
    soup = BeautifulSoup(html, "html.parser")
    links = soup.select("table.table a.link-blue[href]")
    if not links:
        return None

    q_tokens = _tokenize(query)
    best = max(
        links,
        key=lambda a: len(q_tokens & _tokenize(a.get_text(strip=True))),
    )
    return {
        "name": best.get_text(strip=True),
        "url": best["href"],
        "index": _first_index(html),
    }


def _first_index(html: str) -> str | None:
    m = re.search(r"Почтовый индекс\s*</th>.*?<td>\s*(\d{6})", html, re.S)
    if m:
        return m.group(1)
    return None


def get_street_houses(street_url: str) -> list[dict]:
    """Парсит таблицу многоквартирных домов на странице улицы."""
    html = _get(f"{BASE}{street_url}")
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("#street-houses-table")
    if table is None:
        return []
    houses = []
    for tr in table.select("tbody tr"):
        cells = tr.find_all("td")
        if len(cells) < 5:
            continue
        a = cells[0].find("a", href=True)
        if a is None:
            continue
        houses.append(
            {
                "address": a.get_text(strip=True),
                "fias_id": a["href"].rstrip("/").rsplit("/", 1)[-1],
                "year": cells[1].get_text(strip=True),
                "floors": cells[2].get_text(strip=True),
                "apartments": cells[3].get_text(strip=True),
                "state": cells[4].get_text(strip=True),
            }
        )
    return houses


def _match_house(houses: list[dict], house_number: str | None) -> dict | None:
    if not houses or not house_number:
        return houses[0] if houses else None
    pattern = re.compile(rf"\b{re.escape(house_number)}\b")
    for h in houses:
        if pattern.search(h["address"]):
            return h
    return None


def get_house(fias_id: str) -> dict:
    """Парсит страницу дома, возвращает данные из сводки."""
    html = _get(f"{BASE}/houses/{fias_id}")
    soup = BeautifulSoup(html, "html.parser")

    data = {}
    for item in soup.select(".portal-house-summary__item"):
        label = item.select_one(".portal-house-summary__label")
        value = item.select_one(".portal-house-summary__value")
        if label is not None and value is not None:
            data[label.get_text(strip=True)] = value.get_text(strip=True)

    hero = soup.select_one(".portal-hero__inner p")
    desc = hero.get_text(" ", strip=True) if hero is not None else ""
    m = re.search(r"подъездов\s*[—–-]\s*(\d+)", desc)
    if m:
        data["Подъездов"] = m.group(1)
    m = re.search(r"лифтов\s*[—–-]\s*(\d+)", desc)
    if m:
        data["Лифтов"] = m.group(1)

    title = soup.select_one("h1.portal-hero__title")
    if title is not None:
        full_address = re.sub(r"^дом\s+по\s+адресу\s*", "", title.get_text(strip=True), flags=re.IGNORECASE)
        data["_address"] = full_address

    return data


def enrich(street_query: str, house_number: str | None) -> dict:
    """Возвращает обогащающие данные из openaddress.ru.

    Возвращает пустой dict, если по запросу ничего не найдено.
    В случае сетевой/серверной ошибки исключение пробрасывается наружу.
    """
    street = search_street(street_query)
    if street is None:
        return {}

    houses = get_street_houses(street["url"])
    house = _match_house(houses, house_number)
    if house is None:
        return {}

    detail = get_house(house["fias_id"])

    def pick(*keys):
        for k in keys:
            v = detail.get(k)
            if v:
                return v
        return None

    result = {
        "fiasId": house["fias_id"],
        # адрес дома со страницы дома; при отсутствии — из таблицы улицы
        "address": detail.get("_address") or house["address"],
        # индекс со страницы дома часто некорректен, поэтому берём со страницы улицы
        "postalIndex": street.get("index") or pick("Почтовый индекс"),
        "managementCompany": pick("Управляющая компания"),
        "apartments": pick("Квартир", "Квартиры") or house.get("apartments"),
        "entrances": pick("Подъездов"),
        "lifts": pick("Лифтов"),
        "floors": pick("Этажность") or house.get("floors"),
        "year": pick("Год постройки") or house.get("year"),
        "state": house.get("state"),
        "sourceUrl": f"{BASE}/houses/{house['fias_id']}",
    }
    return {k: v for k, v in result.items() if v not in (None, "")}
