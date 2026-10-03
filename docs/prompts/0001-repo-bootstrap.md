# Task 0001 — Repository bootstrap (backend foundation, quality gates, CI)

Author: CTO (Claude) · 2026-10-03 · Branch: `task/0001-repo-bootstrap`

## 0. Before you start
Read `AGENTS.md`, every file in `.cursor/rules/`, `docs/maintainers/02-architecture.md`, `docs/maintainers/03-privacy-and-security.md`, `docs/maintainers/adr/`. They are binding. Do not modify them. Do not modify or move `Svoi_Pravila.pptx`.

## 1. Goal
Create the production-grade foundation of the backend: project layout, configuration, privacy-safe logging, the ASGI application factory with health/readiness, PostgreSQL and Valkey connectivity, Alembic, local infrastructure, container image, and the full set of quality gates in `make check` and GitHub Actions.

**No product features in this task.** No domain entities, Telegram, LLM, mini-app, metrics or server deployment files. Do not create empty packages or modules "for later": every file you add must have real behaviour and tests, or be configuration that is used now.

## 2. Versions and dependencies
- Python **3.13** (latest patch), pinned in `backend/.python-version`. `requires-python = ">=3.13,<3.14"`.
- Use the latest **stable** release of every dependency, verified on PyPI / the official registry at execution time. Record exact resolved versions in the report. Commit `backend/uv.lock`.
- Runtime dependencies allowed in this task: `fastapi`, `uvicorn[standard]`, `pydantic`, `pydantic-settings`, `structlog`, `sqlalchemy[asyncio]`, `asyncpg`, `alembic`, `redis` (asyncio client, used against Valkey).
- Development dependency group (PEP 735 `[dependency-groups]`): `ruff`, `mypy`, `pytest`, `pytest-asyncio`, `pytest-cov`, `hypothesis`, `httpx`, `import-linter`, `pip-audit`, `pre-commit`.
- Build backend: `hatchling`. Source layout: `backend/src/svoi_pravila/`.
- Any other dependency → stop and report BLOCKED.

## 3. Requirements

### 3.1 Repository root
- `.gitignore` (Python, uv, env files, IDE/OS artefacts, coverage output), `.editorconfig`.
- `.env.example` — every setting from §3.3 with a one-line description and a safe local value or an empty value for secrets. No real secrets.
- `compose.yaml` for **local development only**: PostgreSQL **18** and Valkey (latest stable major), images pinned to an exact version tag, ports bound to `127.0.0.1` only, passwords read from `.env`, named volumes, healthchecks.
- `Makefile` — the single entry point. Targets: `install`, `fmt`, `lint`, `typecheck`, `imports`, `test-unit`, `test-integration`, `test` (both with coverage gate), `audit` (`pip-audit` against the lockfile), `secrets` (gitleaks via a version-pinned container image, scanning the working tree), `migrations-check`, `infra-up`, `infra-down`, `check` (runs every gate in order, fails fast). Targets must work from the repository root.
- `.pre-commit-config.yaml`: ruff (lint + format) and gitleaks hooks, all revisions pinned.
- `README.md`: one-paragraph project description, prerequisites (uv, Docker), local setup, `make` targets, pointer to `AGENTS.md`. Developer-facing only.
- `.github/pull_request_template.md`: link to task prompt and report, checklist of gates.

### 3.2 Tooling configuration (`backend/pyproject.toml`)
- **ruff:** target py313, line length 100; rule sets at minimum: `E, W, F, I, B, UP, N, S, A, C4, SIM, RET, PTH, ERA, PL, RUF, ASYNC, T20, DTZ, TRY, PERF, FBT`. Tests may relax only `S101` (assert) and `PLR2004` (magic values) through per-file ignores. No other ignores.
- **mypy:** `strict = true`, `warn_unreachable = true`, Pydantic mypy plugin enabled, `disallow_any_explicit = true` for `svoi_pravila.application`.
- **pytest:** `asyncio_mode = "auto"`, `--strict-markers`, `--strict-config`, markers `unit` and `integration`; warnings treated as errors.
- **coverage:** branch coverage on, `fail_under = 95`; a separate enforced check that `svoi_pravila.application` has 100 % line and branch coverage (implement through the Makefile `test` target, e.g. a second coverage report restricted to that package with its own threshold).
- **import-linter** contracts (all must be active and passing):
  1. Layers: `svoi_pravila.bootstrap` above `svoi_pravila.api` and `svoi_pravila.adapters` (these two are independent of each other) above `svoi_pravila.application`.
  2. `svoi_pravila.adapters.persistence` and `svoi_pravila.adapters.cache` are independent.
  3. `svoi_pravila.application` must not import: `svoi_pravila.config`, `svoi_pravila.observability`, `fastapi`, `starlette`, `pydantic`, `pydantic_settings`, `sqlalchemy`, `asyncpg`, `redis`, `structlog`, `alembic`.

