.PHONY: install test lint fmt

install:
	pip install -e ".[dev]"

test:
	python -m pytest -q tests

lint:
	ruff check src tests

fmt:
	ruff format src tests
