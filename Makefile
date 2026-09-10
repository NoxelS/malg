.DEFAULT_GOAL := help

.PHONY: help format format-check lint typecheck test test-cov check container-build memory-init run restart-tools

# Avoid relying on a user-global uv cache, which can be unavailable in isolated
# development environments.
UV_CACHE_DIR ?= /tmp/malg-uv-cache
export UV_CACHE_DIR

help:
	@printf '%s\n' 'Targets: format, format-check, lint, typecheck, test, test-cov, check, container-build, memory-init, run, restart-tools'

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
	docker compose -f docker/compose.yaml build malg

memory-init:
	docker compose -f docker/compose.yaml run --rm --no-deps memory-init

run: container-build memory-init
	docker compose -f docker/compose.yaml run --rm --no-deps malg

restart-tools:
	docker compose -f docker/compose.yaml up -d --build --force-recreate lightpanda searxng trace-viewer trace-proxy
