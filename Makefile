# SoftwareAsAGraph Root Makefile
# Provides streamlined targets for testing, linting, and pre-merge validation.

PYTHON ?= python3
PYTEST ?= pytest

.PHONY: help test test-fast test-all test-integration lint check

help:
	@echo "Available commands:"
	@echo "  make test          Run fast unit tests (skips slow simulations & integration)"
	@echo "  make test-fast     Alias for 'make test'"
	@echo "  make test-all      Run full test suite (excluding live Neo4j integration)"
	@echo "  make test-integration Run live Neo4j integration tests"
	@echo "  make lint          Run ruff linter across the repository"
	@echo "  make check         Pre-merge validation: runs lint and fast unit tests"

test: test-fast

test-fast:
	$(PYTEST) -v --tb=short --quick -m "not integration"

test-all:
	$(PYTEST) -v --tb=short -m "not integration"

test-integration:
	$(PYTEST) -v --tb=short -m "integration"

lint:
	@if command -v ruff >/dev/null 2>&1; then \
		ruff check saag/ cli/ api/ reproduce/ scripts/ tests/; \
	else \
		echo "ruff not found in PATH; skipping ruff linting (install via 'pip install ruff')"; \
	fi

check: lint test-fast
	@echo "✓ Pre-merge check passed."
