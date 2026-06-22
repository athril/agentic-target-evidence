DC := docker compose
PYTEST := uv run pytest

.DEFAULT_GOAL := help

# ── Infrastructure groups ─────────────────────────────────────────────────────

INFRA_SERVICES   := postgres redis clickhouse
LANGFUSE_SERVICES := minio minio-setup langfuse-web langfuse-worker
OTEL_SERVICES    := otel-collector
APP_SERVICES     := ollama data-acquisition agents-knowledge agents-reasoning report planner mcp-gateway chat

# ── Top-level targets ─────────────────────────────────────────────────────────

.PHONY: dirs up down restart ps logs help

dirs: ## Create host-side result directories before Docker claims them as root
	mkdir -p results/original results/data results/report

up: dirs ## Start everything (infra + Langfuse + OTEL + app)
	$(DC) up -d $(INFRA_SERVICES) $(LANGFUSE_SERVICES) $(OTEL_SERVICES) $(APP_SERVICES)

down: ## Stop and remove all containers (data volumes preserved)
	$(DC) down

restart: down up ## Full stop + start

ps: ## Show running container status
	$(DC) ps

logs: ## Tail logs for all running services (Ctrl-C to exit)
	$(DC) logs -f

# ── Selective start targets ───────────────────────────────────────────────────

.PHONY: infra langfuse otel

infra: ## Start only infrastructure (postgres, redis, clickhouse)
	$(DC) up -d $(INFRA_SERVICES)

langfuse: infra ## Start Langfuse stack (requires infra)
	$(DC) up -d $(LANGFUSE_SERVICES)
	@echo "Langfuse UI → http://localhost:3000  (admin@gtv.local / admin)"

otel: langfuse ## Start OTEL collector (requires Langfuse)
	$(DC) up -d $(OTEL_SERVICES)

# ── Selective stop targets ────────────────────────────────────────────────────

.PHONY: stop-app stop-otel stop-langfuse stop-infra

stop-app: ## Stop only application containers
	$(DC) stop $(APP_SERVICES)

stop-otel: ## Stop OTEL collector
	$(DC) stop $(OTEL_SERVICES)

stop-langfuse: ## Stop Langfuse services
	$(DC) stop $(LANGFUSE_SERVICES)

stop-infra: ## Stop infrastructure (postgres, redis, clickhouse)
	$(DC) stop $(INFRA_SERVICES)

# ── Log tailing helpers ───────────────────────────────────────────────────────

.PHONY: logs-langfuse logs-otel logs-infra

logs-langfuse: ## Tail Langfuse web + worker logs
	$(DC) logs -f langfuse-web langfuse-worker

logs-otel: ## Tail OTEL collector logs
	$(DC) logs -f otel-collector

logs-infra: ## Tail postgres + redis + clickhouse logs
	$(DC) logs -f $(INFRA_SERVICES)

# ── Development ───────────────────────────────────────────────────────────────

.PHONY: test test-smoke test-unit install mcp-serve mcp-chat chat

install: ## Install Python dependencies
	uv sync

mcp-serve: ## Run the MCP gateway (all public connectors as one server); MCP_TRANSPORT=http for HTTP
	uv run target-evidence-mcp

mcp-chat: ## Run the chat assistant locally (Gradio + Ollama + MCP tools); needs `MCP_TRANSPORT=http make mcp-serve` running first
	uv run --group chat target-evidence-chat

chat: ## Start the chat assistant + its MCP gateway as Docker services (UI → http://localhost:7860)
	$(DC) up -d mcp-gateway chat

GENE       ?= PTPN1
DISEASE    ?= pancreatic cancer
TISSUE     ?=
POPULATION ?=

run: ## Run analysis: make run GENE=BRCA1 DISEASE="breast cancer"
	uv run python run_analysis.py "$(GENE)" "$(DISEASE)" $(if $(TISSUE),--tissue "$(TISSUE)") $(if $(POPULATION),--population "$(POPULATION)")

test: ## Run all tests (excluding smoke)
	$(PYTEST) tests/ -m "not smoke" -q

test-smoke: ## Run end-to-end smoke test (requires Ollama + internet)
	$(PYTEST) tests/smoke/ -v -s -m smoke

test-schemas: ## Run schema / contract tests only
	$(PYTEST) tests/schemas/ -v

# ── Data management ───────────────────────────────────────────────────────────

.PHONY: db-migrate clean-volumes

db-migrate: ## Run Alembic migrations against the running postgres
	env $(shell grep -v '^#' .env | xargs) uv run alembic upgrade head

clean-volumes: ## Remove ALL data volumes (destructive — asks for confirmation)
	@echo "This will delete postgres-data, clickhouse-data, and redis-data."
	@read -p "Type YES to confirm: " c && [ "$$c" = "YES" ]
	$(DC) down -v

# ── Help ──────────────────────────────────────────────────────────────────────

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage: make \033[36m<target>\033[0m\n\nTargets:\n"} \
	     /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
