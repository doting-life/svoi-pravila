.PHONY: install fmt fmt-check lint typecheck imports test-unit test-integration test \
	audit secrets migrations-check infra-up infra-down run check

BACKEND := backend
GITLEAKS_IMAGE := zricethezav/gitleaks:v8.30.1@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f

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
	$(UV) pytest tests/unit -m unit --cov=svoi_pravila --cov-report=term-missing

test-integration:
	$(UV) pytest tests/integration -m integration --cov=svoi_pravila --cov-append --cov-report=term-missing

test:
	rm -f $(BACKEND)/.coverage
	$(UV) pytest tests/unit tests/integration \
		--cov=svoi_pravila \
		--cov-report=term-missing \
		--cov-fail-under=95
	$(UV) coverage report --include='*/svoi_pravila/application/*' --fail-under=100

audit:
	cd $(BACKEND) && uv export --frozen --no-dev --no-emit-project -o /tmp/svoi-pravila-requirements.txt
	$(UV) pip-audit -r /tmp/svoi-pravila-requirements.txt

secrets:
	docker run --rm -v "$(CURDIR):/repo:ro" $(GITLEAKS_IMAGE) detect --source=/repo --verbose

migrations-check:
	$(UV) alembic upgrade head
	$(UV) alembic check

infra-up:
	test -f .env || cp .env.example .env
	docker compose up -d --wait

infra-down:
	docker compose down

run:
	$(UV) svoi-pravila-api

check: lint fmt-check typecheck imports test audit secrets migrations-check
