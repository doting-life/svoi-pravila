# Svoi Pravila

AI helper for difficult conversations: a Telegram inline bot and mini-app that remembers the agreements between two people. This repository holds the backend service and the mini-app; see `AGENTS.md` for executor workflow and ownership boundaries.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) at the version in `scripts/uv-version` (Python from `backend/.python-version`)
- Node from `miniapp/.nvmrc` and pnpm from `miniapp/package.json` `packageManager` (Corepack)
- Docker (Compose v2) for the local stack (API, one-shot migrations, PostgreSQL 18, Valkey)

## Local setup

```bash
cp .env.example .env
make install
make infra-up
make ci
```

`make install` syncs backend dependencies and points this clone at `.githooks` (`make hooks`). Commit runs format and lint on staged files; push runs `make ci`.

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
make up      # build images, migrate, start API + mini-app; wait until healthy; print URLs
make logs    # follow api, migrate, and miniapp logs
make ps      # compose status
make down    # stop the full stack; keep named volumes
```

### Mini-app over HTTPS (Telegram menu button)

Telegram requires HTTPS for the Web App menu button. For local checks, use the Compose `tunnel` profile (Cloudflare quick tunnel to the `miniapp` service):

```bash
make up
make miniapp-tunnel
```

In the tunnel logs, copy the printed `https://….trycloudflare.com` origin into **your own** `.env` as `SP_MINIAPP_URL` (no path or query). Restart the API so the bot can call `setChatMenuButton` on startup, then open the bot’s chat menu button «Мои правила».

The trycloudflare URL **changes every run**. Stop the tunnel with `make miniapp-tunnel-down`. The executor never edits the owner’s `.env`.

Data lives in Docker named volumes (`postgres_data`, `valkey_data`). To reset local data:

```bash
docker compose down -v
```

That destroys local PostgreSQL and Valkey data.

Tests never write to the manual-testing database: they use a dedicated PostgreSQL database named `<POSTGRES_DB>_test` (for example `svoi_pravila_test`) and Valkey logical database `15`.

## Make targets

| Target | Purpose |
|--------|---------|
| `install` | Sync backend deps with uv and set `core.hooksPath` to `.githooks` |
| `hooks` | Point this clone at `.githooks` (repo-local git config only) |
| `toolchain-check` | Compare local Python, uv, Node, pnpm, Docker, and image digests with pins |
| `ci` | Full local mirror of GitHub CI (fail-fast, one summary line per stage) |
| `build` | Build app images (`svoi-pravila-api:local`) |
| `up` | Build and start the full stack (profile `app`); wait until API/mini-app are healthy |
| `down` | Stop the full stack; keep volumes |
| `logs` | Follow `api`, `migrate`, and `miniapp` logs |
| `ps` | Show compose service status |
| `miniapp-check` | OpenAPI drift check + mini-app lint/test/build |
| `miniapp-tunnel` | Start Cloudflare quick tunnel to the mini-app (profile `tunnel`) |
| `miniapp-tunnel-down` | Stop the tunnel service |
| `fmt` | Ruff format |
| `lint` | Ruff lint |
| `typecheck` | mypy |
| `imports` | import-linter contracts |
| `test-unit` | Unit tests |
| `test-integration` | Integration tests (requires `infra-up`) |
| `test` | Unit + integration with coverage gates |
| `audit` | `pip-audit` against the lockfile |
| `secrets` | gitleaks scan of the working tree |
| `image` | Build API and mini-app CI images |
| `image-scan` | Trivy HIGH/CRITICAL scan of both CI images |
| `stack-smoke` | Compose stack smoke (env file outside the repo) |
| `ownership-guard` | Forbid task-branch edits to CTO-owned paths (`BASE=<ref>`) |
| `migrations-check` | Alembic upgrade + check against compose DB |
| `infra-up` / `infra-down` | Start/stop local Postgres and Valkey (tests/gates) |
| `check` | Backend and mini-app gates in order (fail-fast) |

Agent instructions and ownership: [`AGENTS.md`](AGENTS.md).
