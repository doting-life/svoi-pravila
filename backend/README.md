# Backend

Python package `svoi-pravila` (FastAPI API, use cases, adapters).

## LLM models (GigaChat)

Benchmark date: **2026-10-04**. Dataset: `benchmarks/data/bench_v2.jsonl` (22 synthetic Russian cases × soften/help_say/decode; `decode_stream` reuses decode rows; `--repeat 3`). Calls are sequential; one unmeasured warm-up per model. Price table date: **2026-10-03** (₽ / 1k tokens). Billable cost excludes cached/precached tokens ([counting tokens](https://developers.sber.ru/docs/ru/gigachat/guides/counting-tokens)).

The development GigaChat key is **token-limited**. Live runs require an explicit budget (`--max-tokens`) after an offline `--dry-run`. See `.cursor/rules/60-live-provider-budget.mdc`.

Targets (quality among schema-eligible calls; availability reported separately; false-non-ok ≤5%):

| Operation | Schema after ≤1 retry | First attempt | Latency (ok) |
|---|---|---|---|
| soften / help_say | ≥95% | ≥90% | p50 ≤1500 ms, p95 ≤2500 ms |
| decode stream | ≥95% | ≥90% | TTFC p50 ≤1500 ms, total p95 ≤10000 ms |

First-attempt success means every phase succeeded on its first try: `attempts == 1` for soften/help_say, `attempts == 2` for two-phase `decode_stream`.

### Selection (2026-10-04)

From live segments `0005d_lightning_soft.md` and `0005d_2pro_decode.md` (no new provider calls in 0005-e):

| Setting | Model | Rationale |
|---|---|---|
| `SP_GIGACHAT_MODEL_SOFTEN` | `GigaChat-3-Lightning` | Schema 92/95, latency within targets; false-non-ok 0%. |
| `SP_GIGACHAT_MODEL_HELP_SAY` | `GigaChat-3-Lightning` | Schema 95/98, latency within targets; false-non-ok 0%. |
| `SP_GIGACHAT_MODEL_DECODE` | `GigaChat-2-Pro` | `decode_stream` schema after retry 100%, TTFC p50 1.3 s, total p95 8.2 s, false-non-ok 0%. First-attempt is **100%**: mean attempts exactly 2.00 with 100% success ⇒ every phase succeeded on its first try (the earlier 0% figure was a benchmark bug treating first-attempt as `attempts == 1`). |

Run:

```bash
# Offline plan (no network): call counts + token estimate
make bench-llm BENCH_ARGS='--dry-run --models GigaChat-3-Lightning --ops decode_stream --smoke --repeat 1'

# Live run (requires --max-tokens; stop before exceeding budget)
make bench-llm BENCH_ARGS='--models GigaChat-3-Lightning --ops soften --smoke --repeat 1 --max-tokens 50000 --out /tmp/bench.md'

make bench-llm BENCH_ARGS='--list-models'
```

Token estimate heuristic (dry-run / pre-call budget): `ceil(rendered system+user characters / 3)` plus a per-operation output cap; worst-case retries are counted (2 attempts per structured phase; both phases for `decode_stream`).

`--smoke` runs only cases with `"smoke": true` (6 per operation). `--cases <id>...` selects by id.

`--record-fixtures <dir>` writes sanitized HTTP fixtures via httpx event hooks. Streamed responses are buffered for the fixture file, so latency and TTFC from a recording run are **not** valid measurements.

## Telegram (local)

Local delivery uses **polling**. Webhook mode is for VPS/staging/production (task 0023).

1. Create a test bot in BotFather; enable inline mode later (0007). Put the token only in local `.env` as `SP_TELEGRAM_BOT_TOKEN` — never commit it.
2. Ensure `SP_TELEGRAM_UPDATES_MODE=polling`, `SP_ENVIRONMENT=local`, and run `make dev-env` so `SP_PSEUDONYM_PEPPER` is generated.
3. Start the stack: `make up`.
4. Open the bot in Telegram and send `/start`.
5. Confirm «Мне есть 18», then accept both consent screens.
6. Read the completion notice (what we store / don’t store, «via @bot», commands).
7. Send `/help` and a plain text message — both should show the help text for this build (`/start`, `/help` only).

Without a bot token, automated tests still cover the channel; manual smoke is skipped.
