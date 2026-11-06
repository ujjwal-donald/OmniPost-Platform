.PHONY: install up down run-api run-worker run-beat migrate makemigration seed test

install:
	pip install -r requirements.txt

up:
	docker compose up -d postgres redis minio

down:
	docker compose down

run-api:
	uvicorn app.main:app --reload --port 8000

run-worker:
	celery -A app.workers.celery_app worker --loglevel=info --concurrency=2

run-beat:
	celery -A app.workers.celery_app beat --loglevel=info

makemigration:
	alembic revision --autogenerate -m "$(m)"

migrate:
	alembic upgrade head

seed:
	python -m seeds.seed

test:
	pytest -q
