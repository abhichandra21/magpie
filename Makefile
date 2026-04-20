.PHONY: test lint format run install

install:
	uv sync

test:
	uv run pytest

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests

format:
	uv run ruff format src tests
	uv run ruff check --fix src tests

run:
	uv run magpie
