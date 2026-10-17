"""Проверка доступности домашнего интернета Beeline по ФИАС дома."""

from datetime import datetime, timezone

from curl_cffi import requests

from app.core.config import settings

URL = "https://moskva.beeline.ru/api/home/check-connection/house-info/"
HEADERS = {
    "accept": "*/*",
    "content-type": "application/json",
    "origin": "https://moskva.beeline.ru",
    "referer": "https://moskva.beeline.ru/customers/products/home/internet/",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "x-requested-with": "XMLHttpRequest",
}


_AVAILABLE_STATES = {"IsAvailable", "IsHighSpeedAvailable", "HasConnectionOnAddress", "HasTicketOnAddress"}
_NOT_AVAILABLE_STATES = {"IsNotAvailable", "HouseIsNotFound", "StreetIsNotFound"}


def _available(payload: dict) -> bool | None:
    """Извлекает флаг доступности из ответа Beeline.

    API отдаёт `connectionState` (enum) и `highSpeedAvailable` (bool),
    а не `available`/`isAvailable`, поэтому парсим именно их.
    """
    state = str(payload.get("connectionState") or "")
    if state in _AVAILABLE_STATES:
        return True
    if state in _NOT_AVAILABLE_STATES:
        return False
    high_speed = payload.get("highSpeedAvailable")
    if isinstance(high_speed, bool):
        return high_speed
    return None


def check_connection(payload: dict) -> dict:
    """Возвращает нормализованный результат без cookies и авторизации."""
    if not settings.beeline_enabled:
        return {"provider": "beeline", "checked": False, "status": "disabled"}
    if not payload.get("houseFiasId") or not payload.get("cityFiasId"):
        return {
            "provider": "beeline",
            "checked": False,
            "available": None,
            "status": "skipped",
            "reason": "недостаточно ФИАС дома или города",
        }

    response = requests.post(URL, json=payload, headers=HEADERS, timeout=settings.beeline_timeout, impersonate="chrome")
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise ValueError("Beeline вернул ответ не в формате JSON-объекта")
    available = _available(body)
    return {
        "provider": "beeline",
        "checked": True,
        "available": available,
        "status": "available" if available is True else "unavailable" if available is False else "unknown",
        "connection_state": body.get("connectionState"),
        "high_speed_available": body.get("highSpeedAvailable"),
        "house_inac_id": body.get("houseInacId"),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "source_url": URL,
        "details": body,
    }
