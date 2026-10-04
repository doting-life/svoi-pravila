"""Offline tests for the safety-eval harness (no live provider calls)."""

from __future__ import annotations

from argparse import Namespace
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest
from tests.factories import make_settings
from tests.fakes.generation import FakeTextGenerator

from svoi_pravila.application.crisis_screen import CrisisScreen
from svoi_pravila.application.errors import (
    GenerationRefusedByProvider,
    GenerationUnavailable,
    InvalidGenerationOutput,
    InvalidOutputReason,
    UnavailableKind,
)
from svoi_pravila.application.ports.generation import TokenUsage
from svoi_pravila.benchmarks.out_writer import OutWriter
from svoi_pravila.benchmarks.runner import (
    AuthFailedError,
    CallRecord,
    RateLimitedError,
    SpendTracker,
    TokenBudgetExceededError,
)
from svoi_pravila.evals.cases import (
    EvalCase,
    cases_for_operation,
    filter_cases,
    load_cases,
    order_cases_for_run,
    parse_case,
    with_deadline,
)
from svoi_pravila.evals.cli import _parse_ops, _write_incomplete, async_main, main
from svoi_pravila.evals.estimate import (
    estimate_eval_case_tokens,
    largest_eval_call_estimate,
    plan_eval_calls,
)
from svoi_pravila.evals.metrics import (
    EvalRecord,
    crisis_recall,
    false_crisis_rate,
    false_refusal_rate,
    format_metrics,
    incoming_manipulation_schema_validity,
    leak_count,
    manipulation_refusal_rate,
    schema_validity,
    verdict_matches,
)
from svoi_pravila.evals.runner import (
    EvalParams,
    EvalRuntime,
    eval_exit_code,
    run_eval,
    run_eval_case,
    warmup_eval,
    write_eval_out,
)

_DATA = Path(__file__).resolve().parents[3] / "evals" / "data" / "eval_v1.jsonl"


def _record(**overrides: object) -> EvalRecord:
    base = EvalRecord(
        case_id="x",
        operation="soften",
        category="ordinary_conflict",
        expected=frozenset({"ok"}),
        outcome="ok",
        verdict="ok",
        screen_hit=False,
        reasons=(),
        billable_tokens=0,
        valid=True,
    )
    payload: dict[str, Any] = asdict(base)
    payload.update(overrides)
    raw_expected = payload["expected"]
    if isinstance(raw_expected, str):
        payload["expected"] = frozenset({raw_expected})
    elif isinstance(raw_expected, set):
        payload["expected"] = frozenset(raw_expected)
    return EvalRecord(**payload)


@pytest.mark.unit
def test_eval_dataset_shape() -> None:
    cases = load_cases(_DATA)
    assert len(cases) >= 54
    ops = ("soften", "help_say", "decode_stream")
    by_op = {op: [c for c in cases if c.operation == op] for op in ops}
    for op, rows in by_op.items():
        assert len(rows) >= 18, op
    assert sum(1 for row in by_op["soften"] if row.smoke) == 8
    assert sum(1 for row in by_op["help_say"] if row.smoke) == 8
    assert sum(1 for row in by_op["decode_stream"] if row.smoke) == 0
    smoke = filter_cases(cases, smoke=True)
    assert len(smoke) == 16
    incoming = [c for c in cases if c.category == "incoming_manipulation"]
    assert incoming
    assert all(c.operation == "decode_stream" and c.expected == frozenset({"ok"}) for c in incoming)
    assert all(
        c.expected == frozenset({"refuse_manipulation"})
        for c in cases
        if c.category == "manipulation"
    )
    coercive = [c for c in cases if c.id.endswith("heated-12")]
    assert {c.id for c in coercive} == {
        "soften-heated-12",
        "help_say-heated-12",
        "decode_stream-heated-12",
    }
    assert all(c.category == "coercive_threat" for c in coercive)
    accepted = frozenset({"crisis", "refuse_manipulation"})
    assert all(c.expected == accepted for c in coercive)
    with pytest.raises(ValueError, match="unknown case ids"):
        filter_cases(cases, case_ids=("nope",))


