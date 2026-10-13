"""Offline tests for the rewrite eval: cases, run loop, report and CLI (no network)."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.factories import make_settings
from tests.fakes.generation import FakeTextGenerator

from svoi_pravila.application.errors import (
    GenerationRefusedByProvider,
    GenerationUnavailable,
    InvalidGenerationOutput,
    InvalidOutputReason,
    UnavailableKind,
)
from svoi_pravila.application.ports.generation import (
    GenerationMeta,
    RewriteResult,
    SafetyVerdict,
    TokenUsage,
    Variant,
)
from svoi_pravila.benchmarks.runner import SpendTracker
from svoi_pravila.domain.enums import RelationshipKind, RewriteGoal, VariantKind
from svoi_pravila.domain.variant_policy import PolicyRule
from svoi_pravila.evals.rewrite_cases import (
    RewriteCase,
    load_rewrite_cases,
    merge_cases,
    parse_rewrite_case,
)
from svoi_pravila.evals.rewrite_cli import async_main, main
from svoi_pravila.evals.rewrite_report import render_report
from svoi_pravila.evals.rewrite_run import (
    RewriteEvalParams,
    RunOutcome,
    run_rewrite_eval,
)

_DATA = Path(__file__).resolve().parents[3] / "evals" / "data" / "interview_v1.jsonl"
_DRAFT = "Сиди дома и жди"


def _case(case_id: str = "c1", *, goal: RewriteGoal | None = None) -> RewriteCase:
    return RewriteCase(id=case_id, draft=_DRAFT, goal=goal)


def _params(*, repeat: int = 1, max_tokens: int = 100_000) -> RewriteEvalParams:
    return RewriteEvalParams(repeat=repeat, deadline_seconds=5.0, max_tokens=max_tokens)


def _result(*variants: Variant, safety: SafetyVerdict = SafetyVerdict.OK) -> RewriteResult:
    return RewriteResult(
        variants=variants,
        applied_rule_indexes=(),
        safety=safety,
        meta=GenerationMeta(
            model="fake",
            prompt_version="rewrite@v1",
            latency_ms=7,
            attempts=1,
            usage=TokenUsage(input=3, output=4),
        ),
    )


def _unavailable(kind: UnavailableKind) -> GenerationUnavailable:
    return GenerationUnavailable(
        kind, usage=TokenUsage(output=2), attempts=1, model="m", prompt_version="p"
    )


def _cli_args(**overrides: object) -> Namespace:
    values: dict[str, object] = {
        "data": str(_DATA),
        "extra_cases": None,
        "repeat": 1,
        "deadline": 5.0,
        "out": None,
        "dry_run": False,
        "list_models": False,
        "max_tokens": None,
    }
    values.update(overrides)
    return Namespace(**values)


@pytest.mark.unit
def test_interview_cases_are_committed_and_valid() -> None:
    cases = load_rewrite_cases(_DATA)
    assert [case.id for case in cases] == ["i1", "i2", "i3", "i4", "i5", "i6"]
    assert all(case.goal is None for case in cases)


@pytest.mark.unit
def test_parse_rewrite_case_and_errors(tmp_path: Path) -> None:
    parsed = parse_rewrite_case(
        {"id": "g", "draft": "x", "goal": "decline", "relationship": "work"}
    )
    assert parsed == RewriteCase(
        id="g", draft="x", goal=RewriteGoal.DECLINE, relationship=RelationshipKind.WORK
    )
    with pytest.raises(TypeError):
        parse_rewrite_case([])
    with pytest.raises(ValueError, match="case id"):
        parse_rewrite_case({"id": "", "draft": "x"})
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"id": "a", "draft": "x"}\n{\n', encoding="utf-8")
    with pytest.raises(ValueError, match=r"bad\.jsonl:2: invalid case"):
        load_rewrite_cases(bad)
    empty = tmp_path / "empty.jsonl"
    empty.write_text("\n", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        load_rewrite_cases(empty)


@pytest.mark.unit
def test_merge_cases_keeps_ids_unique() -> None:
    merged = merge_cases([_case("a")], [_case("b")])
    assert [case.id for case in merged] == ["a", "b"]
    with pytest.raises(ValueError, match="duplicate case id: a"):
        merge_cases([_case("a")], [_case("a")])


@pytest.mark.unit
async def test_run_applies_typography_and_reports_policy_violations() -> None:
    gen = FakeTextGenerator(
        rewrite_result=_result(
            Variant(text="Совсем другой текст про ничего общего.", kind=VariantKind.CHECK),
            Variant(text="Сиди дома \u2014 я ценю тебя", kind=VariantKind.SOFTER),
            Variant(text=_DRAFT, kind=VariantKind.FIRMER),
        )
    )
    spend = SpendTracker()
    result = await run_rewrite_eval(gen, [_case(goal=RewriteGoal.SET_BOUNDARY)], _params(), spend)
    assert result.stop_reason is None
    (record,) = result.records
    assert record.outcome is RunOutcome.OK
    assert gen.rewrite_calls[0].goal is RewriteGoal.SET_BOUNDARY
    by_kind = {variant.kind: variant for variant in record.variants}
    assert by_kind[VariantKind.CHECK].violations == (PolicyRule.EDIT_RATIO,)
    assert by_kind[VariantKind.SOFTER].text == "Сиди дома, я ценю тебя"
    assert by_kind[VariantKind.SOFTER].violations == (PolicyRule.STOP_PHRASE,)
    assert by_kind[VariantKind.FIRMER].violations == ()
    assert spend.spent_billable == record.billable_tokens == 7


@pytest.mark.unit
async def test_run_counts_non_ok_safety_without_variants() -> None:
    gen = FakeTextGenerator(rewrite_result=_result(safety=SafetyVerdict.CRISIS))
    result = await run_rewrite_eval(gen, [_case()], _params(), SpendTracker())
    assert result.records[0].outcome is RunOutcome.NOT_OK_SAFETY
    assert result.records[0].variants == ()


@pytest.mark.unit
async def test_run_records_refusal_and_invalid_output_and_continues() -> None:
    gen = FakeTextGenerator()
    gen.rewrite_error = GenerationRefusedByProvider(
        usage=TokenUsage(output=1), attempts=1, model="m", prompt_version="p"
    )
    spend = SpendTracker()
    refused = await run_rewrite_eval(gen, [_case("a"), _case("b")], _params(repeat=2), spend)
    assert [r.outcome for r in refused.records] == [RunOutcome.REFUSED] * 4
    assert spend.spent_billable == 4
    gen.rewrite_error = InvalidGenerationOutput(
        (InvalidOutputReason.PROMPT_LEAK,),
        usage=TokenUsage(output=3),
        attempts=2,
        model="m",
        prompt_version="p",
    )
    invalid = await run_rewrite_eval(gen, [_case()], _params(), SpendTracker())
    assert invalid.records[0].outcome is RunOutcome.INVALID_OUTPUT
    assert invalid.records[0].billable_tokens == 3


@pytest.mark.unit
async def test_run_unavailable_continues_but_rate_limit_and_auth_stop() -> None:
    gen = FakeTextGenerator()
    gen.rewrite_error = _unavailable(UnavailableKind.TIMEOUT)
    spend = SpendTracker()
    soft = await run_rewrite_eval(gen, [_case("a"), _case("b")], _params(), spend)
    assert soft.stop_reason is None
    assert [r.outcome for r in soft.records] == [RunOutcome.UNAVAILABLE] * 2
    assert spend.spent_billable == 4
    for kind in (UnavailableKind.RATE_LIMITED, UnavailableKind.AUTH):
        gen.rewrite_error = _unavailable(kind)
        stopped = await run_rewrite_eval(gen, [_case("a"), _case("b")], _params(), SpendTracker())
        assert stopped.stop_reason == kind.value
        assert len(stopped.records) == 1
        assert len(gen.rewrite_calls) >= 1


@pytest.mark.unit
async def test_run_stops_before_a_call_that_would_cross_the_budget() -> None:
    gen = FakeTextGenerator()
    gen.max_billable_value = 100
    result = await run_rewrite_eval(
        gen, [_case("a"), _case("b")], _params(repeat=3, max_tokens=103), SpendTracker()
    )
    assert result.stop_reason == "token_budget"
    assert len(result.records) == 2
    assert len(gen.rewrite_calls) == 2


@pytest.mark.unit
async def test_report_lists_summary_refusals_and_details() -> None:
    gen = FakeTextGenerator(
        rewrite_result=_result(
            Variant(text=_DRAFT, kind=VariantKind.CHECK),
            Variant(text="Сиди | дома\nи жди, пожалуйста", kind=VariantKind.SOFTER),
            Variant(text="Жди дома", kind=VariantKind.FIRMER),
        )
    )
    spend = SpendTracker()
    result = await run_rewrite_eval(gen, [_case("a")], _params(repeat=2), spend)
    gen.rewrite_error = GenerationRefusedByProvider(
        usage=TokenUsage(), attempts=1, model="m", prompt_version="p"
    )
    refused = await run_rewrite_eval(gen, [_case("b")], _params(), spend)
    result.records.extend(refused.records)
    report = render_report(result, model="fake-model", spent_billable=spend.spent_billable)
    assert "- model: fake-model" in report
    assert "- status: COMPLETE" in report
    assert "- runs: 3" in report
    assert "- ok: 2" in report
    assert "- refused: 1" in report
    assert "- latency p50 / p95 ms: 7 / 7" in report
    assert "- billable tokens: 14" in report
    assert "| b | 1 |" in report
    assert "| a | 0 |" in report
    assert "Сиди \\| дома и жди, пожалуйста" in report
    assert "| b | 1 | refused | | |  | |" in report
    for rule in PolicyRule:
        assert f"| {rule.value} |" in report


@pytest.mark.unit
def test_report_marks_incomplete_runs_with_the_reason() -> None:
    from svoi_pravila.evals.rewrite_run import RewriteEvalResult

    report = render_report(
        RewriteEvalResult(stop_reason="token_budget"), model="m", spent_billable=0
    )
    assert "- status: INCOMPLETE (token_budget)" in report
    assert "- latency p50 / p95 ms: 0 / 0" in report


@pytest.mark.unit
async def test_cli_dry_run_prints_plan_and_makes_no_client(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr("svoi_pravila.evals.rewrite_cli.LlmToolSettings", make_settings)

    def forbid(_settings: object) -> None:
        raise AssertionError("network")

    monkeypatch.setattr("svoi_pravila.evals.rewrite_cli.create_gigachat_client", forbid)
    extra = tmp_path / "extra.jsonl"
    extra.write_text(json.dumps({"id": "x1", "draft": "Привет", "goal": "reconnect"}) + "\n")
    code = await async_main(_cli_args(dry_run=True, repeat=2, extra_cases=str(extra)))
    assert code == 0
    out = capsys.readouterr().out
    assert "Cases: 7" in out
    assert "Total planned calls: 14" in out
    assert "Total worst-case billable tokens:" in out
    assert "Largest single-call worst-case estimate:" in out


@pytest.mark.unit
async def test_cli_live_run_requires_max_tokens_and_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("svoi_pravila.evals.rewrite_cli.LlmToolSettings", make_settings)
    with pytest.raises(SystemExit, match="--max-tokens and --out"):
        await async_main(_cli_args(max_tokens=None, out="r.md"))
    with pytest.raises(SystemExit, match="--max-tokens and --out"):
        await async_main(_cli_args(max_tokens=1000, out=None))


def _one_case(tmp_path: Path) -> str:
    path = tmp_path / "one.jsonl"
    path.write_text(json.dumps({"id": "one", "draft": _DRAFT}, ensure_ascii=False) + "\n")
    return str(path)


class _Client:
    async def aget_models(self) -> SimpleNamespace:
        return SimpleNamespace(data=[SimpleNamespace(id_="GigaChat-3-Lightning")])


def _patch_client(monkeypatch: pytest.MonkeyPatch, gen: FakeTextGenerator) -> list[str]:
    closed: list[str] = []

    async def close(_client: object) -> None:
        closed.append("closed")

    monkeypatch.setattr("svoi_pravila.evals.rewrite_cli.LlmToolSettings", make_settings)
    monkeypatch.setattr(
        "svoi_pravila.evals.rewrite_cli.create_gigachat_client", lambda _s: _Client()
    )
    monkeypatch.setattr("svoi_pravila.evals.rewrite_cli.close_gigachat_client", close)
    monkeypatch.setattr("svoi_pravila.evals.rewrite_cli.GigaChatTextGenerator", lambda _c, _s: gen)
    return closed


@pytest.mark.unit
async def test_cli_list_models_spends_no_generation_tokens(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    gen = FakeTextGenerator()
    closed = _patch_client(monkeypatch, gen)
    code = await async_main(_cli_args(list_models=True))
    assert code == 0
    assert "GigaChat-3-Lightning" in capsys.readouterr().out
    assert gen.rewrite_calls == []
    assert closed == ["closed"]


@pytest.mark.unit
async def test_cli_live_run_writes_report_and_exit_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    gen = FakeTextGenerator(
        rewrite_result=_result(
            Variant(text="Сиди дома и жди", kind=VariantKind.CHECK),
            Variant(text="Пожалуйста, посиди дома", kind=VariantKind.SOFTER),
            Variant(text="Жди дома", kind=VariantKind.FIRMER),
        )
    )
    closed = _patch_client(monkeypatch, gen)
    out = tmp_path / "nested" / "report.md"
    code = await async_main(_cli_args(data=_one_case(tmp_path), max_tokens=1_000_000, out=str(out)))
    assert code == 0
    assert len(gen.rewrite_calls) == 1
    assert "- status: COMPLETE" in out.read_text(encoding="utf-8")
    assert "Spent billable tokens: 7" in capsys.readouterr().out
    assert closed == ["closed"]


@pytest.mark.unit
async def test_cli_exit_code_is_one_for_violations_and_early_stops(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    violating = FakeTextGenerator(
        rewrite_result=_result(
            Variant(text="Совсем другой текст про ничего общего.", kind=VariantKind.CHECK),
            Variant(text="Пожалуйста, посиди дома", kind=VariantKind.SOFTER),
            Variant(text="Жди дома", kind=VariantKind.FIRMER),
        )
    )
    _patch_client(monkeypatch, violating)
    data = _one_case(tmp_path)
    code = await async_main(_cli_args(data=data, max_tokens=1_000_000, out=str(tmp_path / "v.md")))
    assert code == 1
    limited = FakeTextGenerator()
    limited.rewrite_error = _unavailable(UnavailableKind.RATE_LIMITED)
    _patch_client(monkeypatch, limited)
    report = tmp_path / "limited.md"
    assert await async_main(_cli_args(data=data, max_tokens=1_000_000, out=str(report))) == 1
    assert "INCOMPLETE (rate_limited)" in report.read_text(encoding="utf-8")


@pytest.mark.unit
def test_main_parses_arguments_and_exits_with_the_run_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("svoi_pravila.evals.rewrite_cli.LlmToolSettings", make_settings)
    monkeypatch.setattr(
        "sys.argv", ["svoi-pravila-eval-rewrite", "--data", str(_DATA), "--dry-run"]
    )
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 0
    assert "Total planned calls: 18" in capsys.readouterr().out
