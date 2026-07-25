.PHONY: install test lint run ui docker up down

install:
	python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev,frontend]"

test:
	. .venv/bin/activate && pytest -q

lint:
	. .venv/bin/activate && ruff check src tests

run:
	. .venv/bin/activate && uvicorn eve.api.main:app --reload --port 8000

ui:
	. .venv/bin/activate && streamlit run frontend/app.py

up:
	docker compose up --build

down:
	docker compose down