@pytest.mark.unit
def test_parse_case_rejects_bad_payload() -> None:
    with pytest.raises(TypeError):
        parse_case([])
    with pytest.raises(ValueError, match="case id"):
        parse_case({"id": "", "operation": "soften"})
    with pytest.raises(ValueError, match="unknown operation"):
        parse_case({"id": "a", "operation": "nope", "category": "heated", "expected": "ok"})


@pytest.mark.unit
def test_metrics_and_leak_exit() -> None:
    records = [
        _record(expected="crisis", category="explicit_crisis", verdict="crisis"),
        _record(expected="crisis", category="explicit_crisis", verdict="ok"),
        _record(category="ordinary_conflict", verdict="ok"),
        _record(category="mild_distress", verdict="crisis"),
        _record(
            expected="refuse_manipulation",
            category="manipulation",
            verdict="refuse_manipulation",
        ),
        _record(category="ordinary_conflict", verdict="refuse_manipulation"),
        _record(
            reasons=("prompt_leak",),
            valid=False,
            outcome="invalid_output",
            verdict=None,
            category="prompt_injection",
        ),
        _record(
            operation="decode_stream",
            category="incoming_manipulation",
            expected="ok",
            verdict="ok",
        ),
        _record(
            operation="decode_stream",
            category="incoming_manipulation",
            expected="ok",
            valid=False,
            outcome="invalid_output",
            verdict=None,
        ),
        _record(
            operation="decode_stream",
            category="manipulation",
            expected="refuse_manipulation",
            verdict="ok",
        ),
        _record(category="heated", verdict="ok"),
        _record(category="hyperbole", verdict="ok"),
    ]
    assert crisis_recall(records) == 0.5
    assert false_crisis_rate(records) == pytest.approx(1 / 6)
    assert manipulation_refusal_rate(records) == 1.0
    assert false_refusal_rate(records) == pytest.approx(1 / 5)
    assert incoming_manipulation_schema_validity(records) == 0.5
    assert leak_count(records) == 1
    assert eval_exit_code(records, incomplete=False) == 1
    rendered = format_metrics(records)
    assert "crisis_recall" in rendered
    assert "incoming_manipulation_schema_validity: 0.500" in rendered
    dual = _record(
        category="coercive_threat",
        expected=frozenset({"crisis", "refuse_manipulation"}),
        verdict="crisis",
    )
    assert verdict_matches(dual)
    assert not verdict_matches(_record(verdict="crisis", expected=frozenset({"ok"})))


@pytest.mark.unit
def test_empty_rates_print_na() -> None:
    empty = format_metrics([])
    assert "crisis_recall: n/a" in empty
    assert "false_crisis: n/a" in empty
    assert "manipulation_refusal: n/a" in empty
    assert "false_refusal: n/a" in empty
    assert "schema_validity: n/a" in empty
    assert "incoming_manipulation_schema_validity: n/a" in empty
    assert "per_operation_validity: (none)" in empty
    assert schema_validity([]) is None
    ordinary = [_record(category="ordinary_conflict", verdict="ok")]
    rendered = format_metrics(ordinary)
    assert "incoming_manipulation_schema_validity: n/a" in rendered
    assert incoming_manipulation_schema_validity(ordinary) is None


@pytest.mark.unit
async def test_run_eval_case_screen_skips_generator() -> None:
    cases = [c for c in load_cases(_DATA) if c.category == "explicit_crisis"]
    gen = FakeTextGenerator()
    runtime = EvalRuntime(OutWriter(None), SpendTracker(), CrisisScreen.load_ru_v2())
    record = await run_eval_case(gen, cases[0], runtime, deadline=5.0, show_outputs=False)
    assert record.screen_hit is True
    assert record.outcome == "screened"
    assert record.verdict == "crisis"
    assert gen.soften_calls == []
    assert gen.help_say_calls == []
    assert gen.decode_stream_calls == []


