# Svoi Pravila

AI helper for difficult conversations: a Telegram inline bot and mini-app that remembers the agreements between two people. This repository holds the backend service and (later) the mini-app; see `AGENTS.md` for executor workflow and ownership boundaries.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (Python 3.13 managed by uv)
- Docker (Compose v2) for the local stack (API, one-shot migrations, PostgreSQL 18, Valkey)

## Local setup

```bash
cp .env.example .env
make install
make infra-up
make check
```

`make infra-up` starts only PostgreSQL and Valkey (for gates and tests). Use the Local stack section below to run the application itself.

## Fresh environment check

Never delete, move, or overwrite an existing repo-root `.env` (it holds credentials that cannot be restored from the repository). To run gates against a generated file:

```bash
make dev-env ENV_FILE=/tmp/sp-fresh.env && make check ENV_FILE=/tmp/sp-fresh.env
```

`make dev-env ENV_FILE=/path/outside/repo.env` creates or updates that file only.

## Local stack

The supported way to run the application locally is the Docker stack (same images intended for the VPS): one-shot migrations, then the API, plus PostgreSQL and Valkey.

Prerequisites: Docker Compose v2, and a repo-root `.env` (from `cp .env.example .env` then `make dev-env` to fill `SP_DATA_KEK`).

```bash
make up      # build images, migrate, start API; wait until healthy; print URL
make logs    # follow api and migrate logs
make ps      # compose status
make down    # stop the full stack; keep named volumes
```

Data lives in Docker named volumes (`postgres_data`, `valkey_data`). To reset local data:

```bash
docker compose down -v
```

That destroys local PostgreSQL and Valkey data.

Tests never write to the manual-testing database: they use a dedicated PostgreSQL database named `<POSTGRES_DB>_test` (for example `svoi_pravila_test`) and Valkey logical database `15`.

## Make targets

| Target | Purpose |
|--------|---------|
| `install` | Sync backend deps with uv (incl. dev group) and install pre-commit hooks |
| `build` | Build app images (`svoi-pravila-api:local`) |
| `up` | Build and start the full stack (profile `app`); wait until API is healthy |
| `down` | Stop the full stack; keep volumes |
| `logs` | Follow `api` and `migrate` logs |
| `ps` | Show compose service status |
| `fmt` | Ruff format |
| `lint` | Ruff lint |
| `typecheck` | mypy |
| `imports` | import-linter contracts |
| `test-unit` | Unit tests |
| `test-integration` | Integration tests (requires `infra-up`) |
| `test` | Unit + integration with coverage gates |
| `audit` | `pip-audit` against the lockfile |
| `secrets` | gitleaks scan of the working tree |
| `migrations-check` | Alembic upgrade + check against compose DB |
| `infra-up` / `infra-down` | Start/stop local Postgres and Valkey (tests/gates) |
| `check` | All gates in order (fail-fast) |

Agent instructions and ownership: [`AGENTS.md`](AGENTS.md).
