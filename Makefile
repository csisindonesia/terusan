# Terusan — research data warehouse and data portal.
# Requires: go 1.26+, uv, node 22+ (corepack), psql.

SHELL := /bin/bash
PNPM  := corepack pnpm
DB    ?= terusan
DATABASE_URL ?= postgres://localhost:5432/$(DB)

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ---- setup ----------------------------------------------------------------

.PHONY: setup
setup: setup-node setup-python storage-init ## Install every toolchain and create local storage
	@echo "setup complete; copy .env.example to .env if you have not already"

.PHONY: setup-node
setup-node: ## Install workspace JS dependencies
	$(PNPM) install

.PHONY: setup-python
setup-python: ## Install pipeline dependencies
	cd pipelines && uv sync --group dev

.PHONY: storage-init
storage-init: ## Create raw/ bronze/ silver/ gold/ exports/ temporary/ under STORAGE_ROOT
	cd pipelines && uv run terusan storage init

.PHONY: storage-info
storage-info: ## Show which backend STORAGE_ROOT currently points at
	cd pipelines && uv run terusan storage info

# ---- database -------------------------------------------------------------

.PHONY: db-create
db-create: ## Create the local application database
	createdb $(DB) || echo "database $(DB) already exists"

.PHONY: db-migrate
db-migrate: ## Apply every up migration in order
	@for f in db/migrations/*.up.sql; do \
	  echo "applying $$f"; \
	  psql -q -v ON_ERROR_STOP=1 -d "$(DATABASE_URL)" -f "$$f" || exit 1; \
	done

.PHONY: db-rollback
db-rollback: ## Revert every migration, newest first
	@for f in $$(ls -r db/migrations/*.down.sql); do \
	  echo "reverting $$f"; \
	  psql -q -v ON_ERROR_STOP=1 -d "$(DATABASE_URL)" -f "$$f" || exit 1; \
	done

.PHONY: db-reset
db-reset: db-rollback db-migrate ## Rebuild the schema from scratch

# ---- development ----------------------------------------------------------

.PHONY: dev-portal
dev-portal: ## Run the data portal on :3000
	$(PNPM) --filter @terusan/portal dev

.PHONY: dev-api
dev-api: ## Run the serving layer on :8080
	cd services/api && go run ./cmd/api

# ---- pipeline -------------------------------------------------------------

.PHONY: catalog-sync
catalog-sync: ## Push the source registry into PostgreSQL
	cd pipelines && uv run terusan catalog sync

.PHONY: runs
runs: ## Show recent pipeline runs
	cd pipelines && uv run terusan catalog runs

.PHONY: ingest
ingest: ## Run every scheduled source into RAW
	cd pipelines && uv run terusan sources run

.PHONY: extract
extract: ## Extract RAW into Bronze
	cd pipelines && uv run terusan warehouse extract

.PHONY: silver
silver: ## Normalize Bronze into Silver (see `terusan silver normalize --help`)
	@echo "Silver needs a column mapping per indicator; run:"
	@echo "  cd pipelines && uv run terusan silver normalize --help"

.PHONY: compact
compact: ## Merge small Parquet files across the analytical layers
	cd pipelines && uv run terusan warehouse compact bronze
	cd pipelines && uv run terusan warehouse compact silver
	cd pipelines && uv run terusan warehouse compact gold

# ---- quality --------------------------------------------------------------

.PHONY: test
test: test-go test-python ## Run every test suite

.PHONY: test-go
test-go: ## Run Go tests
	cd services/api && go test ./...

.PHONY: test-python
test-python: ## Run pipeline tests
	cd pipelines && uv run pytest -q

.PHONY: lint
lint: ## Run every linter
	cd services/api && gofmt -l . && go vet ./...
	cd pipelines && uv run ruff check . && uv run ruff format --check .
	$(PNPM) -r typecheck

.PHONY: fmt
fmt: ## Format every language
	cd services/api && gofmt -w .
	cd pipelines && uv run ruff format . && uv run ruff check --fix .

.PHONY: build
build: ## Build every deployable artifact
	cd services/api && go build -o bin/api ./cmd/api
	$(PNPM) -r build

.PHONY: clean
clean: ## Remove build output and scratch (never touches the data lake)
	rm -rf services/api/bin apps/portal/dist .cache/*
