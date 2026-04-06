.PHONY: help format lint typecheck test check ci run setup clean

help:
	@echo "Available commands:"
	@echo "  make setup      - Install dependencies using uv"
	@echo "  make format     - Format code using ruff"
	@echo "  make lint       - Lint code using ruff"
	@echo "  make typecheck  - Run type checking using mypy"
	@echo "  make test       - Run tests using pytest"
	@echo "  make check      - Run format (write), lint --fix, typecheck, and test"
	@echo "  make ci         - Like GitHub Actions: format --check, lint, typecheck, test (no writes)"
	@echo "  make run        - Start the FastAPI server (sensor_app); PORT=8001 to change port"
	@echo "  make clean      - Remove __pycache__ and build artifacts"

setup:
	uv sync --all-groups

format:
	uv run ruff format .
	uv run ruff check --fix .

lint:
	uv run ruff check .

typecheck:
	uv run mypy src/sensor_app tests

test:
	uv run pytest

check: format lint typecheck test

ci:
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy src/sensor_app tests
	uv run pytest

PORT ?= 8000

run:
	uv run uvicorn sensor_app.api.main:app --reload --app-dir src --port $(PORT)

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache .mypy_cache
