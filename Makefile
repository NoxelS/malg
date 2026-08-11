.PHONY: build check test run

# Avoid relying on a user-global uv cache, which can be unavailable in isolated
# development environments.
UV_CACHE_DIR ?= /tmp/malg-uv-cache
export UV_CACHE_DIR

check:
	uv run ruff format --check .
	uv run ruff check .

test:
	uv run pytest

build:
	docker compose -f docker/compose.yaml build malg

run: build
	docker compose -f docker/compose.yaml run --rm malg
