.PHONY: install test lint run worker migrate migration docker up down

install:
	python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"

test:
	. .venv/bin/activate && pytest -q

lint:
	. .venv/bin/activate && ruff check src tests

# Serves both the JSON API and the Validrow web app on one port.
run:
	. .venv/bin/activate && uvicorn eve.api.main:app --reload --port 8000

# Bulk-job worker. Needs EVE_REDIS_URL; without it the API runs jobs itself.
worker:
	. .venv/bin/activate && arq eve.jobs.worker.WorkerSettings

# The app migrates to head on startup, so this is only for running migrations
# separately — e.g. as a deploy step before the new version boots.
migrate:
	. .venv/bin/activate && alembic upgrade head

# make migration m="add credits table"
migration:
	. .venv/bin/activate && alembic revision -m "$(m)"

up:
	docker compose up --build

down:
	docker compose down
