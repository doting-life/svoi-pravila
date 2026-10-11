# backend/AGENTS.md — backend rules

Adds to the root `AGENTS.md`. Applies to everything under `backend/`.

## Architecture (hexagonal)
Reference: `docs/maintainers/02-architecture.md` — read only the sections the task cites.

Layers in `backend/src/svoi_pravila`: `domain` ← `application` ← `adapters/*`, `api` ← `bootstrap`.
- `domain`: entities, value objects, domain errors, pure policies. Standard library only. No Pydantic, SQLAlchemy, aiogram, FastAPI, httpx, logging frameworks, I/O, clocks or randomness (time and ids come through ports).
- `application`: use cases and ports (`typing.Protocol`). Imports `domain` only. Ports are injected through the constructor. One public `execute` entry per use case; input and output are immutable dataclasses.
- `adapters/<name>`: implement ports. Adapters never import each other (shared `observability` is allowed).
- `api` and `adapters/channels/*` call use cases only — never repositories, LLM clients or the database directly.
- `bootstrap.py` is the only composition root. No module-level singletons or global mutable state elsewhere.
- `config.py` is the only place that reads environment variables.
- Boundaries are enforced by `import-linter`; a broken contract is a failed gate. Never weaken a contract.

Conventions:
- Authorization (who may access a contact, pair or rule) lives in use cases, not in routers or handlers.
- Domain errors are explicit types; adapters translate them to transport responses. No string matching on error messages.
- Identifiers: UUIDv7 through the `IdGenerator` port. Time: timezone-aware UTC through the `Clock` port.
- User-facing strings live in the channel adapter's localization catalog, never inline.
- Every new persisted field is classified C0 (not personal), C1 (identifier) or C2 (special-category, always encrypted through `Cipher`). State it in the report.

## Python
- Python version pinned in `backend/.python-version`; package manager `uv` only (version pinned in `pyproject.toml`); commit `uv.lock`. Never use pip directly.
- Fully typed; `mypy --strict` passes. No `Any` in public signatures. Prefer `Protocol`, `TypedDict`, frozen `dataclass(slots=True)` in domain and application; Pydantic v2 only at the edges (config, API schemas, LLM output schemas).
- Async all the way for I/O; no blocking calls in async code; every network call has an explicit timeout.
- `ruff` lints and formats; its configuration in `backend/pyproject.toml` changes only through a task.
- SQLAlchemy 2.x typed ORM with `asyncpg`; sessions only through the Unit of Work adapter. Every schema change is an Alembic migration with a working downgrade. No raw SQL string interpolation.
- Settings via `pydantic-settings`; secrets typed `SecretStr`; never log or `repr` a secret.
- Logging via `structlog` through the project logger factory only; never `print`. Never log user text, LLM prompts or outputs, rule text, contact labels or Telegram message objects — log ids, counts, durations and enums.
- Catch the narrowest exception; re-raise or translate; never swallow; no bare `except`.
- Public functions and classes have concise docstrings about behaviour and invariants.

## Testing
- Layout: `tests/unit` (no I/O), `tests/integration` (real PostgreSQL and Valkey), `tests/contract` (adapters against recorded provider responses).
- Coverage: `domain` and `application` — 100 % lines and branches; overall — at least 95 %. Never lower a threshold.
- Unit tests use in-memory fakes of ports from `tests/fakes`, not mocks of internals. Mock only at true process boundaries.
- No network in unit or contract tests; LLM and Telegram behaviour through fakes or recorded fixtures with synthetic text only.
- Test behaviour, invariants and failure paths (timeouts, invalid LLM output, duplicate updates, unauthorized access). Coverage without assertions is a defect.
- Property-based tests (Hypothesis) for parsers, validators and crypto round-trips.
- Deterministic: fake `Clock`, fake `IdGenerator`, no sleeps.
- When touching privacy-sensitive code: a log canary test (a unique marker inside processed text never appears in captured logs) and authorization tests (owner, partner, stranger) for every endpoint or use case that reaches contacts, pairs or rules.
- LLM quality evals (`backend/evals`) are separate from the test suite and never call the provider from the default `pytest` run.

## Live LLM provider calls — hard budget
Every executor uses their own development key with a daily limit; every live call spends it.
- Allowed only when the task has a **"Live budget"** section naming models, operations, case subset, repeats and the maximum billable tokens for the whole task. Never otherwise: no exploratory calls, no "quick check", no re-running to recover from our own bug, no live calls while developing or debugging, never from `pytest`, `make check`, CI or any default target.
- Before spending: prove validators, metrics and failure accounting offline (recorded fixtures); run the tool with `--dry-run` (planned calls and worst-case estimate, no call); preflight with `--list-models`; always set `--max-tokens <budget>` — the tool stops before a call that could cross it and marks the output `INCOMPLETE`.
- While spending: smallest and most important segment first, one segment per command; prompt iteration on the named smoke subset with `--repeat 1`; stop at the first `auth` or `rate_limited`, never wait-and-retry. An `INCOMPLETE` result is reported as measured, never re-run without a new budget.
- Report every live run: the command, the `--out` file verbatim, the billable tokens actually spent, and the task total.
