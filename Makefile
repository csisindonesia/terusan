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

.PHONY: auth-user
auth-user: ## Create or reset a portal account (EMAIL=you@example.org). Stop the API first.
	@test -n "$(EMAIL)" || (echo "usage: make auth-user EMAIL=you@example.org" >&2; exit 1)
	cd services/api && go run ./cmd/authctl create -email "$(EMAIL)" -name "$(NAME)" -role "$(ROLE)"

.PHONY: auth-users
auth-users: ## List the portal accounts
	cd services/api && go run ./cmd/authctl list

.PHONY: dev-api
dev-api: ## Run the serving layer on :8080
	cd services/api && go run ./cmd/api

.PHONY: dev
dev: ## Run the API and the portal together, both reloading on change (Ctrl-C stops both)
	@./scripts/dev.sh

.PHONY: tunnel
tunnel: ## Run the stack and publish it on a public https URL (needs cloudflared)
	@./scripts/tunnel.sh

.PHONY: serve
serve: ## Build and run the stack the way the public hostname expects it
	@./scripts/serve.sh

.PHONY: tunnel-install
tunnel-install: ## One-time: put the portal on terusan.csis.or.id via a named tunnel
	@./scripts/tunnel-install.sh

TUNNEL_AGENT       := com.terusan.tunnel
TUNNEL_AGENT_PLIST := $(HOME)/Library/LaunchAgents/$(TUNNEL_AGENT).plist

.PHONY: tunnel-service
tunnel-service: ## Keep the Cloudflare connector running across reboots (launchd)
	@mkdir -p $(HOME)/Library/LaunchAgents .cache/logs
	@sed -e 's#__CLOUDFLARED__#$(shell command -v cloudflared)#g' \
	  -e 's#__CONFIG__#$(HOME)/.cloudflared/config.yml#g' \
	  -e 's#__PROJECT_ROOT__#$(CURDIR)#g' \
	  infra/local/$(TUNNEL_AGENT).plist > $(TUNNEL_AGENT_PLIST)
	@launchctl bootout gui/$$(id -u)/$(TUNNEL_AGENT) 2>/dev/null || true
	@launchctl bootstrap gui/$$(id -u) $(TUNNEL_AGENT_PLIST)
	@echo "installed $(TUNNEL_AGENT_PLIST)"

.PHONY: tunnel-service-remove
tunnel-service-remove: ## Stop the connector and remove its agent
	@launchctl bootout gui/$$(id -u)/$(TUNNEL_AGENT) 2>/dev/null || true
	@rm -f $(TUNNEL_AGENT_PLIST)
	@echo "removed $(TUNNEL_AGENT)"

