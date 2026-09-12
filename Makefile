.DEFAULT_GOAL := help

.PHONY: help format format-check lint typecheck test test-cov check container-build api-up frontend-up up workers restart-tools

# Avoid relying on a user-global uv cache, which can be unavailable in isolated
# development environments.
UV_CACHE_DIR ?= /tmp/malg-uv-cache
export UV_CACHE_DIR

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
	docker compose -f docker/compose.yaml build api worker frontend

api-up:
	docker compose -f docker/compose.yaml up --build -d postgres api

frontend-up:
	docker compose -f docker/compose.yaml up --build -d frontend


up:
	docker compose -f docker/compose.yaml up --build -d --scale worker=3 postgres lightpanda searxng trace-viewer trace-proxy api frontend worker

workers:
	docker compose -f docker/compose.yaml up --build -d --force-recreate --no-deps --scale worker=3 worker

restart-tools:
	docker compose -f docker/compose.yaml up -d --build --force-recreate lightpanda searxng trace-viewer trace-proxy
