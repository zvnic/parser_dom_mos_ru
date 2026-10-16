# Parser dom.mos.ru — микросервис обогащения данных о домах

Микросервис, который по адресу дома возвращает информацию о нём из открытых
источников: **dom.mos.ru** (основной) и **openaddress.ru** (ФИАС/ГАР). Данные
сводятся в единую каноническую модель и отдаются по REST API — удобно для
обогащения записей в CRM.

## Источники данных

| Источник | Что даёт |
|---|---|
| [dom.mos.ru](https://dom.mos.ru/) | адрес, округ/район, тип дома, год постройки, серия проекта, этажность, общая/жилая площадь, УК, ЖСК, председатель совета |
| [openaddress.ru](https://openaddress.ru/) | почтовый индекс, УК, число квартир/подъездов/лифтов, состояние дома, ссылка на страницу |

dom.mos.ru защищён DDoS-Guard (TLS-fingerprinting), поэтому запросы
выполняются через `curl_cffi` с имперсонацией браузера Chrome — обычные
`requests`/`curl` на рукопожатии обрываются.

## Возможности

- Поиск и обогащение по одному адресу и батчем.
- Устойчивость к сбоям: если один источник не отвечает или не знает адрес,
  возвращаются данные из другого, а проблема фиксируется в `sources`/`warnings`.
- Кэширование результатов в **Redis** (данные о домах меняются редко) с
  in-memory fallback при недоступности Redis.
- Каноническая модель с нормализованными типами и приоритетом источников.

## Структура

```
app/
  main.py              # FastAPI-приложение + /health, /ready
  api/routes.py        # REST-эндпоинты
  api/schemas.py       # Pydantic-схемы
  core/config.py       # конфиг из ENV (префикс ENRICH_)
  core/logging.py      # логирование
  core/cache.py        # кэш (Redis + in-memory fallback)
  services/
    dom_mos.py         # клиент dom.mos.ru
    openaddress.py     # клиент openaddress.ru
    canonical.py       # каноническая модель
    enrichment.py      # оркестрация + batch
Dockerfile
docker-compose.yml
Makefile
.env.example
```

## Запуск

```bash
cp .env.example .env     # при необходимости отредактировать (порт и т.п.)
docker compose up -d --build
```

Или через Makefile:

```bash
make up          # сборка + запуск
make logs        # логи
make health      # проверка /health
make smoke       # smoke-тест обогащения
make down        # остановка
make clean       # удалить контейнеры и образ
```

Сервис поднимается на `http://localhost:8000` (порт задаётся `ENRICH_PORT`).

## API

### Обогащение одного адреса

```
GET /api/v1/enrich?address=2-я улица Синичкина, дом 22
```

### Батч-обогащение

```
POST /api/v1/enrich/batch
Content-Type: application/json

{ "addresses": ["Шарикоподшипниковская 26", "Большая Калитниковская, д. 38"] }
```

Ответ: `{ results: [...], total, ok, not_found, error }`, где каждая запись —
`{ address, status: ok|not_found|error, data, error }`.

### Служебные

- `GET /health` — liveness
- `GET /ready` — readiness
- `GET /docs` — Swagger-документация

### Формат ответа (сокращённо)

```json
{
  "canonical": {
    "id": "1538407",
    "address": "… Шарикоподшипниковская улица, дом 28",
    "postal_index": "115088",
    "okrug": "Юго-Восточный Административный Округ",
    "rayon": "Южнопортовый",
    "building_type": "МКД",
    "construction_year": 2002,
    "project_series": "П-46м",
    "floors_count": 14,
    "total_area_sqm": 13088.4,
    "residential_area_sqm": 12968.3,
    "management_company": "ГОСУДАРСТВЕННОЕ БЮДЖЕТНОЕ УЧРЕЖДЕНИЕ … «ЖИЛИЩНИК РАЙОНА ЮЖНОПОРТОВЫЙ»",
    "apartments_count": 277,
    "entrances_count": 5,
    "lifts_count": 5,
    "condition": "Исправный",
    "source_url": "https://openaddress.ru/houses/…"
  },
  "sources": { "dom_mos": "ok", "openaddress": "ok" },
  "warnings": [],
  "mkdId": "1538407",
  "openaddress": { "…": "…" },
  "full": { "…": "…" },
  "alternatives": []
}
```

Каноническая модель — `canonical` — основной результат для интеграции.
Остальные поля (`info`, `openaddress`, `full`) оставлены для отладки и
продвинутых сценариев.

## Конфигурация

Все настройки задаются переменными окружения (префикс `ENRICH_`, см.
`.env.example`):

| Переменная | По умолчанию | Описание |
|---|---|---|
| `ENRICH_PORT` | `8000` | порт сервиса (в контейнере и на хосте) |
| `ENRICH_LOG_LEVEL` | `INFO` | уровень логирования |
| `ENRICH_REQUEST_TIMEOUT` | `15` | таймаут запроса к источнику, сек |
| `ENRICH_REQUEST_RETRIES` | `2` | число повторов запроса |
| `ENRICH_BATCH_CONCURRENCY` | `1` | параллелизм в батче (см. ниже) |
| `ENRICH_BATCH_DELAY` | `0.5` | пауза между адресами в батче, сек |
| `ENRICH_CACHE_ENABLED` | `true` | включить кэш |
| `ENRICH_CACHE_TTL` | `2592000` | время жизни записи кэша, сек (30 дней) |
| `ENRICH_REDIS_URL` | `redis://redis:6379/0` | адрес Redis |

> **Важно:** `ENRICH_BATCH_CONCURRENCY` по умолчанию `1` — dom.mos.ru за
> DDoS-Guard блокирует IP при большом числе одновременных запросов.
> Увеличивайте аккуратно.

## Кэширование

Результаты обогащения кэшируются в Redis (TTL 30 дней по умолчанию), так как
данные о домах меняются редко. При недоступности Redis работает
потокобезопасный in-memory fallback. Очистить кэш:

```bash
make flush-cache
```

Кэшируются только «успешные» результаты — ответы с ошибкой источника не
сохраняются.

## Отказоустойчивость

Если один источник не отвечает или не знает адрес, запрос не падает:

| dom.mos.ru | openaddress.ru | Результат |
|---|---|---|
| ok | ok | полный `canonical` |
| ok | error | `canonical` без индекса/квартир/подъездов + warning |
| error | ok | `canonical` на базе openaddress (`id` = FIAS) |
| no_data | no_data | HTTP `404` |

Статусы по источникам — в `sources`, текстовые пояснения — в `warnings`.