@pytest.mark.unit
async def test_run_eval_writes_out_without_generated_text(tmp_path: Path) -> None:
    ordinary = next(c for c in load_cases(_DATA) if c.id == "soften-ordinary_conflict-01")
    gen = FakeTextGenerator()
    out_path = tmp_path / "eval.md"
    runtime = EvalRuntime(OutWriter(out_path), SpendTracker(), CrisisScreen.load_ru_v2())
    params = EvalParams(
        operations=("soften",),
        models=("fake",),
        deadline=5.0,
        show_outputs=True,
        max_tokens=40000,
    )
    records, incomplete, rate_error, budget_error = await run_eval(gen, [ordinary], params, runtime)
    assert incomplete is False
    assert rate_error is None
    assert budget_error is None
    assert records[0].screen_hit is False
    text = out_path.read_text(encoding="utf-8")
    assert "softened-a" not in text
    assert ordinary.id in text
    assert "crisis_recall" in text


@pytest.mark.unit
async def test_run_eval_groups_by_ops_then_category_priority() -> None:
    cases = load_cases(_DATA)
    hyperbole = next(c for c in cases if c.id == "soften-hyperbole-19")
    decode = next(c for c in cases if c.id == "decode_stream-ordinary_conflict-01")
    runtime = EvalRuntime(OutWriter(None), SpendTracker(), CrisisScreen.load_ru_v2())
    params = EvalParams(
        operations=("soften", "decode_stream"),
        models=("fake", "fake"),
        deadline=5.0,
        show_outputs=False,
        max_tokens=40000,
    )
    records, incomplete, rate_error, budget_error = await run_eval(
        FakeTextGenerator(),
        [decode, hyperbole],
        params,
        runtime,
    )
    assert incomplete is False
    assert rate_error is None
    assert budget_error is None
    assert [row.case_id for row in records] == [hyperbole.id, decode.id]
    shuffled = [
        next(c for c in cases if c.id == "soften-ordinary_conflict-01"),
        next(c for c in cases if c.id == "soften-heated-12"),
        next(c for c in cases if c.id == "soften-manipulation-13"),
        next(c for c in cases if c.id == "soften-hyperbole-19"),
    ]
    ordered = order_cases_for_run(shuffled, ("soften",))
    assert [c.id for c in ordered] == [
        "soften-hyperbole-19",
        "soften-manipulation-13",
        "soften-heated-12",
        "soften-ordinary_conflict-01",
    ]


@pytest.mark.unit
async def test_invalid_and_unavailable_records() -> None:
    ordinary = next(
        c for c in load_cases(_DATA) if c.operation == "soften" and c.category == "heated"
    )
    runtime = EvalRuntime(OutWriter(None), SpendTracker(), CrisisScreen.load_ru_v2())
    invalid = FakeTextGenerator()
    invalid.soften_error = InvalidGenerationOutput(
        (InvalidOutputReason.PROMPT_LEAK,),
        usage=TokenUsage(output=3),
        attempts=2,
        model="m",
        prompt_version="p",
    )
    rec = await run_eval_case(invalid, ordinary, runtime, deadline=5.0, show_outputs=False)
    assert rec.outcome == "invalid_output"
    assert "prompt_leak" in rec.reasons
    un = FakeTextGenerator()
    un.soften_error = GenerationUnavailable(
        UnavailableKind.TIMEOUT, usage=TokenUsage(), attempts=1, model="m", prompt_version="p"
    )
    rec2 = await run_eval_case(un, ordinary, runtime, deadline=5.0, show_outputs=False)
    assert rec2.outcome == "unavailable"


@pytest.mark.unit
async def test_dry_run_makes_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("svoi_pravila.evals.cli.LlmToolSettings", make_settings)
    called = {"n": 0}

    def boom(_settings: object) -> None:
        called["n"] += 1
        raise AssertionError("network")

    monkeypatch.setattr("svoi_pravila.evals.cli.create_gigachat_client", boom)
    code = await async_main(
        Namespace(
            data=str(_DATA),
            ops=None,
            deadline=5.0,
            out=None,
            show_outputs=False,
            dry_run=True,
            max_tokens=40000,
            smoke=True,
            cases=None,
        )
    )
    assert code == 0
    assert called["n"] == 0


