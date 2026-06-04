.DEFAULT_GOAL := help
PY ?= python3
VENV := .venv
BIN := $(VENV)/bin

.PHONY: help install dev test lint fmt cov run demo docker-build docker-run clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

$(BIN)/python: ## Create the virtualenv
	$(PY) -m venv $(VENV)

install: $(BIN)/python ## Install runtime + dev deps (editable)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -e ".[dev]"

dev: install ## Alias for install
test: ## Run the test suite
	$(BIN)/python -m pytest

cov: ## Run tests with coverage
	$(BIN)/python -m pytest --cov=finoptic --cov-report=term-missing

lint: ## Lint with ruff
	$(BIN)/ruff check src tests

fmt: ## Auto-format with ruff
	$(BIN)/ruff format src tests
	$(BIN)/ruff check --fix src tests

run: ## Run the API + dashboard locally (http://localhost:8000)
	$(BIN)/uvicorn finoptic.api.app:app --reload

demo: ## Scan the bundled sample data via the CLI
	$(BIN)/finoptic sample

docker-build: ## Build the container image
	docker build -t finoptic:latest .

docker-run: ## Run the container
	docker run --rm -p 8000:8000 finoptic:latest

clean: ## Remove caches and local state
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage *.db
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
