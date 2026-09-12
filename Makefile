.PHONY: help up down reset logs migrate revision dev test lint fmt typecheck psql inspect load load-report

COMPOSE ?= docker compose
PYTEST  ?= $(shell test -x .venv/bin/pytest && echo .venv/bin/pytest || echo pytest)

# So `make up` can print the URL the ports were actually published on. Compose
# reads .env itself; this is only for the echo below.
-include .env
API_PORT ?= 8001

help:
	@grep -hE '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

up: ## Start api + postgres
	$(COMPOSE) up -d --build
	@echo "Swagger: http://localhost:$(API_PORT)/docs"
	@echo "Health:  http://localhost:$(API_PORT)/health"

down: ## Stop everything
	$(COMPOSE) down

reset: ## Stop and destroy the database volume
	$(COMPOSE) down -v

logs: ## Tail api logs
	$(COMPOSE) logs -f api

# `docker compose restart` reuses the built image and will NOT pick up source
# edits — use `make up`, which passes --build.
migrate: ## Apply migrations
	$(COMPOSE) exec api alembic upgrade head

revision: ## Autogenerate a migration: make revision m="add employers table"
	$(COMPOSE) exec api alembic revision --autogenerate -m "$(m)"

inspect: ## Report the header shape of every file in data/raw/
	$(COMPOSE) exec api python -m scripts.inspect_headers

dev: ## Install the dev toolchain into .venv (uv)
	uv venv --python 3.12
	uv pip install -e ".[dev]"

test: ## Full suite — needs `make up` for postgres
	$(PYTEST) -q

lint: ## ruff
	.venv/bin/ruff check . && .venv/bin/ruff format --check .

fmt: ## ruff format
	.venv/bin/ruff check --fix . && .venv/bin/ruff format .

typecheck: ## mypy
	.venv/bin/mypy app ingest

psql: ## psql into the database
	$(COMPOSE) exec db psql -U tfwp -d tfwp

load: ## Ingest everything in data/raw/ (idempotent)
	$(COMPOSE) exec api python -m scripts.load

load-report: ## Summarise what is currently loaded
	$(COMPOSE) exec api python -m scripts.load --report
