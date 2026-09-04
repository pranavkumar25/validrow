.PHONY: install test lint run worker migrate migration disposable docker up down

install:
	python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"

test:
	. .venv/bin/activate && pytest -q

lint:
	. .venv/bin/activate && ruff check src tests scripts

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

# Re-merge the vendored disposable-domain list with upstream. Refuses if the
# incoming list collides with a domain we treat as real mail.
disposable:
	. .venv/bin/activate && python scripts/refresh_disposable.py

up:
	docker compose up --build

down:
	docker compose down
