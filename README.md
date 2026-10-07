# Svoi Pravila

AI helper for difficult conversations: a Telegram inline bot and mini-app that remembers the agreements between two people. This repository holds the backend service and the mini-app; see `AGENTS.md` for executor workflow and ownership boundaries.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) at `[tool.uv] required-version` in `backend/pyproject.toml` (Python from `backend/.python-version`)
- Node from `miniapp/.nvmrc`
- pnpm on PATH at the `packageManager` version in `miniapp/package.json` (any installer; for example a one-time `corepack enable` done by the developer, not by make). `make toolchain-check` enforces it.
- Docker (Compose v2) for the local stack (API, one-shot migrations, PostgreSQL 18, Valkey)

## Local setup

```bash
cp .env.example .env
make install
make infra-up
make ci
```

`make install` syncs backend dependencies and points this clone at `.githooks` (`make hooks`). Commit runs format and lint on staged files; push runs `make ci` on the exact pushed commit in a temporary worktree.

`make ci` never reads or writes the repository `.env`. It writes an ephemeral env file outside the repo, uses compose project `svoi-pravila-ci` on its own ports, and removes that project (volumes included) on exit. Run one GitHub job locally with `make ci JOB=backend` (or `miniapp`, `secrets`, `image`, `stack-smoke`, `ownership-guard`).

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

### Analytics dashboard

Grafana reads only the aggregate tables (`analytics_daily`, `analytics_daily_scenario`, `analytics_cohorts`) via the configured Grafana DB user (`SP_GRAFANA_DB_USER`, default `grafana_reader`), a member of `svoi_analytics_read`. It is never exposed on a public interface — only `127.0.0.1`, on an internal compose network shared with Postgres.

The one-shot `migrate` service needs a Postgres role with `CREATEROLE` (not superuser) so it can create the analytics group role and the Grafana LOGIN role. Full separation of migrate / app / analytics roles is planned for task 0022.

Locally:

```bash
make up
make observability-up
```

Open `http://127.0.0.1:${GRAFANA_PORT:-3000}` (admin password from `SP_GRAFANA_ADMIN_PASSWORD` in your env file). Stop with `make observability-down`.

On the VPS, open an SSH tunnel and keep Grafana bound to loopback only:

```bash
ssh -L 3000:127.0.0.1:3000 <host>
```

Then open `http://127.0.0.1:3000` on your machine. Do not publish Grafana ports on the host firewall.

Data lives in Docker named volumes (`postgres_data`, `valkey_data`). To reset local data:

```bash
docker compose down -v
```

That destroys local PostgreSQL and Valkey data.

Tests never write to the manual-testing database: they use a dedicated PostgreSQL database named `<POSTGRES_DB>_test` (for example `svoi_pravila_test`) and Valkey logical database `15`.

The integration suite connects to Postgres as the compose admin role (`POSTGRES_USER`) and requires that role to be a **superuser** (the default `postgres` image user is). At session start it asserts `rolsuper`; otherwise it fails with a clear message naming that requirement. A non-superuser admin cannot create/drop the temporary databases and roles the suite needs.

## Make targets

| Target | Purpose |
|--------|---------|
| `install` | Sync backend deps with uv and set `core.hooksPath` to `.githooks` |
| `hooks` | Point this clone at `.githooks` (repo-local git config only) |
| `toolchain-check` | Compare local Python, uv, Node, pnpm, Docker, and image digests with pins |
| `ci` | Hermetic local mirror of GitHub CI (`make ci JOB=<name>` for one job). Never touches `.env`. |
| `build` | Build app images (`svoi-pravila-api:local`) |
| `up` | Build and start the full stack (profile `app`); wait until API/mini-app are healthy |
| `down` | Stop the full stack; keep volumes |
| `logs` | Follow `api`, `migrate`, and `miniapp` logs |
| `ps` | Show compose service status |
| `miniapp-check` | OpenAPI drift check + mini-app lint/test/build |
| `miniapp-tunnel` | Start Cloudflare quick tunnel to the mini-app (profile `tunnel`) |
| `miniapp-tunnel-down` | Stop the tunnel service |
| `observability-up` | Start Grafana (profile `observability`) after migrate; bind `127.0.0.1` only |
| `observability-down` | Stop the Grafana service |
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
| `check` | Backend and mini-app gates (no secrets scan) |

Agent instructions and ownership: [`AGENTS.md`](AGENTS.md).
