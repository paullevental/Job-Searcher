# OfferRadar developer commands (AGENTS.md §10).
#
# The Java service builds in a container: `gradle` is not assumed to be
# installed, and the toolchain is pinned to Java 21 regardless of the local JDK.

.DEFAULT_GOAL := help
SHELL := /bin/bash

COMPOSE := docker compose
UV := uv
NPM := npm --prefix web
GRADLE_CACHE := offerradar-gradle-cache
GRADLE_IMAGE := gradle:8.12-jdk21
GRADLE := docker run --rm \
	-v "$(CURDIR)/workflow":/app -w /app \
	-v $(GRADLE_CACHE):/home/gradle/.gradle \
	$(GRADLE_IMAGE) gradle --no-daemon

.PHONY: help env hooks up down down-v logs ps \
        test test-agents test-workflow test-web \
        lint lint-agents lint-workflow lint-web \
        fmt fmt-agents fmt-workflow fmt-web \
        check check-llm check-domains check-env wrapper install clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# --- setup -----------------------------------------------------------------

env: ## Create .env from .env.example if it does not exist
	@if [ -f .env ]; then \
		echo ".env already exists, leaving it alone"; \
	else \
		cp .env.example .env && echo "created .env -- fill in real values"; \
	fi

hooks: ## Install the gitleaks pre-commit hook
	@command -v pre-commit >/dev/null 2>&1 || { \
		echo "pre-commit is not installed. Install it with:"; \
		echo "  brew install pre-commit"; exit 1; }
	pre-commit install
	@echo "gitleaks will now run on every commit"

install: ## Install local dev dependencies for agents and web
	cd agents && $(UV) sync
	$(NPM) install

# --- compose ---------------------------------------------------------------

up: ## Start all services
	@test -f docker-compose.yml || { \
		echo "docker-compose.yml does not exist yet."; \
		echo "AGENTS.md §11 M0 lists it under 'Paul writes', so it is yours to author."; \
		echo "Service contract: postgres:5432 redis:6379 agents:8000 workflow:8080 web:5173,"; \
		echo "each service answering GET /health (liveness) and GET /ready (dependencies)."; \
		exit 1; }
	$(COMPOSE) up -d --build
	$(COMPOSE) ps

down: ## Stop all services, keep the database volume
	$(COMPOSE) down

down-v: ## Stop all services and delete the database volume
	$(COMPOSE) down -v

logs: ## Tail logs from all services
	$(COMPOSE) logs -f --tail=100

ps: ## Show service status
	$(COMPOSE) ps

# --- tests -----------------------------------------------------------------

test: test-agents test-workflow test-web ## Run every test suite

test-agents: ## pytest (offline; no network, no LLM calls)
	cd agents && $(UV) run pytest

test-workflow: ## JUnit via Gradle in Docker
	$(GRADLE) test

test-web: ## Vitest
	$(NPM) run test

# --- lint ------------------------------------------------------------------

lint: check lint-agents lint-workflow lint-web ## Lint everything, guards first

lint-agents: ## ruff + mypy
	cd agents && $(UV) run ruff check . && $(UV) run ruff format --check . && $(UV) run mypy

lint-workflow: ## Spotless check
	$(GRADLE) spotlessCheck

lint-web: ## ESLint + Prettier + tsc
	$(NPM) run lint
	$(NPM) run format:check
	$(NPM) run build

# --- formatting ------------------------------------------------------------

fmt: fmt-agents fmt-workflow fmt-web ## Format everything in place

fmt-agents:
	cd agents && $(UV) run ruff format . && $(UV) run ruff check --fix .

fmt-workflow:
	$(GRADLE) spotlessApply

fmt-web:
	$(NPM) run format

# --- repo guards (AGENTS.md §2) --------------------------------------------

check: check-llm check-domains check-env ## Run all repo rule guards

check-llm: ## Rule 8: no model SDK outside agents/.../llm/
	@python3 scripts/check_llm_chokepoint.py

check-domains: ## Rule 2: no banned job-board domains in source
	@python3 scripts/check_banned_domains.py

check-env: ## §9/§12: .env.example documents every variable code reads
	@python3 scripts/check_env_example.py

# --- misc ------------------------------------------------------------------

wrapper: ## Regenerate the Gradle wrapper (needs Docker)
	docker run --rm -v "$(CURDIR)/workflow":/app -w /app \
		-v $(GRADLE_CACHE):/home/gradle/.gradle \
		$(GRADLE_IMAGE) gradle wrapper --gradle-version 8.12 --no-daemon

clean: ## Remove build artefacts and local caches
	$(RM) -r agents/.venv agents/.pytest_cache agents/.ruff_cache agents/.mypy_cache
	$(RM) -r workflow/build workflow/.gradle
	$(RM) -r web/node_modules web/dist
	find . -name __pycache__ -type d -prune -exec $(RM) -r {} +
