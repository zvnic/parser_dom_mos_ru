"""Конфигурация микросервиса через переменные окружения (префикс ENRICH_)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ENRICH_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    log_level: str = "INFO"
    request_timeout: float = 15.0
    request_retries: int = 2
    batch_concurrency: int = 1
    batch_delay: float = 0.5

    # Кэширование
    cache_enabled: bool = True
    cache_ttl: int = 2592000  # 30 дней (данные о домах почти не меняются)
    redis_url: str = "redis://redis:6379/0"
    gis_zhkh_index_path: str | None = None
    beeline_enabled: bool = True
    beeline_timeout: float = 10.0
    # moskva.beeline.ru — только Москва, поэтому ФИАС города/региона фиксирован
    beeline_city_fias_id: str = "0c5b2444-70a0-4932-980c-b4dc0d3f02b5"
    beeline_region_fias_id: str = "0c5b2444-70a0-4932-980c-b4dc0d3f02b5"
    beeline_city_with_type: str = "г Москва"
    beeline_city: str = "Москва"


settings = Settings()
