.PHONY: build check test run restart-tools

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
	docker compose -f docker/compose.yaml run --rm --no-deps malg

restart-tools:
	docker compose -f docker/compose.yaml up -d --build --force-recreate lightpanda trace-viewer trace-proxy