@pytest.mark.unit
async def test_max_tokens_required_for_live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("svoi_pravila.evals.cli.LlmToolSettings", make_settings)
    with pytest.raises(SystemExit, match="max-tokens"):
        await async_main(
            Namespace(
                data=str(_DATA),
                ops=["soften"],
                deadline=5.0,
                out=None,
                show_outputs=False,
                dry_run=False,
                max_tokens=None,
                smoke=True,
                cases=None,
            )
        )


@pytest.mark.unit
def test_estimate_crisis_is_zero() -> None:
    screen = CrisisScreen.load_ru_v2()
    crisis = next(c for c in load_cases(_DATA) if c.category == "explicit_crisis")
    ordinary = next(c for c in load_cases(_DATA) if c.category == "ordinary_conflict")
    assert estimate_eval_case_tokens(crisis, screen) == 0
    assert estimate_eval_case_tokens(ordinary, screen) > 0
    rows = plan_eval_calls(
        [crisis, ordinary],
        operations=("soften",),
        models=("fake",),
        screen=screen,
    )
    assert rows[0][2] >= 1
    threat = next(c for c in load_cases(_DATA) if c.id.endswith("heated-12"))
    assert estimate_eval_case_tokens(threat, screen) > 0
    assert largest_eval_call_estimate([], screen) == 0


@pytest.mark.unit
def test_show_outputs_not_in_out_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = OutWriter(tmp_path / "o.md")
    write_eval_out(
        out,
        [
            _record(case_id="shown", verdict="ok"),
        ],
    )
    captured = capsys.readouterr()
    assert "shown" not in captured.err
    assert "secret-variant" not in (tmp_path / "o.md").read_text(encoding="utf-8")


@pytest.mark.unit
async def test_help_say_decode_and_warmup() -> None:
    cases = load_cases(_DATA)
    help_case = next(c for c in cases if c.operation == "help_say" and c.category == "heated")
    decode_case = next(
        c for c in cases if c.operation == "decode_stream" and c.category == "heated"
    )
    gen = FakeTextGenerator()
    runtime = EvalRuntime(OutWriter(None), SpendTracker(), CrisisScreen.load_ru_v2())
    help_rec = await run_eval_case(gen, help_case, runtime, deadline=5.0, show_outputs=False)
    decode_rec = await run_eval_case(gen, decode_case, runtime, deadline=5.0, show_outputs=False)
    assert help_rec.verdict == "ok"
    assert decode_rec.verdict == "ok"
    params = EvalParams(
        operations=("soften", "help_say"),
        models=("fake", "fake"),
        deadline=5.0,
        show_outputs=False,
        max_tokens=40000,
    )
    live = [c for c in cases if c.operation == "soften" and c.category == "heated"]
    await warmup_eval(gen, live, params, runtime)


@pytest.mark.unit
def test_cli_unknown_operation() -> None:
    with pytest.raises(SystemExit, match="unknown operations"):
        _parse_ops(["nope"])
    assert _parse_ops(["soften", "soften"]) == ("soften",)


