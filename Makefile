.PHONY: install fmt fmt-check lint typecheck imports test-unit test-integration test \
	audit secrets migrations-check image-scan dev-env infra-up infra-down build up down \
	logs ps bench-llm eval-llm check

BACKEND := backend
GITLEAKS_IMAGE := zricethezav/gitleaks:v8.30.1@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f
TRIVY_IMAGE := aquasec/trivy:0.70.0@sha256:be1190afcb28352bfddc4ddeb71470835d16462af68d310f9f4bca710961a41e

# Pass repo-root .env into uv when present (path relative to backend/).
ENV_FILE_ARG := $(if $(wildcard .env),--env-file ../.env,)
UV := cd $(BACKEND) && uv run $(ENV_FILE_ARG)

install:
	cd $(BACKEND) && uv sync --frozen
	cd $(BACKEND) && uv run $(ENV_FILE_ARG) pre-commit install

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
	$(UV) coverage report --include='*/svoi_pravila/adapters/persistence/*' --fail-under=95
	$(UV) coverage report --include='*/svoi_pravila/adapters/llm/*' --fail-under=100
	$(UV) coverage report --include='*/svoi_pravila/adapters/channels/*' --fail-under=95
	$(UV) coverage report --include='*/svoi_pravila/adapters/cache/*' --fail-under=95
	$(UV) coverage report --include='*/svoi_pravila/evals/*' --fail-under=95

bench-llm:
	docker compose --profile app run --rm --entrypoint svoi-pravila-bench-llm api $(BENCH_ARGS)

eval-llm:
	docker compose --profile app run --rm --entrypoint svoi-pravila-eval api $(EVAL_ARGS)

audit:
	cd $(BACKEND) && uv export --frozen --no-dev --no-emit-project -o /tmp/svoi-pravila-requirements.txt
	$(UV) pip-audit -r /tmp/svoi-pravila-requirements.txt

secrets:
	docker run --rm -v "$(CURDIR):/repo:ro" $(GITLEAKS_IMAGE) detect --source=/repo --verbose

image-scan:
	docker build -t svoi-pravila:ci -f $(BACKEND)/Dockerfile $(BACKEND)
	docker run --rm -v /var/run/docker.sock:/var/run/docker.sock $(TRIVY_IMAGE) \
		image --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 --format table \
		svoi-pravila:ci

migrations-check:
	$(UV) alembic upgrade head
	$(UV) alembic check

dev-env:
	test -f .env || cp .env.example .env
	cd $(BACKEND) && uv run python ../scripts/ensure_dev_env.py

infra-up: dev-env
	docker compose up -d --wait

infra-down:
	docker compose down

build:
	docker compose --profile app build

up: build
	docker compose --profile app up -d --wait
	@echo "API: http://127.0.0.1:$${API_PORT:-8000}"

down:
	docker compose --profile app down

logs:
	docker compose --profile app logs -f api migrate

ps:
	docker compose --profile app ps

check: lint fmt-check typecheck imports migrations-check test audit secrets
