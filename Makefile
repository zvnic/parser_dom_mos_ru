# Управление микросервисом обогащения данных о домах.
# Порт читается из .env (ENRICH_PORT), либо переопределяется: make health ENRICH_PORT=8001

-include .env
ENRICH_PORT ?= 8000
HOST ?= 127.0.0.1

COMPOSE := docker compose
IMAGE := dom-mos-enrichment:latest
CONTAINER := dom-mos-enrichment

.PHONY: help build up down restart logs ps health smoke flush-cache clean

help: ## список доступных команд
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

build: ## собрать Docker-образ
	$(COMPOSE) build

up: ## запустить сервис (с пересборкой) в фоне
	$(COMPOSE) up -d --build

down: ## остановить сервис
	$(COMPOSE) down

restart: ## перезапустить сервис
	$(COMPOSE) restart

logs: ## логи в реальном времени
	$(COMPOSE) logs -f

ps: ## статус контейнеров
	$(COMPOSE) ps

health: ## проверить /health
	@curl -s "http://$(HOST):$(ENRICH_PORT)/health"

smoke: ## smoke-тест: обогащение одного адреса
	@curl -s "http://$(HOST):$(ENRICH_PORT)/api/v1/enrich?address=%D0%A8%D0%B0%D1%80%D0%B8%D0%BA%D0%BE%D0%BF%D0%BE%D0%B4%D1%88%D0%B8%D0%BF%D0%BD%D0%B8%D0%BA%D0%BE%D0%B2%D1%81%D0%BA%D0%B0%D1%8F%2028"

flush-cache: ## очистить кэш Redis
	@$(COMPOSE) exec redis redis-cli FLUSHDB

clean: ## полная очистка: остановить и удалить контейнеры/образ
	$(COMPOSE) down --remove-orphans -v
	docker rm -f $(CONTAINER) 2>/dev/null || true
	docker rmi $(IMAGE) 2>/dev/null || true