SERVE_AGENT       := com.terusan.serve
SERVE_AGENT_PLIST := $(HOME)/Library/LaunchAgents/$(SERVE_AGENT).plist
PUBLIC_URL        ?= https://terusan.csis.or.id
PUBLIC_HOST       := $(patsubst https://%,%,$(PUBLIC_URL))

.PHONY: serve-install
serve-install: ## Keep the published stack running across reboots (launchd)
	@mkdir -p $(HOME)/Library/LaunchAgents .cache/logs
	@sed -e 's#__PROJECT_ROOT__#$(CURDIR)#g' -e 's#__PATH__#$(PATH)#g' \
	  -e 's#__PUBLIC_URL__#$(PUBLIC_URL)#g' \
	  infra/local/$(SERVE_AGENT).plist > $(SERVE_AGENT_PLIST)
	@launchctl bootout gui/$$(id -u)/$(SERVE_AGENT) 2>/dev/null || true
	@launchctl bootstrap gui/$$(id -u) $(SERVE_AGENT_PLIST)
	@echo "installed $(SERVE_AGENT_PLIST); serving $(PUBLIC_URL)"

.PHONY: serve-uninstall
serve-uninstall: ## Stop the published stack and remove its agent
	@launchctl bootout gui/$$(id -u)/$(SERVE_AGENT) 2>/dev/null || true
	@rm -f $(SERVE_AGENT_PLIST)
	@echo "removed $(SERVE_AGENT)"

.PHONY: serve-status
serve-status: ## Show whether the published stack and its tunnel are up
	@launchctl print gui/$$(id -u)/$(SERVE_AGENT) 2>/dev/null \
	  | grep -E 'state|last exit code' || echo "$(SERVE_AGENT) is not loaded"
	@cloudflared tunnel info terusan 2>/dev/null | head -8 || true
	@# Resolved against 1.1.1.1 rather than this machine's resolver, which
	@# holds the record this hostname had before the tunnel took it and would
	@# report the old server's health as ours until that entry expires.
	@ip=$$(dig +short @1.1.1.1 $(PUBLIC_HOST) | head -1); \
	  curl -s -o /dev/null --resolve $(PUBLIC_HOST):443:$$ip \
	    -w "$(PUBLIC_URL)/healthz -> %{http_code}\n" $(PUBLIC_URL)/healthz || true

.PHONY: restart
restart: ## Restart the published stack (rebuilds first; loads the agent if it is not)
	@if launchctl print gui/$$(id -u)/$(SERVE_AGENT) >/dev/null 2>&1; then \
	  launchctl kickstart -k gui/$$(id -u)/$(SERVE_AGENT); \
	  echo "restarted $(SERVE_AGENT); it rebuilds before it listens"; \
	else \
	  $(MAKE) serve-install; \
	fi

.PHONY: start
start: ## Start the published stack, and keep it running across reboots
	@$(MAKE) serve-install

.PHONY: stop
stop: ## Stop the published stack, freeing :8080 and :3000 for `make dev`
	@launchctl bootout gui/$$(id -u)/$(SERVE_AGENT) 2>/dev/null \
	  && echo "stopped $(SERVE_AGENT)" \
	  || echo "$(SERVE_AGENT) was not running"

.PHONY: logs
logs: ## Follow the published stack's log (Ctrl-C stops following, not the stack)
	@mkdir -p .cache/logs && touch .cache/logs/serve.err.log .cache/logs/serve.out.log
	@tail -n 50 -f .cache/logs/serve.err.log .cache/logs/serve.out.log

# ---- docker ---------------------------------------------------------------

COMPOSE := docker compose

.PHONY: docker-build
docker-build: ## Build the API, portal and pipelines images
	$(COMPOSE) --profile tools build

.PHONY: docker-up
docker-up: ## Run the portal and the API against the shared lake (DATA_DIR in .env)
	$(COMPOSE) up -d --build
	@echo "portal http://localhost:$${PORTAL_PORT:-3000}  api http://localhost:$${API_PORT:-8080}"

.PHONY: docker-down
docker-down: ## Stop the stack, keeping the lake and the database volume
	$(COMPOSE) --profile tools --profile catalog --profile cache down

.PHONY: docker-logs
docker-logs: ## Follow the containers' logs
	$(COMPOSE) logs -f

.PHONY: docker-ps
docker-ps: ## Show what is running, and whether the API is healthy
	$(COMPOSE) ps

.PHONY: docker-ingest
docker-ingest: ## Run one pipeline command in a container, e.g. make docker-ingest CMD="sources run bnpb-disaster"
	@test -n "$(CMD)" || (echo 'usage: make docker-ingest CMD="sources list"' >&2; exit 1)
	$(COMPOSE) run --rm pipelines $(CMD)

.PHONY: docker-shell
docker-shell: ## A shell in the pipelines image, for poking at the lake
	$(COMPOSE) run --rm --entrypoint /bin/bash pipelines

# ---- nas ------------------------------------------------------------------
#
# The deployment: the stack runs on the UGREEN box, behind a Cloudflare
# Tunnel. See docs/nas-deployment.md.
#
# The containers are started and restarted from the UGOS Docker app, because
# the login user is not in the docker group there — so the targets below
# either work over SSH without the socket (deploy, status, shell) or say so
# when they need it. Overridable per invocation:
#
#   make nas-deploy NAS_HOST=192.168.1.212 NAS_DIR=/volume2/terusan

NAS_HOST ?= 192.168.1.212
NAS_USER ?= dev
NAS_PORT ?= 22
NAS_DIR  ?= /volume2/terusan
TUNNEL_HOSTNAME ?= terusan.csis.or.id

NAS_SSH     := ssh -p $(NAS_PORT) $(NAS_USER)@$(NAS_HOST)
NAS_COMPOSE := cd $(NAS_DIR)/stack && docker compose
NAS_UI_HINT := echo "  → no Docker socket for $(NAS_USER); use UGOS → Docker → Project → terusan"

.PHONY: nas-tunnel-install
nas-tunnel-install: ## One-time: create the tunnel and hand its credentials to the NAS
	@./scripts/nas-tunnel-install.sh

.PHONY: nas-deploy
nas-deploy: ## Sync the repository to the NAS and render the stack's compose file
	@./scripts/nas-deploy.sh

.PHONY: nas-ps
nas-ps: ## What is running on the NAS, and whether the API is healthy
	@$(NAS_SSH) "$(NAS_COMPOSE) ps" 2>/dev/null || $(NAS_UI_HINT)

.PHONY: nas-logs
nas-logs: ## Follow the containers' logs on the NAS
	@$(NAS_SSH) "$(NAS_COMPOSE) logs -f --tail 100" 2>/dev/null || $(NAS_UI_HINT)

.PHONY: nas-ingest
nas-ingest: ## Run one pipeline command on the NAS, e.g. make nas-ingest CMD="sources list"
	@test -n "$(CMD)" || (echo 'usage: make nas-ingest CMD="sources list"' >&2; exit 1)
	@$(NAS_SSH) "docker exec terusan-pipelines-1 terusan $(CMD)" 2>/dev/null \
	  || echo "  → no Docker socket for $(NAS_USER); run it from the pipelines container's terminal in UGOS: terusan $(CMD)"

.PHONY: nas-shell
nas-shell: ## A shell on the NAS, in the deployed repository
	@$(NAS_SSH) -t "cd $(NAS_DIR)/app && exec \$$SHELL -l"

.PHONY: nas-status
nas-status: ## Is the box up, and is the published hostname answering
	@echo "== the box"
	@$(NAS_SSH) "uptime; df -h $(NAS_DIR) | tail -1" 2>/dev/null || echo "   unreachable"
	@echo "== the lake"
	@$(NAS_SSH) "du -sh $(NAS_DIR)/lake/* 2>/dev/null | sed 's/^/   /'" 2>/dev/null || true
	@echo "== https://$(TUNNEL_HOSTNAME)/healthz"
	@curl -fsS -o /dev/null -w '   %{http_code} in %{time_total}s\n' https://$(TUNNEL_HOSTNAME)/healthz || echo "   no answer"

# ---- scheduling -----------------------------------------------------------

AGENT       := com.terusan.scheduled
AGENT_PLIST := $(HOME)/Library/LaunchAgents/$(AGENT).plist

# The agent this replaced, booted out on install so an older checkout's 05:00
# daily run cannot keep firing invisibly beside the hourly one.
LEGACY_AGENT       := com.terusan.daily
LEGACY_AGENT_PLIST := $(HOME)/Library/LaunchAgents/$(LEGACY_AGENT).plist

.PHONY: due
due: ## Show which sources their schedule says are due this hour (collects nothing)
	@./scripts/scheduled.sh --list

.PHONY: scheduled
scheduled: ## Collect whatever is due now, by each source's own cron
	@./scripts/scheduled.sh

.PHONY: daily
daily: ## Run every active daily source now, whatever their cron says
	@./scripts/daily.sh

.PHONY: schedule-install
schedule-install: ## Install the launchd agent that runs `make scheduled` hourly
	@mkdir -p $(HOME)/Library/LaunchAgents .cache/logs
	@sed -e 's#__PROJECT_ROOT__#$(CURDIR)#g' -e 's#__PATH__#$(PATH)#g' \
	  infra/local/$(AGENT).plist > $(AGENT_PLIST)
	@launchctl bootout gui/$$(id -u)/$(LEGACY_AGENT) 2>/dev/null \
	  && echo "removed the superseded $(LEGACY_AGENT) agent" || true
	@rm -f $(LEGACY_AGENT_PLIST)
	@launchctl bootout gui/$$(id -u)/$(AGENT) 2>/dev/null || true
	@launchctl bootstrap gui/$$(id -u) $(AGENT_PLIST)
	@echo "installed $(AGENT_PLIST); runs on the hour"

.PHONY: schedule-uninstall
schedule-uninstall: ## Remove the launchd agent
	@launchctl bootout gui/$$(id -u)/$(AGENT) 2>/dev/null || true
	@launchctl bootout gui/$$(id -u)/$(LEGACY_AGENT) 2>/dev/null || true
	@rm -f $(AGENT_PLIST) $(LEGACY_AGENT_PLIST)
	@echo "removed $(AGENT)"

.PHONY: schedule-status
schedule-status: ## Show whether the agent is loaded, and when it last ran
	@launchctl print gui/$$(id -u)/$(AGENT) 2>/dev/null \
	  | grep -E 'state|last exit code|runs' || echo "$(AGENT) is not loaded"
	@launchctl print gui/$$(id -u)/$(LEGACY_AGENT) >/dev/null 2>&1 \
	  && echo "WARNING: the superseded $(LEGACY_AGENT) agent is still loaded; run make schedule-install" || true
	@ls -t .cache/logs/daily-*.log 2>/dev/null | head -1 \
	  | xargs -I{} sh -c 'echo; echo "last log: {}"; tail -5 {}' || true

# ---- pipeline -------------------------------------------------------------

.PHONY: catalog-sync
catalog-sync: ## Push the source registry into PostgreSQL
	cd pipelines && uv run terusan catalog sync

.PHONY: runs
runs: ## Show recent pipeline runs from the lake's journal
	cd pipelines && uv run terusan runs

.PHONY: failures
failures: ## Show recent pipeline runs that failed
	cd pipelines && uv run terusan runs --failed

.PHONY: catalog-runs
catalog-runs: ## Show recent pipeline runs from PostgreSQL
	cd pipelines && uv run terusan catalog runs

.PHONY: ingest
ingest: ## Run every scheduled source into RAW
	cd pipelines && uv run terusan sources run

.PHONY: extract
extract: ## Extract RAW into Bronze
	cd pipelines && uv run terusan warehouse extract

.PHONY: dimensions
dimensions: ## Publish geography and commodity dimensions into Silver
	cd pipelines && uv run terusan silver dimensions

.PHONY: documents
documents: ## Publish the catalogue of collected source material into Silver
	cd pipelines && uv run terusan silver documents

.PHONY: silver
silver: ## Normalize Bronze into Silver (see `terusan silver normalize --help`)
	@echo "Silver needs a column mapping per indicator; run:"
	@echo "  cd pipelines && uv run terusan silver normalize --help"

.PHONY: news
news: ## Read one shard of the newspapers, count everything, keep the violence
	cd pipelines && uv run terusan sources run news-monitoring
	cd pipelines && uv run terusan warehouse extract news news-monitoring
	cd pipelines && uv run terusan news cluster
	@./scripts/normalize-news.sh

.PHONY: news-validate
news-validate: ## Score a month of machine coding against VEWS (MONTH=2025-05)
	cd pipelines && uv run terusan news validate $(or $(MONTH),2025-05)

.PHONY: pihps-silver
pihps-silver: ## Normalize PIHPS food prices into one Silver indicator per market
	@./scripts/normalize-pihps.sh

.PHONY: bnpb-silver
bnpb-silver: ## Normalize BNPB disaster impact into one Silver indicator per measure and hazard
	@./scripts/normalize-bnpb.sh

.PHONY: vews-ingest
vews-ingest: ## Land the VEWS yearly exports from tmp/vews (or $$VEWS_DROP_DIR) into RAW
	cd pipelines && uv run terusan sources run vews-collective-violence

.PHONY: vews-silver
vews-silver: ## Normalize VEWS collective violence into one Silver indicator per measure
	@./scripts/normalize-vews.sh

.PHONY: rca-ingest
rca-ingest: ## Land the HS6 RCA working files from tmp/rca (or $$RCA_DROP_DIR) into RAW
	cd pipelines && uv run terusan sources run rca-seed

.PHONY: rca-silver
rca-silver: ## Normalize the RCA base years and WITS sector RCA into Silver
	@./scripts/normalize-rca.sh

.PHONY: pihps-backfill
pihps-backfill: ## Fetch every PIHPS price back to March 2017 (long: ~16k requests)
	@echo "35 places x 4 markets x a month per window since 2017-03."
	@echo "Some 16,000 requests at 2/s. Expect a couple of hours."
	cd pipelines && uv run terusan sources run bi-pihps-food-prices \
	  --since 2017-03-01 --trigger backfill

.PHONY: compact
compact: ## Merge small Parquet files across the analytical layers
	cd pipelines && uv run terusan warehouse compact bronze
	cd pipelines && uv run terusan warehouse compact silver
	cd pipelines && uv run terusan warehouse compact gold

# ---- quality --------------------------------------------------------------

.PHONY: smoke
smoke: ## Check a running stack serves data to a browser (needs `make dev`)
	@./scripts/smoke.sh

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
	$(PNPM) --filter @terusan/portal format:check

.PHONY: fmt
fmt: ## Format every language
	cd services/api && gofmt -w .
	cd pipelines && uv run ruff format . && uv run ruff check --fix .
	$(PNPM) --filter @terusan/portal format

.PHONY: build
build: ## Build every deployable artifact
	cd services/api && go build -o bin/api ./cmd/api
	$(PNPM) -r build

.PHONY: clean
clean: ## Remove build output and scratch (never touches the data lake)
	rm -rf services/api/bin apps/portal/dist .cache/*