### 3.3 Configuration — `svoi_pravila/config.py`
- `Settings` (pydantic-settings), env prefix `SP_`, reads `.env` only when `SP_ENVIRONMENT=local`.
- Fields: `environment` (enum: `local`, `test`, `staging`, `production`), `log_level` (enum), `http_host`, `http_port`, `database_url` (`SecretStr`, must be a `postgresql+asyncpg://` URL — validated), `valkey_url` (`SecretStr`, `redis://` or `rediss://` — validated), `readiness_timeout_seconds` (float, `0 < x <= 5`).
- In `staging` and `production`, `log_level` must not be `DEBUG` (validation error otherwise).
- Valkey TLS policy is decided in the deployment task; do not encode any TLS requirement now.
- No other fields. Telegram/LLM settings arrive with their tasks.
- `Settings` is constructed only in `bootstrap.py`.

### 3.4 Observability — `svoi_pravila/observability/`
- `configure_logging(settings)`: structlog JSON output to stdout; ISO-8601 UTC timestamps; log level from settings. **All stdlib loggers** (uvicorn, sqlalchemy, asyncio, alembic) are routed through structlog's `ProcessorFormatter` so every line is JSON and passes the same processors.
- **Redaction processor**, applied to every event (structlog and stdlib) before rendering:
  - replaces with the constant `"[REDACTED]"` the value of any key (at any nesting depth inside dicts, lists, tuples) whose normalized name (lower-case, `-` → `_`) is in the denylist: `text, message_text, draft, query, prompt, completion, content, body, rule_text, contact_label, caption, raw_update, forward_origin, password, token, secret, authorization, init_data`;
  - the denylist is a single module-level frozenset, documented;
  - the structlog `event` key itself is not redacted (it is a developer-authored constant message).
