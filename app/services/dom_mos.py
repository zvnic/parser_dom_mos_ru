"""Клиент dom.mos.ru: HTTP-запросы и разбор ответов.

Сайт защищён DDoS-Guard (TLS-fingerprinting), поэтому запросы выполняются
через curl_cffi с имперсонацией браузера Chrome.
"""

import json
import re
import time

from curl_cffi import requests

from app.core.config import settings

BASE_URL = "https://dom.mos.ru"
AUTOCOMPLETE_URL = f"{BASE_URL}/mediadom/api/search/autocomplete"
MKD_URL = f"{BASE_URL}/mkd"

USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1"
)

MKD_HEADERS = {
    "accept": "*/*",
    "next-router-state-tree": (
        "%5B%22%22%2C%7B%22children%22%3A%5B%22__PAGE__%22%2C%7B%7D%2Cnull%2Cnull%5D%7D"
        "%2Cnull%2Cnull%2Ctrue%5D"
    ),
    "next-url": "/",
    "rsc": "1",
    "referer": f"{BASE_URL}/",
    "user-agent": USER_AGENT,
}


def _get_session() -> requests.Session:
    return requests.Session(impersonate="chrome")


def _get(session, url, *, params=None, headers=None, retries=None, timeout=None):
    """GET-запрос с повторами и таймаутом (для устойчивости к сбоям источника)."""
    retries = settings.request_retries if retries is None else retries
    timeout = settings.request_timeout if timeout is None else timeout
    last = None
    for attempt in range(retries):
        try:
            resp = session.get(url, params=params, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < retries - 1:
                time.sleep(1)
    raise last


def _extract_json_object(text: str, key: str):
    """Извлекает JSON-объект, следующий за '"key":{', с учётом вложенности."""
    marker = f'"{key}":'
    idx = text.find(marker)
    if idx == -1:
        return None
    start = idx + len(marker)
    while start < len(text) and text[start] in " \t\r\n":
        start += 1
    if start >= len(text) or text[start] != "{":
        return None

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    return None


def search_buildings(term: str, session: requests.Session | None = None) -> list[dict]:
    """Возвращает список зданий, найденных по адресу/названию."""
    own_session = session is None
    if own_session:
        session = _get_session()

    resp = _get(
        session,
        AUTOCOMPLETE_URL,
        params={"term": term, "section": "buildings"},
        headers={"accept": "application/json", "user-agent": USER_AGENT},
    )

    data = resp.json()
    results = []
    for item in data.get("body", {}).get("searchResultDtos", []):
        results.append(
            {
                "mkdId": str(item.get("id")),
                "address": item.get("address"),
                "mkdResponseType": item.get("mkdResponseType"),
                "okrugId": item.get("okrugId"),
                "rayonId": item.get("rayonId"),
            }
        )
    return results


def fetch_building(
    mkd_id: str, mkd_response_type: str, session: requests.Session | None = None
) -> dict:
    """Возвращает данные о здании по mkdId."""
    own_session = session is None
    if own_session:
        session = _get_session()

    resp = _get(
        session,
        MKD_URL,
        params={
            "mkdId": mkd_id,
            "mkdResponseType": mkd_response_type,
            "_rsc": "vusbg",
        },
        headers=MKD_HEADERS,
    )

    mkd = _extract_json_object(resp.text, "mkd")
    if mkd is None:
        raise ValueError(f"Не удалось извлечь данные о здании mkdId={mkd_id}")
    return mkd


_IGNORED_WORDS = (
    "российская федерация",
    "россия",
    "город",
    "москва",
    "г",
    "ул",
    "улица",
    "дом",
    "д",
    "проспект",
    "пр-т",
    "пр",
    "переулок",
    "пер",
    "набережная",
    "наб",
    "проезд",
    "шоссе",
    "бульвар",
    "б-р",
    "площадь",
    "пл",
    "строение",
    "стр",
)


def normalize(address: str) -> str:
    """Приводит адрес к виду, понятному автодополнению dom.mos.ru."""
    s = address.lower()
    for word in _IGNORED_WORDS:
        s = re.sub(rf"\b{re.escape(word)}\b\.?", " ", s)
    s = re.sub(r"[^\wа-яёА-ЯЁ\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def extract_house_number(address: str) -> str | None:
    """Извлекает номер дома из адреса."""
    # "50 корпус 2" (без "д.") — номер дома стоит перед словом "корпус".
    m = re.search(
        r"\b(\d+[а-яёА-ЯЁ]?(?:\s*/\s*\d+)?)\s+корпус\b",
        address,
        re.IGNORECASE,
    )
    if m:
        return m.group(1)
    m = re.search(
        r"\b(?:дом|д|строение|стр|корпус|к)\s*\.?\s*(\d+[а-яёА-ЯЁ]*(?:\s*/\s*\d+)?)",
        address,
        re.IGNORECASE,
    )
    if m:
        return m.group(1)
    nums = re.findall(r"\b(\d+[а-яёА-ЯЁ]*(?:/\d+)?)\b", address)
    return nums[-1] if nums else None


def pick_best(candidates: list[dict], house_number: str | None) -> dict:
    """Выбирает кандидата, чей адрес содержит номер дома."""
    if len(candidates) == 1:
        return candidates[0]
    if house_number:
        pattern = rf"\b{re.escape(house_number)}\b"
        for c in candidates:
            if re.search(pattern, c["address"]):
                return c
    return candidates[0]
