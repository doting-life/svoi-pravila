"""Percentiles, cost estimates, and markdown benchmark tables (C0 only)."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from svoi_pravila.config import Settings

if TYPE_CHECKING:
    from svoi_pravila.benchmarks.runner import CallRecord

# Prices ₽ / 1k tokens — legal tariffs snapshot date 2026-10-03.
# Longest matching key wins so "GigaChat" does not swallow Pro/Max/3-*.
_PRICE_PER_1K: dict[str, float] = {
    "GigaChat-2-Max": 0.65,
    "GigaChat-2-Pro": 0.5,
    "GigaChat-3-Ultra": 0.65,
    "GigaChat-3-Pro": 0.5,
    "GigaChat-3-Lightning": 0.065,
    "GigaChat-Max": 0.65,
    "GigaChat-Pro": 0.5,
    "GigaChat-2": 0.065,
    "GigaChat": 0.065,
}
PRICE_TABLE_DATE = "2026-10-03"


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile; 0.0 if empty."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = round((pct / 100) * (len(ordered) - 1))
    index = min(len(ordered) - 1, max(0, rank))
    return ordered[index]


def price_for(model: str) -> float:
    """₽ per 1k tokens for a model id (longest key match)."""
    lowered = model.lower()
    matches = [(key, price) for key, price in _PRICE_PER_1K.items() if key.lower() in lowered]
    if not matches:
        return _PRICE_PER_1K["GigaChat-2"]
    _key, price = max(matches, key=lambda item: len(item[0]))
    return price


def default_models(settings: Settings) -> list[str]:
    """Unique models from settings, soften then help_say then decode."""
    return list(
        dict.fromkeys(
            [
                settings.gigachat_model_soften,
                settings.gigachat_model_help_say,
                settings.gigachat_model_decode,
            ]
        )
    )


def phase_count(operation: str) -> int:
    """Number of provider phases in one successful call for an operation."""
    return 2 if operation == "decode_stream" else 1


def format_reasons_line(reason_counts: dict[str, int]) -> str:
    """C0 reasons breakdown line for ``--out``."""
    if not reason_counts:
        return "reasons: (none)"
    parts = ",".join(f"{name}:{count}" for name, count in sorted(reason_counts.items()))
    return f"reasons: {parts}"


def _quality_metrics(
    records: list[CallRecord],
    *,
    operation: str,
) -> tuple[float, float, float, dict[str, int], str]:
    n = len(records)
    ok = [r for r in records if r.outcome == "ok"]
    invalid = [r for r in records if r.outcome == "invalid_output"]
    refused = [r for r in records if r.outcome == "refused"]
    unavailable = [r for r in records if r.outcome == "unavailable"]
    quality_n = len(ok) + len(invalid)
    schema_after = (100.0 * len(ok) / quality_n) if quality_n else 0.0
    needed = phase_count(operation)
    first_ok = [r for r in ok if r.attempts == needed]
    schema_first = (100.0 * len(first_ok) / quality_n) if quality_n else 0.0
    refuse_pct = (100.0 * len(refused) / n) if n else 0.0
    kinds = Counter(r.unavailable_kind or "unknown" for r in unavailable)
    unavail_parts = ",".join(f"{k}:{100.0 * c / n:.0f}" for k, c in sorted(kinds.items())) or "0"
    reason_counts = Counter(reason for rec in invalid for reason in rec.reasons)
    return schema_first, schema_after, refuse_pct, dict(reason_counts), unavail_parts


def _safety_metrics(records: list[CallRecord]) -> tuple[str, float]:
    """Return safety histogram and false-non-ok rate (%)."""
    ok_records = [r for r in records if r.outcome == "ok" and r.actual_safety is not None]
    counts = Counter(r.actual_safety for r in ok_records)
    safety_parts = "/".join(
        f"{label}:{counts.get(label, 0)}" for label in ("ok", "crisis", "refuse_manipulation")
    )
    expected_ok_success = [
        r for r in ok_records if r.expected_safety == "ok" and r.actual_safety is not None
    ]
    false_non_ok = sum(1 for r in expected_ok_success if r.actual_safety != "ok")
    false_rate = (100.0 * false_non_ok / len(expected_ok_success)) if expected_ok_success else 0.0
    return safety_parts or "ok:0/crisis:0/refuse_manipulation:0", false_rate


def _token_cost(records: list[CallRecord], model: str) -> tuple[float, float, float, float]:
    n = len(records)
    mean_attempts = (sum(r.attempts for r in records) / n) if n else 0.0
    mean_in = (sum(r.input_tokens for r in records) / n) if n else 0.0
    mean_out = (sum(r.output_tokens for r in records) / n) if n else 0.0
    # Billable = prompt (after cache) + completion; see TokenUsage.billable.
    # https://developers.sber.ru/docs/ru/gigachat/guides/counting-tokens
    billable = sum(r.billable_tokens for r in records)
    cost = (billable / 1000.0) * price_for(model)
    return mean_attempts, mean_in, mean_out, cost


def aggregate_standard_row(
    records: list[CallRecord], *, operation: str, model: str
) -> tuple[str, dict[str, int]]:
    """Return one markdown row for soften or help_say."""
    n = len(records)
    ok = [r for r in records if r.outcome == "ok"]
    ok_lat = [r.latency_ms for r in ok]
    all_lat = [r.latency_ms for r in records]
    schema_first, schema_after, refuse_pct, reason_counts, unavail_parts = _quality_metrics(
        records, operation=operation
    )
    safety_parts, false_rate = _safety_metrics(records)
    mean_attempts, mean_in, mean_out, cost = _token_cost(records, model)
    ok_p50 = percentile(ok_lat, 50)
    ok_p95 = percentile(ok_lat, 95)
    all_p50 = percentile(all_lat, 50)
    schema = f"{schema_first:.0f}/{schema_after:.0f}"
    tokens = f"{mean_in:.0f}/{mean_out:.0f}"
    row = (
        f"| {operation} | {model} | {n} | {ok_p50:.0f} | {ok_p95:.0f} | {all_p50:.0f} | "
        f"{schema} | {refuse_pct:.0f} | {unavail_parts} | {safety_parts} | {false_rate:.0f} | "
        f"{mean_attempts:.2f} | {tokens} | {cost:.3f} |"
    )
    return row, reason_counts


def aggregate_decode_stream_row(
    records: list[CallRecord], *, model: str
) -> tuple[str, dict[str, int]]:
    """Return one markdown row for the two-phase decode_stream operation."""
    n = len(records)
    ok = [r for r in records if r.outcome == "ok"]
    ttfc = [r.ttfc_ms for r in ok if r.ttfc_ms is not None]
    phase_a = [r.phase_a_ms for r in ok if r.phase_a_ms is not None]
    phase_b = [r.phase_b_ms for r in ok if r.phase_b_ms is not None]
    total = [r.latency_ms for r in ok]
    schema_first, schema_after, refuse_pct, reason_counts, unavail_parts = _quality_metrics(
        records, operation="decode_stream"
    )
    safety_parts, false_rate = _safety_metrics(records)
    mean_attempts, mean_in, mean_out, cost = _token_cost(records, model)
    ttfc_p50 = percentile(ttfc, 50)
    ttfc_p95 = percentile(ttfc, 95)
    phase_a_p50 = percentile(phase_a, 50)
    phase_b_p50 = percentile(phase_b, 50)
    total_p50 = percentile(total, 50)
    total_p95 = percentile(total, 95)
    schema = f"{schema_first:.0f}/{schema_after:.0f}"
    tokens = f"{mean_in:.0f}/{mean_out:.0f}"
    row = (
        f"| decode_stream | {model} | {n} | {ttfc_p50:.0f} | {ttfc_p95:.0f} | "
        f"{phase_a_p50:.0f} | {phase_b_p50:.0f} | {total_p50:.0f} | {total_p95:.0f} | "
        f"{schema} | {refuse_pct:.0f} | {unavail_parts} | {safety_parts} | {false_rate:.0f} | "
        f"{mean_attempts:.2f} | {tokens} | {cost:.3f} |"
    )
    return row, reason_counts


def aggregate_row(
    records: list[CallRecord], *, operation: str, model: str
) -> tuple[str, dict[str, int]]:
    """Dispatch to the correct table row builder."""
    if operation == "decode_stream":
        return aggregate_decode_stream_row(records, model=model)
    return aggregate_standard_row(records, operation=operation, model=model)


def standard_report_header() -> str:
    """Markdown header for soften and help_say rows."""
    return (
        "| operation | model | n | ok-p50 | ok-p95 | all-p50 | "
        "schema% first/after | refuse% | unavail% by kind | safety ok/crisis/refuse | "
        "false-non-ok% | mean att | tok in/out | est. ₽ |\n"
        "|---|---|---:|---:|---:|---:|---:|---:|---|---|---:|---:|---:|---:|"
    )


def decode_stream_report_header() -> str:
    """Markdown header for decode_stream rows."""
    return (
        "| operation | model | n | ttfc-p50 | ttfc-p95 | phase-a-p50 | phase-b-p50 | "
        "total-p50 | total-p95 | schema% first/after | refuse% | unavail% by kind | "
        "safety ok/crisis/refuse | false-non-ok% | mean att | tok in/out | est. ₽ |\n"
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---:|---:|---:|---:|"
    )


def format_report(rows: list[str], *, incomplete: bool) -> str:
    """Join table sections with an optional INCOMPLETE banner."""
    parts: list[str] = []
    if incomplete:
        parts.append("**INCOMPLETE** — stopped after first `rate_limited`")
    parts.extend(rows)
    return "\n".join(parts)