- Exceptions are rendered as structured tracebacks **without local variables** (structlog's dict traceback transformer must be configured with `show_locals=False`; verify the default of the installed version and set it explicitly).
- Uvicorn's access log is **disabled**. Instead, a pure ASGI middleware in `api/` logs one event per HTTP request: method, **route template** (not the raw path, never the query string), status code, duration in ms. Requests that match no route are logged with route `"<unmatched>"`.
- No `print`, no other logging setup anywhere.

### 3.5 Application layer — `svoi_pravila/application/`
- Port `ReadinessProbe` (`typing.Protocol`): attribute `name: str`, async method `check() -> None` that raises on failure.
- Use case `CheckReadiness`: constructed with a sequence of probes and a timeout; runs all probes **concurrently**, each bounded by the timeout; returns an immutable result: overall `ready: bool` and per-probe status (`ok` / `failed` / `timeout`). It never propagates probe exceptions and never exposes exception messages in its result.
- 100 % line and branch coverage with fake probes (ok, raising, hanging beyond timeout, mixed).

### 3.6 Adapters
- `adapters/persistence/`: async engine factory from settings (`pool_pre_ping=True`, explicit pool size and timeout parameters as constants), async session factory, a declarative `Base` whose `MetaData` uses an explicit **naming convention** for indexes, unique, check, foreign key and primary key constraints; `DatabaseProbe` implementing `ReadinessProbe` with `SELECT 1`.
- `adapters/cache/`: Valkey client factory (`redis.asyncio`) from settings, `ValkeyProbe` implementing `ReadinessProbe` with `PING`.
- Both expose async `close`/`dispose` used by the application lifespan.

### 3.7 API — `svoi_pravila/api/`
- `create_app(...)` factory receiving already-built collaborators (no global state): the `CheckReadiness` use case, the environment, and lifespan hooks for resource disposal.
- `GET /healthz` → 200 `{"status": "ok"}`; no dependencies touched.
- `GET /readyz` → 200 when ready, 503 otherwise; body lists probe names with `ok` / `failed` / `timeout` only.
- OpenAPI/docs endpoints enabled only in `local` and `test`; in `staging`/`production` they return 404.
- The request-logging middleware from §3.4.

### 3.8 Composition root — `svoi_pravila/bootstrap.py`
- `create_application()` — the only place that builds `Settings`, configures logging, builds the engine, Valkey client, probes, use case and app, and wires disposal into the lifespan. Usable as `uvicorn --factory svoi_pravila.bootstrap:create_application`.

### 3.9 Alembic — `backend/migrations/`
- Async `env.py` that obtains the URL from `Settings` (never from `alembic.ini`), uses `Base.metadata` as `target_metadata`, `compare_type=True`.
- No migration revisions yet (there are no tables).
- `make migrations-check`: against the compose database, runs `alembic upgrade head` then `alembic check` (must report no pending changes).

### 3.10 Container image — `backend/Dockerfile` (+ `backend/.dockerignore`)
- Multi-stage. Builder uses the official `uv` image pinned to an exact version; `uv sync --frozen --no-dev`. Runtime based on `python:3.13-slim` pinned to an exact patch tag.
- Runs as a dedicated non-root user; no build tools, no uv, no tests in the runtime image; `PYTHONDONTWRITEBYTECODE=1`, `PYTHONUNBUFFERED=1`.
- Entrypoint runs uvicorn with the factory from §3.8, access log disabled, proxy headers only from trusted proxies configured via an environment variable documented in `.env.example`.
- Image builds in CI; it is not pushed anywhere.

### 3.11 Tests — `backend/tests/`
- `tests/fakes/` with fake probes.
- Unit:
  - settings validation (valid; wrong DB scheme; wrong Valkey scheme; DEBUG forbidden in production; timeout bounds);
  - redaction processor: table-driven cases plus a **Hypothesis** property test — for arbitrarily nested structures containing denylisted keys, no denylisted value survives in the output;
  - **log canary**: configure logging, emit through structlog and through a stdlib logger (`uvicorn.error`) events containing a unique marker under a denylisted key, and raise/log an exception from a function whose local variable holds the marker; assert the marker never appears in captured output;
  - `CheckReadiness` (100 % branches);
  - API: `/healthz`; `/readyz` 200/503 with fake probes; docs disabled in `production`; request-log event contains route template, not raw path or query string.
- Integration (marked `integration`, require `make infra-up`): `DatabaseProbe` and `ValkeyProbe` against real services; failing probe against an unreachable port; Alembic upgrade on an empty database.

### 3.12 CI — `.github/workflows/ci.yml`
- Triggers: pull requests and pushes to `main`. `permissions: contents: read`. Concurrency group cancels superseded runs.
- **Every third-party action pinned by full commit SHA** with the version in a trailing comment.
- Jobs:
  1. `backend`: install uv (pinned), `uv sync --frozen`, ruff lint, ruff format check, mypy, lint-imports, unit + integration tests with coverage gates (PostgreSQL 18 and Valkey as job services with healthchecks), migrations check, pip-audit.
  2. `secrets`: gitleaks over full git history (binary or container pinned by version and checksum/digest — **not** the gitleaks GitHub Action, which requires a licence for organisations).
  3. `image`: build the Dockerfile; scan with Trivy (pinned) failing on HIGH/CRITICAL fixable vulnerabilities.
  4. `ownership-guard`: on pull requests whose head branch starts with `task/`, fail if the diff touches `AGENTS.md`, `.cursor/`, `docs/maintainers/` or `docs/prompts/`.

## 4. Out of scope
Domain model, Telegram, LLM, metrics endpoint, mini-app, server compose/Caddy/deployment, Valkey TLS policy. Do not add settings, modules or dependencies for them.

## 5. Acceptance criteria
1. `make install && make infra-up && make check` passes on a clean clone (macOS and Linux).
2. Overall coverage ≥ 95 %; `svoi_pravila.application` 100 % lines and branches.
3. All import-linter contracts active and green.
4. Log canary and Hypothesis redaction tests present and green.
5. `docker build` succeeds; container starts with `SP_*` env pointing to compose services and `/readyz` returns 200.
6. CI workflow is syntactically valid, actions SHA-pinned, all four jobs defined.
7. No file outside the scope of this prompt; no suppressions; no TODOs.

## 6. Report
Write `docs/reports/0001-repo-bootstrap.md` using the template from `.cursor/rules/00-executor-contract.mdc`. Include: exact versions of Python, every dependency, container images and pinned actions; full `make check` output summary; coverage numbers (overall and `application`); the output of `lint-imports`.