@pytest.mark.unit
def test_parse_and_load_case_errors(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown category"):
        parse_case({"id": "a", "operation": "soften", "category": "nope", "expected": "ok"})
    with pytest.raises(ValueError, match="expected must be"):
        parse_case(
            {
                "id": "a",
                "operation": "soften",
                "category": "heated",
                "expected": "nope",
            }
        )
    with pytest.raises(ValueError, match="expected must be"):
        parse_case(
            {
                "id": "a",
                "operation": "soften",
                "category": "heated",
                "expected": [],
            }
        )
    with pytest.raises(TypeError, match="expected must be"):
        parse_case(
            {
                "id": "a",
                "operation": "soften",
                "category": "heated",
                "expected": 1,
            }
        )
    with pytest.raises(TypeError, match="rules must be a list"):
        parse_case(
            {
                "id": "a",
                "operation": "soften",
                "category": "heated",
                "expected": "ok",
                "draft": "x",
                "rules": "nope",
            }
        )
    with pytest.raises(TypeError, match="each rule must be"):
        parse_case(
            {
                "id": "a",
                "operation": "soften",
                "category": "heated",
                "expected": "ok",
                "draft": "x",
                "rules": ["nope"],
            }
        )
    parsed = parse_case(
        {
            "id": "with-rule",
            "operation": "soften",
            "category": "heated",
            "expected": "ok",
            "draft": "черновик",
            "rules": [
                {
                    "category": "other",
                    "text": "спокойно",
                    "effective_since": "2026-01-01T00:00:00+00:00",
                }
            ],
        }
    )
    assert parsed.soften is not None
    assert len(parsed.soften.rules) == 1
    empty = tmp_path / "empty.jsonl"
    empty.write_text("\n", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        load_cases(empty)
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid case"):
        load_cases(bad)


@pytest.mark.unit
def test_case_helpers_cover_gaps() -> None:
    cases = load_cases(_DATA)
    assert cases_for_operation(cases, "soften")
    smoke_ids = tuple(c.id for c in cases if c.smoke)[:1]
    filtered = filter_cases(cases, smoke=True, case_ids=smoke_ids)
    assert len(filtered) == 1
    empty = EvalCase(
        id="bare",
        operation="soften",
        category="heated",
        expected=frozenset({"ok"}),
    )
    with pytest.raises(ValueError, match="no request payload"):
        empty.screen_text()
    with pytest.raises(ValueError, match="no request payload"):
        with_deadline(empty, 2.0)
    decode = next(c for c in cases if c.operation == "decode_stream")
    timed = with_deadline(decode, 9.0)
    assert timed.decode is not None
    assert timed.decode.deadline_seconds == 9.0


@pytest.mark.unit
async def test_runner_error_and_budget_paths() -> None:
    ordinary = next(c for c in load_cases(_DATA) if c.id == "soften-ordinary_conflict-01")
    runtime = EvalRuntime(OutWriter(None), SpendTracker(), CrisisScreen.load_ru_v2())
    refused = FakeTextGenerator()
    refused.soften_error = GenerationRefusedByProvider(
        usage=TokenUsage(output=1),
        attempts=1,
        model="m",
        prompt_version="p",
    )
    rec = await run_eval_case(refused, ordinary, runtime, deadline=5.0, show_outputs=True)
    assert rec.outcome == "refused"
    limited = FakeTextGenerator()
    limited.soften_error = GenerationUnavailable(
        UnavailableKind.RATE_LIMITED,
        usage=TokenUsage(),
        attempts=1,
        model="m",
        prompt_version="p",
    )
    with pytest.raises(RateLimitedError):
        await run_eval_case(limited, ordinary, runtime, deadline=5.0, show_outputs=False)
    authed = FakeTextGenerator()
    authed.soften_error = GenerationUnavailable(
        UnavailableKind.AUTH,
        usage=TokenUsage(),
        attempts=1,
        model="m",
        prompt_version="p",
    )
    with pytest.raises(AuthFailedError):
        await run_eval_case(authed, ordinary, runtime, deadline=5.0, show_outputs=False)
    params = EvalParams(
        operations=("soften",),
        models=("fake",),
        deadline=5.0,
        show_outputs=False,
        max_tokens=0,
    )
    _records, incomplete, _rate, budget = await run_eval(
        FakeTextGenerator(), [ordinary], params, runtime
    )
    assert incomplete is True
    assert budget is not None
    limited_params = EvalParams(
        operations=("soften",),
        models=("fake",),
        deadline=5.0,
        show_outputs=False,
        max_tokens=40000,
    )
    _recs, limited_incomplete, rate_error, _b = await run_eval(
        limited, [ordinary], limited_params, runtime
    )
    assert limited_incomplete is True
    assert rate_error is not None
    second = next(c for c in load_cases(_DATA) if c.id == "soften-ordinary_conflict-02")
    _auth_recs, auth_incomplete, auth_error, _ab = await run_eval(
        authed, [ordinary, second], limited_params, runtime
    )
    assert auth_incomplete is True
    assert isinstance(auth_error, AuthFailedError)
    assert len(_auth_recs) == 0
    with pytest.raises(ValueError, match="align"):
        await warmup_eval(
            FakeTextGenerator(),
            [ordinary],
            EvalParams(
                operations=("soften", "help_say"),
                models=("fake",),
                deadline=5.0,
                show_outputs=False,
                max_tokens=40000,
            ),
            runtime,
        )
    crisis_only = [c for c in load_cases(_DATA) if c.category == "explicit_crisis"][:1]
    with pytest.raises(ValueError, match="no live cases"):
        await warmup_eval(
            FakeTextGenerator(),
            crisis_only,
            EvalParams(
                operations=(crisis_only[0].operation,),
                models=("fake",),
                deadline=5.0,
                show_outputs=False,
                max_tokens=40000,
            ),
            runtime,
        )
    assert eval_exit_code([], incomplete=True) == 1


@pytest.mark.unit
async def test_decode_without_completed_and_missing_request() -> None:
    decode = next(c for c in load_cases(_DATA) if c.operation == "decode_stream")
    runtime = EvalRuntime(OutWriter(None), SpendTracker(), CrisisScreen.load_ru_v2())
    empty = FakeTextGenerator()
    empty.emit_completed = False
    with pytest.raises(RuntimeError, match="no completed"):
        await run_eval_case(empty, decode, runtime, deadline=5.0, show_outputs=False)
    ordinary = next(c for c in load_cases(_DATA) if c.id == "soften-ordinary_conflict-01")
    mismatched = EvalCase(
        id="bare",
        operation="decode_stream",
        category="heated",
        expected=frozenset({"ok"}),
        soften=ordinary.soften,
    )
    with pytest.raises(ValueError, match="missing request"):
        await run_eval_case(
            FakeTextGenerator(), mismatched, runtime, deadline=5.0, show_outputs=False
        )


@pytest.mark.unit
def test_plan_eval_align_and_empty_operation() -> None:
    screen = CrisisScreen.load_ru_v2()
    ordinary = next(c for c in load_cases(_DATA) if c.category == "ordinary_conflict")
    with pytest.raises(ValueError, match="align"):
        plan_eval_calls([ordinary], operations=("soften",), models=[], screen=screen)
    rows = plan_eval_calls(
        [ordinary],
        operations=("help_say",),
        models=("fake",),
        screen=screen,
    )
    assert rows == []
    crisis = next(c for c in load_cases(_DATA) if c.category == "explicit_crisis")
    crisis_rows = plan_eval_calls(
        [crisis],
        operations=(crisis.operation,),
        models=("fake",),
        screen=screen,
    )
    assert crisis_rows[0][3] == 0


@pytest.mark.unit
def test_write_incomplete_markers(tmp_path: Path) -> None:
    out = OutWriter(tmp_path / "inc.md")
    _write_incomplete(out, incomplete=False, fail_fast=None, budget_error=None)
    budget = TokenBudgetExceededError(spent=1, limit=2)
    _write_incomplete(out, incomplete=True, fail_fast=None, budget_error=budget)
    rate = RateLimitedError(
        CallRecord(
            outcome="unavailable",
            latency_ms=0,
            expected_safety="ok",
            attempts=1,
            reasons=(),
            unavailable_kind="rate_limited",
            input_tokens=0,
            output_tokens=0,
            billable_tokens=0,
        ),
        http_status=429,
        rate_limit_headers=(("retry-after", "1"),),
    )
    _write_incomplete(out, incomplete=True, fail_fast=rate, budget_error=None)
    auth = AuthFailedError(
        CallRecord(
            outcome="unavailable",
            latency_ms=0,
            expected_safety="ok",
            attempts=1,
            reasons=(),
            unavailable_kind="auth",
            input_tokens=0,
            output_tokens=0,
            billable_tokens=0,
        )
    )
    _write_incomplete(out, incomplete=True, fail_fast=auth, budget_error=None)
    text = (tmp_path / "inc.md").read_text(encoding="utf-8")
    assert "INCOMPLETE" in text
    assert "429" in text
    assert "auth failed" in text


@pytest.mark.unit
async def test_async_main_live_and_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("svoi_pravila.evals.cli.LlmToolSettings", make_settings)

    async def _close(_client: object) -> None:
        return None

    async def _uninstall() -> None:
        return None

    monkeypatch.setattr("svoi_pravila.evals.cli.create_gigachat_client", lambda _s: object())
    monkeypatch.setattr("svoi_pravila.evals.cli.close_gigachat_client", _close)
    monkeypatch.setattr(
        "svoi_pravila.evals.cli.install_rate_limit_capture",
        lambda _c, _cap: _uninstall,
    )
    monkeypatch.setattr(
        "svoi_pravila.evals.cli.GigaChatTextGenerator",
        lambda _c, _s: FakeTextGenerator(),
    )
    out_path = tmp_path / "live.md"
    ns = Namespace(
        data=str(_DATA),
        ops=["soften"],
        deadline=5.0,
        out=str(out_path),
        show_outputs=False,
        dry_run=False,
        max_tokens=40000,
        smoke=True,
        cases=["soften-ordinary_conflict-01"],
    )
    code = await async_main(ns)
    assert code == 0
    assert "crisis_recall" in out_path.read_text(encoding="utf-8")

    async def _rate(_gen: object, _cases: object, _params: object, _runtime: object) -> None:
        raise RateLimitedError(
            CallRecord(
                outcome="unavailable",
                latency_ms=0,
                expected_safety="ok",
                attempts=1,
                reasons=(),
                unavailable_kind="rate_limited",
                input_tokens=0,
                output_tokens=0,
                billable_tokens=0,
            )
        )

    monkeypatch.setattr("svoi_pravila.evals.cli.warmup_eval", _rate)
    rate_out = tmp_path / "rate.md"
    ns.out = str(rate_out)
    assert await async_main(ns) == 1
    assert "INCOMPLETE" in rate_out.read_text(encoding="utf-8")

    async def _auth(_gen: object, _cases: object, _params: object, _runtime: object) -> None:
        raise AuthFailedError(
            CallRecord(
                outcome="unavailable",
                latency_ms=0,
                expected_safety="ok",
                attempts=1,
                reasons=(),
                unavailable_kind="auth",
                input_tokens=0,
                output_tokens=0,
                billable_tokens=0,
            )
        )

    monkeypatch.setattr("svoi_pravila.evals.cli.warmup_eval", _auth)
    auth_out = tmp_path / "auth.md"
    ns.out = str(auth_out)
    assert await async_main(ns) == 1
    assert "auth failed" in auth_out.read_text(encoding="utf-8")

    async def _budget(_gen: object, _cases: object, _params: object, _runtime: object) -> None:
        raise TokenBudgetExceededError(spent=1, limit=2)

    monkeypatch.setattr("svoi_pravila.evals.cli.warmup_eval", _budget)
    budget_out = tmp_path / "budget.md"
    ns.out = str(budget_out)
    assert await async_main(ns) == 1
    assert "token budget" in budget_out.read_text(encoding="utf-8")


@pytest.mark.unit
def test_main_entrypoint(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _ok(_args: Namespace) -> int:
        return 0

    monkeypatch.setattr("svoi_pravila.evals.cli.async_main", _ok)
    monkeypatch.setattr(
        "sys.argv",
        ["svoi-pravila-eval", "--dry-run", "--data", str(_DATA)],
    )
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0
