# AgentWatch — monorepo convenience targets.
# The Python package, the dashboard UI, and the landing page each have their
# own formatter (Ruff / Prettier). This Makefile ties them together so a
# contributor can run `make format` from the repo root and have every
# sub-package format itself in one shot.

.PHONY: format format-check format-py format-js format-landing format-frontend typecheck help

help:
	@echo "Available targets:"
	@echo "  make format       - Format Python, dashboard UI, and landing page"
	@echo "  make format-check - Verify formatting without modifying files"
	@echo "  make format-py    - ruff format (Python)"
	@echo "  make format-frontend - Prettier (frontend/)"
	@echo "  make format-landing  - Prettier (agentwatch-landing/)"
	@echo "  make typecheck    - mypy over the v3 modules"

format-py:
	ruff format panoptis/

format-frontend:
	cd frontend && npm run format

format-landing:
	cd agentwatch-landing && npm run format

format: format-py format-frontend format-landing

format-check: format-check-py format-check-frontend format-check-landing

format-check-py:
	ruff format --check panoptis/

format-check-frontend:
	cd frontend && npm run format:check

format-check-landing:
	cd agentwatch-landing && npm run format:check

# v3 modules are type-checked in CI; the v0.2 packages are not (yet).
V3_MODULES = panoptis/evidence panoptis/storage panoptis/sensors panoptis/events 	panoptis/runtime panoptis/graph panoptis/provenance panoptis/compare 	panoptis/behaviour panoptis/causality panoptis/lab panoptis/state 	panoptis/forecasting panoptis/analysis panoptis/query panoptis/runs 	panoptis/entities panoptis/api/v3.py panoptis/cli/v3.py panoptis/instrument.py

typecheck:
	mypy $(V3_MODULES)
