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
| [ГИС ЖКХ](https://dom.gosuslugi.ru/) | ФИАС/GUID, официальный электронный паспорт (квартиры, лифты, помещения, площади, элементы износа), реквизиты УК и РСО |
| [OpenStreetMap](https://www.openstreetmap.org/) | этажность, подъезды, диапазоны квартир и координаты дверей; расчетные значения явно помечаются `estimate` |
| [Beeline](https://moskva.beeline.ru/customers/products/home/internet/) | проверка доступности домашнего интернета по ФИАС города и дома |

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
    gis_zhkh.py        # клиент ГИС ЖКХ (dom.gosuslugi.ru)
    osm.py             # подъезды и диапазоны квартир OpenStreetMap
    beeline.py         # проверка доступности сети Beeline
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
    "non_residential_premises_count": 3,
    "parking_places_count": 0,
    "entrances_coverage": "osm",
    "entrances": [
      {"number": 1, "flats_from": 1, "flats_to": 55, "lat": 55.7, "lon": 37.7, "source": "osm"}
    ],
    "resource_providers": [
      {"name": "АО МОСВОДОКАНАЛ", "phone": "+7 …", "guid": "…"}
    ],
    "field_sources": {
      "apartments_count": "gis_zhkh_passport",
      "entrances_count": "osm",
      "floors_count": "dom_mos"
    },
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
`canonical.field_sources` показывает, какой источник фактически выбран для
ключевых числовых характеристик; это позволяет отличить официальный паспорт от
OSM и резервных источников.
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
| `ENRICH_GIS_ZHKH_INDEX_PATH` | `/app/data/gis_zhkh_index.json` | путь к индексу адресов/ФИАС из реестра лицензий ГИС ЖКХ |
| `ENRICH_GIS_ZHKH_PASSPORT_ENABLED` | `true` | получать официальный электронный паспорт МКД |
| `ENRICH_GIS_ZHKH_PASSPORT_POLL_ATTEMPTS` | `30` | максимум опросов готовности паспорта |
| `ENRICH_OSM_ENABLED` | `true` | получать подъезды и параметры здания из OSM |
| `ENRICH_OSM_USER_AGENT` | `dom-mos-enrichment/1.0` | идентификатор клиента Nominatim/Overpass |
| `ENRICH_BEELINE_ENABLED` | `true` | включить проверку подключения дома к Beeline |
| `ENRICH_BEELINE_TIMEOUT` | `10` | таймаут запроса к Beeline, сек |

Проверка Beeline выполняется только если первые источники вернули `houseFiasId`
и `cityFiasId`. Cookies и авторизация не используются. Результат доступен в
`beeline` и в `canonical.provider_availability`; без необходимых ФИАС статус
будет `skipped`.

### Индекс ГИС ЖКХ

Индекс адресов строится из актуальных реестров лицензий Москвы и Московской
области и атомарно записывается в Docker volume:

```bash
make gis-index
```

Команду рекомендуется запускать раз в неделю. Для других регионов можно вызвать
`python -m app.scripts.build_gis_index <коды регионов>` внутри контейнера.
Электронный паспорт запрашивается только после получения GUID дома. Отказ
паспорта или OSM не ломает весь ответ: отдельный статус и причина появляются в
`sources` и `warnings`.

Диапазоны квартир подъезда с `source: "osm"` взяты из OSM. Значения с
`source: "estimate"` рассчитаны равномерно и не должны показываться как
официальные. Если в OSM есть контур здания, но нет размеченных входов,
координаты подъездов также приблизительно распределяются вдоль самого длинного
фасада и помечаются `source: "estimate"`. Данные OSM используются по лицензии ODbL с атрибуцией
«© участники OpenStreetMap».

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
