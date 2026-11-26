# OfferRadar developer commands (AGENTS.md §10).
#
# Docker is a dependency provider and a verification target, not the build
# environment: Gradle runs on the host via ./gradlew, whose daemon JVM is pinned
# to Java 21 by workflow/gradle/gradle-daemon-jvm.properties.

.DEFAULT_GOAL := help
SHELL := /bin/bash

COMPOSE := docker compose
UV := uv
NPM := npm --prefix web
GRADLE := cd workflow && ./gradlew
# "$$@" is load-bearing: without it `$(PSQL) -c "..."` passes -c to sh as a
# positional parameter, psql runs with no command, and the target silently
# succeeds having done nothing. $0 is set to `sh` so "$$@" starts at the -c.
PSQL := $(COMPOSE) exec -T postgres sh -c 'exec psql -v ON_ERROR_STOP=1 -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" "$$@"' sh

# Relative to agents/; pass an absolute path for a resume outside the repo.
RESUME ?= ../resume.md

.PHONY: help env hooks up down down-v logs ps \
        migrate migrate-info seed seed-volume unseed-volume explain db-shell \
        ollama-pull embed-resume record-dedup-vectors score-eval cost-report \
        rank shortlist select score sample-score-eval score-eval-run \
        test test-agents test-agents-offline test-workflow test-web \
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

# --- database --------------------------------------------------------------

migrate: ## Start workflow, which runs Flyway on boot (no separate Flyway CLI); print history
	$(COMPOSE) up -d --build workflow
	@$(MAKE) migrate-info

migrate-info: ## List applied Flyway migrations: version, description, success, installed_on
	@$(PSQL) -c "SELECT version, description, success, installed_on FROM flyway_schema_history ORDER BY installed_rank;"

seed: ## Load scripts/seed/dev_seed.sql: a few real companies and their ATS boards
	$(PSQL) < scripts/seed/dev_seed.sql

seed-volume: seed ## Load ~50k synthetic postings for the staleness-index demonstration
	$(PSQL) < scripts/seed/explain_fixture.sql

unseed-volume: ## Remove the synthetic fixture postings that seed-volume loaded
	$(PSQL) -c "DELETE FROM postings WHERE dedup_key LIKE 'fixture-%';"

explain: ## Show EXPLAIN ANALYZE for the staleness query, indexed vs. forced sequential scan
	$(PSQL) < scripts/seed/explain_queries.sql

db-shell: ## Open an interactive psql shell in the postgres container
	$(COMPOSE) exec -it postgres sh -c 'exec psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

ollama-pull: ## Pull EMBEDDING_MODEL (from .env) into the host's native Ollama
	@command -v ollama >/dev/null 2>&1 || { echo "Install Ollama: https://ollama.com/download"; exit 1; }
	set -a && . ./.env && set +a && ollama pull "$$EMBEDDING_MODEL"

embed-resume: ## Chunk and embed resume.md once per version (needs postgres up + native Ollama)
	set -a && . ./.env && set +a && cd agents && \
		DATABASE_URL="postgresql+asyncpg://$$POSTGRES_USER:$$POSTGRES_PASSWORD@localhost:$$POSTGRES_PORT/$$POSTGRES_DB" \
		OLLAMA_URL=http://localhost:$$OLLAMA_PORT \
		$(UV) run python -m offerradar_agents.matcher embed-resume --resume $(RESUME)

record-dedup-vectors: ## Embed dedup_pairs.json titles via local Ollama into a test fixture
	set -a && . ./.env && set +a && cd agents && OLLAMA_URL=http://localhost:$$OLLAMA_PORT \
		$(UV) run python -m offerradar_agents.dedup.record --out tests/fixtures/dedup_pair_vectors.json

rank: ## Stage 1: score every open posting against the embedded resume into matches
	set -a && . ./.env && set +a && cd agents && DATABASE_URL="postgresql+asyncpg://$$POSTGRES_USER:$$POSTGRES_PASSWORD@localhost:$$POSTGRES_PORT/$$POSTGRES_DB" \
		$(UV) run python -m offerradar_agents.matcher rank --resume $(RESUME)

shortlist: ## Gate 1: print the top GATE1_SHORTLIST_SIZE postings that pass the prefilter
	set -a && . ./.env && set +a && cd agents && DATABASE_URL="postgresql+asyncpg://$$POSTGRES_USER:$$POSTGRES_PASSWORD@localhost:$$POSTGRES_PORT/$$POSTGRES_DB" \
		$(UV) run python -m offerradar_agents.matcher shortlist

