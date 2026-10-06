.PHONY: install fmt fmt-check lint typecheck imports test-unit test-integration test \
	audit secrets migrations-check image image-scan openapi miniapp-install miniapp-api-check \
	miniapp-check miniapp-tunnel miniapp-tunnel-down observability-up observability-down \
	dev-env infra-up infra-down build up down logs ps bench-llm eval-llm analytics check \
	toolchain-check stack-smoke ownership-guard hooks ci

BACKEND := backend
MINIAPP := miniapp
include scripts/image-pins.env
export GITLEAKS_IMAGE TRIVY_IMAGE ACTIONLINT_IMAGE
CI_JOBS := backend miniapp secrets image stack-smoke ownership-guard
export CI_JOBS
export JOB
export BASE

ENV_FILE ?= .env
export ENV_FILE
ENV_FILE_ABS := $(abspath $(ENV_FILE))
ENV_FILE_ARG := $(if $(wildcard $(ENV_FILE)),--env-file $(ENV_FILE_ABS),)
COMPOSE_ENV := $(if $(wildcard $(ENV_FILE)),--env-file $(ENV_FILE_ABS),)
COMPOSE := docker compose $(COMPOSE_ENV)
UV := cd $(BACKEND) && uv run $(ENV_FILE_ARG)
PNPM := cd $(MINIAPP) && pnpm

install:
	cd $(BACKEND) && uv sync --frozen
	$(MAKE) hooks

fmt:
	$(UV) ruff format src tests migrations

fmt-check:
	$(UV) ruff format --check src tests migrations

lint:
	$(UV) ruff check src tests migrations

typecheck:
	$(UV) mypy src tests

imports:
	$(UV) lint-imports

test-unit:
	$(UV) pytest tests/unit tests/contract -m unit --cov=svoi_pravila --cov-report=term-missing

test-integration:
	$(UV) pytest tests/integration -m integration --cov=svoi_pravila --cov-append --cov-report=term-missing

test:
	rm -f $(BACKEND)/.coverage
	$(UV) pytest tests/unit tests/contract tests/integration \
		--cov=svoi_pravila \
		--cov-report=term-missing \
		--cov-fail-under=95
	$(UV) coverage report --include='*/svoi_pravila/domain/*' --fail-under=100
	$(UV) coverage report --include='*/svoi_pravila/application/*' --fail-under=100
	$(UV) coverage report --include='*/svoi_pravila/crypto/*' --fail-under=100
	$(UV) coverage report --include='*/svoi_pravila/api/*' --fail-under=100
	$(UV) coverage report --include='*/svoi_pravila/adapters/channels/telegram/init_data.py' --fail-under=100
	$(UV) coverage report --include='*/svoi_pravila/adapters/persistence/*' --fail-under=95
	$(UV) coverage report --include='*/svoi_pravila/adapters/llm/*' --fail-under=100
	$(UV) coverage report --include='*/svoi_pravila/adapters/channels/*' --fail-under=95
	$(UV) coverage report --include='*/svoi_pravila/adapters/cache/*' --fail-under=95
	$(UV) coverage report --include='*/svoi_pravila/adapters/system/inline_result_reuse.py' --fail-under=100
	$(UV) coverage report --include='*/handlers/inline.py' --fail-under=100
	$(UV) coverage report --include='*/handlers/contacts.py' --fail-under=95
	$(UV) coverage report --include='*/handlers/onboarding.py' --fail-under=95
	$(UV) coverage report --include='*/handlers/rules.py' --fail-under=95
	$(UV) coverage report --include='*/svoi_pravila/evals/*' --fail-under=95
	$(UV) coverage report --include='*/svoi_pravila/adapters/persistence/analytics_store.py' --fail-under=95
	$(UV) coverage report --include='*/svoi_pravila/adapters/system/analytics_scheduler.py' --fail-under=100

analytics:
	$(COMPOSE) --profile app run --rm --entrypoint svoi-pravila-analytics api $(ARGS)

bench-llm:
	$(COMPOSE) --profile app run --rm --entrypoint svoi-pravila-bench-llm api $(BENCH_ARGS)

eval-llm:
	mkdir -p $(BACKEND)/evals/out
	$(COMPOSE) --profile app run --rm \
		-v "$(CURDIR)/$(BACKEND)/evals/out:/app/evals/out" \
		--entrypoint svoi-pravila-eval api $(EVAL_ARGS)

audit:
	cd $(BACKEND) && uv export --frozen --no-dev --no-emit-project -o /tmp/svoi-pravila-requirements.txt
	$(UV) pip-audit -r /tmp/svoi-pravila-requirements.txt

secrets:
	./scripts/secrets.sh

image:
	./scripts/image.sh

image-scan:
	./scripts/image_scan.sh

toolchain-check:
	cd $(BACKEND) && uv run python ../scripts/toolchain_check.py

stack-smoke:
	./scripts/stack_smoke.sh

ownership-guard:
	BASE="$(BASE)" ./scripts/ownership_guard.sh

hooks:
	git config core.hooksPath .githooks

ci:
	./scripts/ci.sh

openapi:
	cd $(BACKEND) && uv run python scripts/export_openapi.py --out ../$(MINIAPP)/src/api/openapi.json
	$(PNPM) run openapi:types

miniapp-install:
	$(PNPM) install --frozen-lockfile

miniapp-api-check: openapi
	git diff --exit-code -- $(MINIAPP)/src/api/openapi.json $(MINIAPP)/src/api/schema.d.ts

miniapp-check: miniapp-api-check
	$(PNPM) run check

miniapp-tunnel:
	$(COMPOSE) --profile app --profile tunnel up -d tunnel
	@echo "Copy the https://….trycloudflare.com URL from the tunnel logs into SP_MINIAPP_URL, then restart api."
	@echo "The URL changes every run."
	$(COMPOSE) --profile app --profile tunnel logs -f tunnel

miniapp-tunnel-down:
	$(COMPOSE) --profile app --profile tunnel stop tunnel
	$(COMPOSE) --profile app --profile tunnel rm -f tunnel

observability-up: build
	$(COMPOSE) --profile observability up -d --wait migrate grafana
	@echo "Grafana: http://127.0.0.1:$$(docker compose $(COMPOSE_ENV) port grafana 3000 | sed 's/.*://') (loopback only; SSH tunnel on VPS)"

observability-down:
	$(COMPOSE) --profile observability stop grafana
	$(COMPOSE) --profile observability rm -f grafana

migrations-check:
	$(UV) alembic upgrade head
	$(UV) alembic check

dev-env:
	test -s "$(ENV_FILE)" || cp .env.example "$(ENV_FILE)"
	cd $(BACKEND) && uv run python ../scripts/ensure_dev_env.py --env-file "$(ENV_FILE_ABS)"

infra-up: dev-env
	$(COMPOSE) up -d --wait

infra-down:
	$(COMPOSE) down

build:
	$(COMPOSE) --profile app build

up: build
	$(COMPOSE) --profile app up -d --wait
	@echo "API: http://127.0.0.1:$${API_PORT:-8000}"
	@echo "Mini-app: http://127.0.0.1:$${MINIAPP_PORT:-8080}"

down:
	$(COMPOSE) --profile app down

logs:
	$(COMPOSE) --profile app logs -f api migrate miniapp

ps:
	$(COMPOSE) --profile app ps

check: lint fmt-check typecheck imports migrations-check test audit miniapp-check
