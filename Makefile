SHELL := /bin/bash
.DEFAULT_GOAL := help

PYTHON   ?= python3
VENV     ?= .venv
BIN      := $(VENV)/bin
CLAB_BIN ?= containerlab
# Set CLAB_SUDO=1 when containerlab must run under sudo (e.g. GitHub Actions).
CLAB     := $(if $(filter 1,$(CLAB_SUDO)),sudo -E ,)$(CLAB_BIN)
PYTEST_ARGS ?=

.PHONY: help setup test test-unit test-integration lint format clean distclean \
        docker-test baseline-check baseline-update trends demo

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  %-18s %s\n", $$1, $$2}'

$(BIN)/activate: pyproject.toml
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install -q --upgrade pip
	$(BIN)/pip install -q -e '.[dev]'
	@touch $(BIN)/activate

setup: $(BIN)/activate ## Create venv and install harness + dev deps
	@$(BIN)/python --version

test: setup ## Run the FULL suite (unit + integration) and check convergence regressions
	mkdir -p results
	$(BIN)/pytest $(PYTEST_ARGS) --junitxml=results/junit.xml
	$(BIN)/python scripts/compare_baseline.py --results results --baseline baseline/convergence_baseline.json --report results/regression_report.md

test-unit: setup ## Unit tests only (no Docker needed)
	$(BIN)/pytest -m unit $(PYTEST_ARGS)

test-integration: setup ## Integration tests only (containerlab required)
	mkdir -p results
	$(BIN)/pytest -m integration $(PYTEST_ARGS)

lint: setup ## ruff lint + format check + mypy --strict
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .
	$(BIN)/mypy

format: setup ## Auto-format with ruff
	$(BIN)/ruff format .
	$(BIN)/ruff check --fix .

baseline-check: ## Compare results/ against the stored baseline
	$(BIN)/python scripts/compare_baseline.py --results results --baseline baseline/convergence_baseline.json --report results/regression_report.md

baseline-update: ## Overwrite the stored baseline with the latest results/
	$(BIN)/python scripts/compare_baseline.py --results results --baseline baseline/convergence_baseline.json --update

trends: setup ## Plot convergence times across CI runs -> results/trends/index.html (needs gh)
	$(BIN)/python scripts/collect_trends.py

demo: setup ## Narrated ~2 min live demo: break an OSPF network, watch it heal
	$(BIN)/python scripts/demo.py

docker-test: ## Run the full suite inside the runner container (Docker Desktop hosts)
	bash scripts/run_in_docker.sh

clean: ## Destroy leftover labs, remove results and caches
	-@for t in topologies/*.clab.yml; do $(CLAB) destroy -t "$$t" --cleanup >/dev/null 2>&1 || true; done
	rm -rf results .pytest_cache .mypy_cache .ruff_cache topologies/clab-* *.egg-info
	find . -name __pycache__ -type d -prune -not -path './$(VENV)/*' -exec rm -rf {} +

distclean: clean ## clean + remove the venv
	rm -rf $(VENV)
