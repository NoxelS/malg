.DEFAULT_GOAL := help

.PHONY: help format format-check lint typecheck test test-cov check container-build api-up frontend-up up workers restart-tools

# Avoid relying on a user-global uv cache, which can be unavailable in isolated
# development environments.
UV_CACHE_DIR ?= /tmp/malg-uv-cache
export UV_CACHE_DIR
COMPOSE := docker compose --env-file .env -f docker/compose.yaml

help:
	@printf '%s\n' 'Targets: format, format-check, lint, typecheck, test, test-cov, check, container-build, api-up, frontend-up, up, workers, restart-tools'

format:
	uv run ruff format malg tests

format-check:
	uv run ruff format --check malg tests

lint:
	uv run ruff check malg tests

typecheck:
	uv run mypy

test:
	uv run pytest

test-cov:
	uv run pytest --cov=malg --cov-report=term-missing

check: format-check lint typecheck test-cov

container-build:
	$(COMPOSE) build api worker frontend

api-up:
	$(COMPOSE) up --build -d postgres api

frontend-up:
	$(COMPOSE) up --build -d frontend


up:
	$(COMPOSE) up --build -d --scale worker=3 postgres lightpanda searxng trace-viewer trace-proxy api frontend worker

workers:
	$(COMPOSE) up --build -d --force-recreate --no-deps --scale worker=3 worker

restart-tools:
	$(COMPOSE) up -d --build --force-recreate lightpanda searxng trace-viewer trace-proxy