select: ## Gate 1: mark postings for stage 2: make select IDS="12 34"
	set -a && . ./.env && set +a && cd agents && DATABASE_URL="postgresql+asyncpg://$$POSTGRES_USER:$$POSTGRES_PASSWORD@localhost:$$POSTGRES_PORT/$$POSTGRES_DB" \
		$(UV) run python -m offerradar_agents.matcher select $(IDS)

score: ## Stage 2: score Gate-1-selected postings (spends money when LLM_PROVIDER=anthropic)
	set -a && . ./.env && set +a && cd agents && DATABASE_URL="postgresql+asyncpg://$$POSTGRES_USER:$$POSTGRES_PASSWORD@localhost:$$POSTGRES_PORT/$$POSTGRES_DB" OLLAMA_URL=http://localhost:$$OLLAMA_PORT \
		$(UV) run python -m offerradar_agents.matcher score --resume $(RESUME)

sample-score-eval: ## Write 30 postings to rate 1-5: make sample-score-eval OUT=<file>
	set -a && . ./.env && set +a && cd agents && DATABASE_URL="postgresql+asyncpg://$$POSTGRES_USER:$$POSTGRES_PASSWORD@localhost:$$POSTGRES_PORT/$$POSTGRES_DB" \
		$(UV) run python -m offerradar_agents.matcher sample-score-eval --out $(OUT)

score-eval-run: ## Score rated postings: make score-eval-run RATINGS=<file> OUT=<file> [PROMPT=score_v2] [PROVIDER=ollama]
	set -a && . ./.env && set +a && cd agents && DATABASE_URL="postgresql+asyncpg://$$POSTGRES_USER:$$POSTGRES_PASSWORD@localhost:$$POSTGRES_PORT/$$POSTGRES_DB" OLLAMA_URL=http://localhost:$$OLLAMA_PORT \
		$(UV) run python -m offerradar_agents.matcher score-eval-run --resume $(RESUME) \
		--ratings $(RATINGS) --out $(OUT) $(if $(PROMPT),--prompt-version $(PROMPT)) $(if $(PROVIDER),--provider $(PROVIDER))

score-eval: ## Stage-2 eval: make score-eval RATINGS=<ratings.json> RUN=<run.json>
	cd agents && $(UV) run python -m offerradar_agents.matcher.score_eval --ratings $(RATINGS) --run $(RUN)

cost-report: ## Funnel vs all-postings cost for one run: make cost-report RUN_ID=<n> (needs postgres up)
	set -a && . ./.env && set +a && cd agents && \
		DATABASE_URL="postgresql+asyncpg://$$POSTGRES_USER:$$POSTGRES_PASSWORD@localhost:$$POSTGRES_PORT/$$POSTGRES_DB" \
		$(UV) run python -m offerradar_agents.matcher.cost_report --run-id $(RUN_ID)

# --- tests -----------------------------------------------------------------

test: test-agents test-workflow test-web ## Run every test suite

test-agents: ## pytest, including container-backed tests (needs Docker)
	cd agents && $(UV) run pytest

test-agents-offline: ## pytest without the Postgres container (no Docker needed)
	cd agents && $(UV) run pytest -m "not integration"

test-workflow: ## JUnit, including Testcontainers schema tests
# Testcontainers needs the Docker daemon, but not Compose: one Postgres, no stack.
	@docker info >/dev/null 2>&1 || { \
		echo "The Docker daemon is not running; Testcontainers cannot start Postgres."; \
		echo "Start Docker Desktop, then rerun. Do not run 'make up' as well -- the"; \
		echo "five-service stack and a test container together is what exhausts memory."; \
		exit 1; }
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

check: check-llm check-domains check-env check-offline ## Run all repo rule guards

check-llm: ## Rule 8: no model SDK outside agents/.../llm/
	@python3 scripts/check_llm_chokepoint.py

check-domains: ## Rule 2: no banned job-board domains in source
	@python3 scripts/check_banned_domains.py

check-offline: ## Rule 5: no live network in tests
	@python3 scripts/check_offline_tests.py

check-env: ## §9/§12: .env.example documents every variable code reads
	@python3 scripts/check_env_example.py

# --- misc ------------------------------------------------------------------

wrapper: ## Regenerate the Gradle wrapper
	$(GRADLE) wrapper --gradle-version 8.12

clean: ## Remove build artefacts and local caches
	$(RM) -r agents/.venv agents/.pytest_cache agents/.ruff_cache agents/.mypy_cache
	$(RM) -r workflow/build workflow/.gradle
	$(RM) -r web/node_modules web/dist
	find . -name __pycache__ -type d -prune -exec $(RM) -r {} +
