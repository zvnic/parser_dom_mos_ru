"""Кэширование результатов обогащения.

Данные о домах меняются редко, поэтому результаты кэшируются надолго (TTL).
Основной бэкенд — Redis (распределённый, переживает рестарты и реплики).
При недоступности Redis используется потокобезопасный in-memory fallback,
чтобы и во время сбоя Redis повторные запросы не шли во внешние источники.
"""

import hashlib
import json
import threading
import time

from app.core.config import settings


class _MemoryCache:
    """Потокобезопасный in-memory TTL-кэш (fallback при недоступном Redis)."""

    def __init__(self, ttl: int):
        self.ttl = ttl
        self._data: dict[str, tuple[dict, float]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> dict | None:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            value, expires = item
            if time.time() < expires:
                return value
            self._data.pop(key, None)
            return None

    def set(self, key: str, value: dict) -> None:
        with self._lock:
            self._data[key] = (value, time.time() + self.ttl)


_memory = _MemoryCache(settings.cache_ttl)
_redis_client = None
_redis_lock = threading.Lock()


def _get_redis():
    global _redis_client
    if _redis_client is None:
        with _redis_lock:
            if _redis_client is None:
                import redis

                _redis_client = redis.Redis.from_url(
                    settings.redis_url,
                    decode_responses=True,
                    socket_connect_timeout=2,
                    socket_timeout=2,
                )
    return _redis_client


def _key(address: str) -> str:
    digest = hashlib.sha256(address.strip().lower().encode("utf-8")).hexdigest()
    return f"enrich:{digest}"


def _serialize(value: dict) -> str:
    data = dict(value)
    canonical = data.get("canonical")
    if canonical is not None and hasattr(canonical, "model_dump"):
        data["canonical"] = canonical.model_dump()
    return json.dumps(data, ensure_ascii=False)


def cache_get(address: str) -> dict | None:
    """Возвращает закэшированный результат или None."""
    if not settings.cache_enabled:
        return None

    key = _key(address)
    try:
        raw = _get_redis().get(key)
        if raw is not None:
            return json.loads(raw)
    except Exception:  # noqa: BLE001 — Redis недоступен, работаем через память
        return _memory.get(key)
    return None


def cache_set(address: str, value: dict) -> None:
    """Сохраняет результат в кэш."""
    if not settings.cache_enabled:
        return

    key = _key(address)
    try:
        _get_redis().set(key, _serialize(value), ex=settings.cache_ttl)
    except Exception:  # noqa: BLE001 — Redis недоступен, пишем в память
        _memory.set(key, value)
