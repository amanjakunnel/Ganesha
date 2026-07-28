PYTHON ?= python3.12
VENV ?= .venv

.PHONY: setup migrate init-db compose-up compose-down test lint typecheck format run seed-demo sheets-init sheets-sync check

setup:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/python -m pip install --upgrade pip
	$(VENV)/bin/python -m pip install -e ".[dev]"

compose-up:
	docker-compose up -d

compose-down:
	docker-compose down

migrate:
	$(VENV)/bin/python -m alembic upgrade head

init-db: compose-up migrate

test:
	$(VENV)/bin/python -m pytest -q

lint:
	$(VENV)/bin/python -m ruff check .

typecheck:
	$(VENV)/bin/python -m mypy apps packages tests

format:
	$(VENV)/bin/python -m ruff format .

run:
	$(VENV)/bin/python -m uvicorn apps.api.main:app --reload

seed-demo:
	$(VENV)/bin/python -m apps.cli.main workflow demo

workflow-dashboard:
	$(VENV)/bin/python -m apps.cli.main workflow dashboard

sheets-init:
	$(VENV)/bin/python -m apps.cli.main

sheets-sync:
	$(VENV)/bin/python -m apps.cli.main

db-doctor:
	$(VENV)/bin/python -m apps.cli.main db-doctor

db-reset-local:
	$(VENV)/bin/python -m apps.cli.main db-reset-local --yes

check:
	$(VENV)/bin/python -c "import tomllib; tomllib.load(open('pyproject.toml','rb')); print('valid TOML')"
	$(VENV)/bin/python --version

