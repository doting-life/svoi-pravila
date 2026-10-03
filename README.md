# Svoi Pravila

AI helper for difficult conversations: a Telegram inline bot and mini-app that remembers the agreements between two people. This repository holds the backend service and (later) the mini-app; see `AGENTS.md` for executor workflow and ownership boundaries.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (Python 3.13 managed by uv)
- Docker (Compose v2) for local PostgreSQL 18 and Valkey

## Local setup

```bash
cp .env.example .env
make install
make infra-up
make check
```

Run the API (after infra is up and `.env` is configured):

```bash
make run
```

`make run` starts the `svoi-pravila-api` console script, which loads `SP_*` settings (from the process environment, or via `uv run --env-file ../.env` when `.env` exists) and binds to `SP_HTTP_HOST` / `SP_HTTP_PORT`.

## Make targets

| Target | Purpose |
|--------|---------|
| `install` | Sync backend deps with uv (incl. dev group) and install pre-commit hooks |
| `run` | Start the API (`svoi-pravila-api`) |
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
| `infra-up` / `infra-down` | Start/stop local Postgres and Valkey |
| `check` | All gates in order (fail-fast) |

Agent instructions and ownership: [`AGENTS.md`](AGENTS.md).
