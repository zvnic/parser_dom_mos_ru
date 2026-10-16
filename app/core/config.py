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


settings = Settings()
