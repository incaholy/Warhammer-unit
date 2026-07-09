DEFAULT_GOAL := help
.ONESHELL:

# ---- Python / tooling ----
ENV_NAME := $(shell cat .python-version 2>/dev/null)
PYTHON_VERSION ?= 3.12.7
PYENV ?= pyenv
PYTHON := $(PYENV) exec python
PIP := $(PYENV) exec pip
ALEMBIC := $(PYENV) exec alembic
PYTEST := $(PYENV) exec pytest
UVICORN := $(PYENV) exec uvicorn
PSQL ?= psql

# ---- Database URL + its parsed parts ----
# DATABASE_URL comes from the environment, or is read out of .env if unset.
DATABASE_URL ?= $(shell $(PYTHON) -c "import os; from dotenv import load_dotenv; load_dotenv(); print(os.getenv('DATABASE_URL',''))")
DB_NAME := $(shell $(PYTHON) -c "from urllib.parse import urlparse; print(urlparse('$(DATABASE_URL)').path.lstrip('/'))")
DB_USER := $(shell $(PYTHON) -c "from urllib.parse import urlparse; print(urlparse('$(DATABASE_URL)').username or '')")
DB_PASSWORD := $(shell $(PYTHON) -c "from urllib.parse import urlparse; print(urlparse('$(DATABASE_URL)').password or '')")
DB_HOST := $(shell $(PYTHON) -c "from urllib.parse import urlparse; print(urlparse('$(DATABASE_URL)').hostname or 'localhost')")
DB_PORT := $(shell $(PYTHON) -c "from urllib.parse import urlparse; print(urlparse('$(DATABASE_URL)').port or 5432)")

# Admin creds used to create the role/database. Default to the current OS user
# (typical for a Homebrew Postgres install).
DB_SUPERUSER ?= $(shell whoami)
DB_SUPERDB ?= postgres
DB_SUPERPASS ?=

# ---- App server (for `make run`) ----
APP_HOST ?= 127.0.0.1
APP_PORT ?= 8000

# ---- Docker ----
COMPOSE ?= docker compose

.PHONY: help setup install install-dev venv check-db-url db-setup migrate migrate-fresh run test create-admin docker-build docker-up docker-down docker-test

help: ## Show this help.
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "%-20s %s\n", $$1, $$2}'

setup: install db-setup migrate ## Ensure env + deps, create the DB, and run migrations.

install: venv ## Install runtime dependencies into the pyenv virtualenv.
	@$(PIP) install --upgrade pip
	@$(PIP) install -r requirements.txt

install-dev: venv ## Install dev/test dependencies into the pyenv virtualenv.
	@$(PIP) install --upgrade pip
	@$(PIP) install -r requirements-dev.txt

venv: ## Ensure the pyenv virtualenv from .python-version exists (idempotent).
	@if [ -z "$(ENV_NAME)" ]; then echo ".python-version is missing; set it to your env name (e.g., warhammer-unit-env)"; exit 1; fi
	@$(PYENV) versions --bare | grep -qx "$(ENV_NAME)" || $(PYENV) virtualenv $(PYTHON_VERSION) $(ENV_NAME)

check-db-url: ## Fail fast if DATABASE_URL is not set.
	@if [ -z "$(strip $(DATABASE_URL))" ]; then \
		echo "DATABASE_URL is not set (export it or add it to .env)"; \
		exit 1; \
	fi

db-setup: check-db-url ## Create the DB role and database if missing; safe to re-run.
	@PGPASSWORD="$(DB_SUPERPASS)" \
	$(PSQL) -v ON_ERROR_STOP=1 \
		-v db_user='$(DB_USER)' \
		-v db_password='$(DB_PASSWORD)' \
		-v db_name='$(DB_NAME)' \
		"postgresql://$(DB_SUPERUSER)@$(DB_HOST):$(DB_PORT)/$(DB_SUPERDB)" \
		-f app/core/db/scripts/db_setup.sql

migrate: check-db-url ## Apply Alembic migrations up to head.
	@$(ALEMBIC) upgrade head

migrate-fresh: check-db-url ## Drop, recreate, and re-migrate the DB (destructive — wipes all data).
	@echo "==> Dropping database $(DB_NAME)"
	@PGPASSWORD="$(DB_SUPERPASS)" \
	$(PSQL) -v ON_ERROR_STOP=1 \
		"postgresql://$(DB_SUPERUSER)@$(DB_HOST):$(DB_PORT)/$(DB_SUPERDB)" \
		-c "DROP DATABASE IF EXISTS $(DB_NAME);"
	@$(MAKE) db-setup
	@$(MAKE) migrate

run: ## Run the FastAPI app locally with auto-reload.
	@$(UVICORN) app.main:app --reload --host $(APP_HOST) --port $(APP_PORT)

test: ## Run the test suite.
	@$(PYTEST) tests/ -v

create-admin: check-db-url ## Promote a user to admin: make create-admin USERNAME=<name>.
	@if [ -z "$(USERNAME)" ]; then echo "usage: make create-admin USERNAME=<name>"; exit 1; fi
	@$(PYTHON) -m scripts.make_admin $(USERNAME)

docker-build: ## Build the API Docker image.
	@$(COMPOSE) build

docker-up: ## Start the app + Postgres with docker compose (migrations run on start).
	@$(COMPOSE) up --build

docker-down: ## Stop and remove the docker compose stack (keeps the DB volume).
	@$(COMPOSE) down

docker-test: ## Run the suite in a container against a throwaway Postgres.
	@$(COMPOSE) -f docker-compose.yml -f docker-compose.test.yml up --build \
		--abort-on-container-exit --exit-code-from api
