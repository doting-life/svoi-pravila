"""Offline tests for the rewrite eval: cases, run loop, report and CLI (no network)."""

from __future__ import annotations

import json
from argparse import Namespace
from datetime import UTC, datetime
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
from svoi_pravila.application.lexicons import load_tone_lexicons_ru_v1
from svoi_pravila.application.ports.generation import (
    GenerationMeta,
    RewriteResult,
    RuleContext,
    SafetyVerdict,
    TokenUsage,
    Variant,
)
from svoi_pravila.benchmarks.runner import SpendTracker
from svoi_pravila.config import GigaChatRuntimeSettings
from svoi_pravila.domain.enums import RelationshipKind, RewriteGoal, RuleCategory, VariantKind
from svoi_pravila.domain.variant_policy import PolicyRule
from svoi_pravila.evals.rewrite_cases import (
    RewriteCase,
    RewriteCategory,
    load_rewrite_cases,
    merge_cases,
    parse_rewrite_case,
    select_categories,
)
from svoi_pravila.evals.rewrite_cli import async_main, main
from svoi_pravila.evals.rewrite_report import render_report
from svoi_pravila.evals.rewrite_run import (
    ModelEval,
    RewriteEvalParams,
    RewriteEvalResult,
    RunOutcome,
    run_rewrite_eval,
)

_DATA = Path(__file__).resolve().parents[3] / "evals" / "data" / "rewrite_v2.jsonl"
_DRAFT = "Сиди дома и жди"


def _case(case_id: str = "c1", *, goal: RewriteGoal | None = None) -> RewriteCase:
    return RewriteCase(id=case_id, category=RewriteCategory.BASE, draft=_DRAFT, goal=goal)


def _params(*, repeat: int = 1, max_tokens: int = 100_000) -> RewriteEvalParams:
    return RewriteEvalParams(
        repeat=repeat,
        deadline_seconds=5.0,
        max_tokens=max_tokens,
        lexicons=load_tone_lexicons_ru_v1(),
    )


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
        "category": None,
        "baseline": None,
        "write_baseline": None,
        "repeat": 1,
        "deadline": 5.0,
        "out": None,
        "dry_run": False,
        "list_models": False,
        "max_tokens": None,
        "model": None,
    }
    values.update(overrides)
    return Namespace(**values)


@pytest.mark.unit
def test_base_cases_keep_the_interview_ids() -> None:
    cases = load_rewrite_cases(_DATA)
    base = [case for case in cases if case.category is RewriteCategory.BASE]
    assert [case.id for case in base] == ["i1", "i2", "i3", "i4", "i5", "i6"]
    assert all(case.goal is None for case in base)


@pytest.mark.unit
def test_parse_rewrite_case_and_errors(tmp_path: Path) -> None:
    parsed = parse_rewrite_case(
        {
            "id": "g",
            "category": "goal",
            "draft": "x",
            "goal": "decline",
            "relationship": "work",
            "forbidden_stems": ["иначе"],
            "effect_pair": "p1",
            "smoke": True,
        }
    )
    assert parsed == RewriteCase(
        id="g",
        category=RewriteCategory.GOAL,
        draft="x",
        goal=RewriteGoal.DECLINE,
        relationship=RelationshipKind.WORK,
        forbidden_stems=("иначе",),
        effect_pair="p1",
        smoke=True,
    )
    with pytest.raises(TypeError):
        parse_rewrite_case([])
    with pytest.raises(ValueError, match="id must be a non-empty string"):
        parse_rewrite_case({"id": "", "category": "base", "draft": "x"})
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"id": "a", "category": "base", "draft": "x"}\n{\n', encoding="utf-8")
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
def test_parse_rewrite_case_is_strict() -> None:
    base = {"id": "a", "category": "base", "draft": "x"}
    for bad, message in (
        ({**base, "extra": 1}, "unknown fields: extra"),
        ({**base, "category": "nope"}, "nope"),
        ({"id": "a", "draft": "x"}, "category must be"),
        ({"id": "a", "category": "goal", "draft": "x"}, "goal cases require a goal"),
        ({**base, "goal": "nope"}, "nope"),
        ({**base, "draft": " "}, "draft must be"),
        ({**base, "effect_pair": 3}, "effect_pair must be"),
    ):
        with pytest.raises(ValueError, match=message):
            parse_rewrite_case(bad)
    with pytest.raises(TypeError, match="smoke"):
        parse_rewrite_case({**base, "smoke": "yes"})
    with pytest.raises(TypeError, match="forbidden_stems"):
        parse_rewrite_case({**base, "forbidden_stems": "иначе"})
    with pytest.raises(ValueError, match="isoformat"):
        parse_rewrite_case(
            {**base, "rules": [{"category": "how_to_ask", "text": "t", "effective_since": 1}]}
        )


@pytest.mark.unit
def test_parse_rewrite_case_reads_rules_and_goal_null() -> None:
    parsed = parse_rewrite_case(
        {
            "id": "r",
            "category": "rule",
            "draft": "x",
            "goal": None,
            "rules": [
                {
                    "category": "how_to_ask",
                    "text": "Заканчивать вопросом",
                    "effective_since": "2026-01-01T00:00:00+00:00",
                }
            ],
        }
    )
    assert parsed.goal is None
    assert [rule.category for rule in parsed.rules] == [RuleCategory.HOW_TO_ASK]


@pytest.mark.unit
def test_load_rewrite_cases_rejects_a_repeated_id_with_its_line(tmp_path: Path) -> None:
    path = tmp_path / "dup.jsonl"
    line = json.dumps({"id": "a", "category": "base", "draft": "x"})
    path.write_text(f"{line}\n\n{line}\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"dup\.jsonl:3: duplicate case id: a"):
        load_rewrite_cases(path)


@pytest.mark.unit
async def test_run_passes_rules_and_applies_the_set_policy_stems_and_closing_ask() -> None:
    rule = RuleContext(
        category=RuleCategory.HOW_TO_ASK,
        text="Заканчивать вопросом",
        effective_since=datetime(2026, 1, 1, tzinfo=UTC),
    )
    case = RewriteCase(
        id="rule-with",
        category=RewriteCategory.RULE,
        draft="Мне нужно чтобы ты предупреждал заранее",
        rules=(rule,),
        forbidden_stems=("иначе",),
        effect_pair="p",
    )
    gen = FakeTextGenerator(
        rewrite_result=_result(
            Variant(text="Мне нужно, чтобы ты предупреждал заранее", kind=VariantKind.CHECK),
            Variant(text="Прошу, предупреждай заранее. Можешь?", kind=VariantKind.SOFTER),
            Variant(text="Предупреждай заранее, иначе пеняй на себя", kind=VariantKind.FIRMER),
        )
    )
    result = await run_rewrite_eval(gen, [case], _params(), SpendTracker())
    assert gen.rewrite_calls[0].rules == (rule,)
    (record,) = result.records
    assert record.had_rules is True
    assert record.category is RewriteCategory.RULE
    by_kind = {variant.kind: variant for variant in record.variants}
    assert by_kind[VariantKind.CHECK].stem_leak is None
    assert by_kind[VariantKind.SOFTER].stem_leak is False
    assert by_kind[VariantKind.FIRMER].stem_leak is True
    assert [v.closing_ask_ok for v in record.variants] == [False, True, False]
    assert by_kind[VariantKind.FIRMER].violations == (PolicyRule.LADDER_LENGTH,)


@pytest.mark.unit
async def test_run_reports_ladder_and_tone_rules_on_the_variant_set() -> None:
    case = RewriteCase(id="l", category=RewriteCategory.LADDER, draft="Верни деньги")
    gen = FakeTextGenerator(
        rewrite_result=_result(
            Variant(text="Верни деньги", kind=VariantKind.CHECK),
            Variant(text="Верни деньги, пожалуйста", kind=VariantKind.SOFTER),
            Variant(text="Верни деньги, пожалуйста, хуй", kind=VariantKind.FIRMER),
        )
    )
    result = await run_rewrite_eval(gen, [case], _params(), SpendTracker())
    firmer = result.records[0].variants[2]
    assert firmer.violations == (
        PolicyRule.LENGTH,
        PolicyRule.PROFANITY,
        PolicyRule.HEDGE,
        PolicyRule.LADDER_LENGTH,
    )
    assert firmer.stem_leak is None
    assert firmer.closing_ask_ok is None


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
    report = render_report([ModelEval("fake-model", result, spend.spent_billable)])
    assert "## Model: fake-model" in report
    assert "- status: COMPLETE" in report
    assert "- runs: 3" in report
    assert "- ok: 2" in report
    assert "- refused: 1" in report
    assert "- latency p50 / p95 ms: 7 / 7" in report
    assert "- billable tokens: 14" in report
    assert "| b | 1 |" in report
    assert "| a | 0 |" in report
    assert "Сиди \\| дома и жди, пожалуйста" in report
    assert "| b | 1 | refused | | | |  | |" in report
    assert "- check_ok: 0 / 0" in report
    for rule in PolicyRule:
        assert f"| {rule.value} |" in report


@pytest.mark.unit
def test_interview_cases_carry_reference_corrections_for_check() -> None:
    expected = {
        case.id: case.check_expected
        for case in load_rewrite_cases(_DATA)
        if case.category is RewriteCategory.BASE
    }
    assert all(expected.values())
    assert expected["i1"] == ("Сиди дома и жди",)
    assert len(expected["i3"]) == 2
    assert len(expected["i6"]) == 2
    assert not any(text.endswith(".") for text in expected["i1"] + expected["i2"])


@pytest.mark.unit
def test_parse_check_expected_accepts_a_list_and_rejects_other_shapes() -> None:
    base = {"id": "a", "category": "base", "draft": "x"}
    parsed = parse_rewrite_case({**base, "check_expected": ["X", "Y"]})
    assert parsed.check_expected == ("X", "Y")
    assert parse_rewrite_case(base).check_expected == ()
    for bad in ("X", ["X", 1], [" "]):
        with pytest.raises(TypeError, match="check_expected"):
            parse_rewrite_case({**base, "check_expected": bad})


@pytest.mark.unit
async def test_run_compares_check_with_references_after_whitespace_normalization() -> None:
    case = RewriteCase(
        id="c",
        category=RewriteCategory.BASE,
        draft="ну что ребята",
        check_expected=("Ну что, ребята",),
    )

    async def run(check_text: str) -> bool | None:
        gen = FakeTextGenerator(
            rewrite_result=_result(
                Variant(text=check_text, kind=VariantKind.CHECK),
                Variant(text="Ну что, ребята, как вы", kind=VariantKind.SOFTER),
                Variant(text="Ну что, ребята", kind=VariantKind.FIRMER),
            )
        )
        result = await run_rewrite_eval(gen, [case], _params(), SpendTracker())
        by_kind = {variant.kind: variant for variant in result.records[0].variants}
        assert by_kind[VariantKind.SOFTER].check_ok is None
        assert by_kind[VariantKind.FIRMER].check_ok is None
        return by_kind[VariantKind.CHECK].check_ok

    assert await run("Ну  что,\nребята") is True
    assert await run("ну что ребята") is False
    no_reference = RewriteCase(id="n", category=RewriteCategory.BASE, draft="ну что ребята")
    gen = FakeTextGenerator(
        rewrite_result=_result(
            Variant(text="ну что ребята", kind=VariantKind.CHECK),
            Variant(text="Ну что, ребята, как вы", kind=VariantKind.SOFTER),
            Variant(text="Ну что, ребята", kind=VariantKind.FIRMER),
        )
    )
    result = await run_rewrite_eval(gen, [no_reference], _params(), SpendTracker())
    assert result.records[0].variants[0].check_ok is None


@pytest.mark.unit
async def test_run_substitutes_the_draft_for_an_empty_check_and_counts_it() -> None:
    case = RewriteCase(
        id="c",
        category=RewriteCategory.BASE,
        draft="ну что ребята",
        check_expected=("ну что ребята",),
    )
    gen = FakeTextGenerator(
        rewrite_result=_result(
            Variant(text="", kind=VariantKind.CHECK),
            Variant(text="Ну что, ребята, как вы", kind=VariantKind.SOFTER),
            Variant(text="Ну что, ребята", kind=VariantKind.FIRMER),
        )
    )
    result = await run_rewrite_eval(gen, [case], _params(repeat=2), SpendTracker())
    first, second = result.records
    assert first.check_empty is True
    assert first.variants[0].text == "ну что ребята"
    assert first.variants[0].check_ok is True
    assert first.variants[0].violations == ()
    assert second.check_empty is True
    report = render_report([ModelEval("m", result, 0)])
    assert "- check_empty (draft substituted): 2" in report
    assert "| 1 / 2 | 2 |" not in report
    assert "| 2 / 2 | 2 |" in report


@pytest.mark.unit
async def test_run_does_not_count_a_filled_check_as_empty() -> None:
    gen = FakeTextGenerator(
        rewrite_result=_result(
            Variant(text=_DRAFT, kind=VariantKind.CHECK),
            Variant(text="Пожалуйста, посиди дома", kind=VariantKind.SOFTER),
            Variant(text="Жди дома", kind=VariantKind.FIRMER),
        )
    )
    result = await run_rewrite_eval(gen, [_case("a")], _params(), SpendTracker())
    assert result.records[0].check_empty is False
    assert "- check_empty (draft substituted): 0" in render_report([ModelEval("m", result, 0)])


@pytest.mark.unit
async def test_run_records_slot_and_finish_reason_of_invalid_attempts() -> None:
    gen = FakeTextGenerator()
    gen.rewrite_error = InvalidGenerationOutput(
        (InvalidOutputReason.EMPTY_TEXT, InvalidOutputReason.EMPTY_TEXT),
        usage=TokenUsage(output=3),
        attempts=2,
        model="m",
        prompt_version="p",
    ).with_diagnostics((VariantKind.SOFTER, VariantKind.CHECK), ("stop", None))
    result = await run_rewrite_eval(gen, [_case("a")], _params(), SpendTracker())
    (record,) = result.records
    assert [(d.reason, d.finish_reason) for d in record.diagnostics] == [
        ("empty_text:softer", "stop"),
        ("empty_text:check", None),
    ]
    report = render_report([ModelEval("m", result, 3)])
    assert "| a | 1 | 1 | empty_text:softer | stop |" in report
    assert "| a | 1 | 2 | empty_text:check | none |" in report


@pytest.mark.unit
def test_report_marks_incomplete_runs_with_the_reason() -> None:
    report = render_report(
        [ModelEval("m", RewriteEvalResult(stop_reason="token_budget"), 0)], not_run=("n", "o")
    )
    assert "- not run: n, o" in report
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
    extra.write_text(
        json.dumps({"id": "x1", "category": "goal", "draft": "Привет", "goal": "reconnect"}) + "\n"
    )
    code = await async_main(_cli_args(dry_run=True, repeat=2, extra_cases=str(extra)))
    assert code == 0
    out = capsys.readouterr().out
    assert "Models: GigaChat-2\n" in out
    total = len(load_rewrite_cases(_DATA)) + 1
    assert f"Cases: {total}\n" in out
    assert f"Total planned calls: {total * 2}\n" in out
    assert "Total worst-case billable tokens:" in out
    assert "Largest single-call worst-case estimate:" in out


@pytest.mark.unit
async def test_cli_category_filter_keeps_only_the_selected_categories(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("svoi_pravila.evals.rewrite_cli.LlmToolSettings", make_settings)
    cases = load_rewrite_cases(_DATA)
    wanted = {RewriteCategory.PROFANITY, RewriteCategory.LADDER}
    await async_main(_cli_args(dry_run=True, category=[RewriteCategory.PROFANITY]))
    only_profanity = len([c for c in cases if c.category is RewriteCategory.PROFANITY])
    assert f"Cases: {only_profanity}\n" in capsys.readouterr().out
    await async_main(_cli_args(dry_run=True, category=sorted(wanted)))
    both = len([c for c in cases if c.category in wanted])
    assert f"Cases: {both}\n" in capsys.readouterr().out


@pytest.mark.unit
def test_select_categories_keeps_everything_without_a_filter_and_rejects_an_empty_result() -> None:
    cases = load_rewrite_cases(_DATA)
    assert select_categories(cases, ()) == cases
    profanity = select_categories(cases, {RewriteCategory.PROFANITY})
    assert profanity
    assert {case.category for case in profanity} == {RewriteCategory.PROFANITY}
    with pytest.raises(ValueError, match="selected categories"):
        select_categories(profanity, {RewriteCategory.GOAL})


@pytest.mark.unit
def test_main_accepts_repeatable_category(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("svoi_pravila.evals.rewrite_cli.LlmToolSettings", make_settings)
    monkeypatch.setattr(
        "sys.argv",
        [
            "svoi-pravila-eval-rewrite",
            "--data",
            str(_DATA),
            "--dry-run",
            "--category",
            "profanity",
            "--category",
            "ladder",
        ],
    )
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 0
    expected = len(
        [
            case
            for case in load_rewrite_cases(_DATA)
            if case.category in {RewriteCategory.PROFANITY, RewriteCategory.LADDER}
        ]
    )
    assert f"Cases: {expected}\n" in capsys.readouterr().out


@pytest.mark.unit
async def test_cli_dry_run_multiplies_the_plan_by_distinct_models(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("svoi_pravila.evals.rewrite_cli.LlmToolSettings", make_settings)
    await async_main(_cli_args(dry_run=True, model=["A", "B", "A"]))
    out = capsys.readouterr().out
    assert "Models: A, B\n" in out
    assert f"Total planned calls: {len(load_rewrite_cases(_DATA)) * 2}\n" in out


@pytest.mark.unit
async def test_cli_live_run_with_models_reports_each_and_stops_at_the_first_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ok = FakeTextGenerator(
        rewrite_result=_result(
            Variant(text="Сиди дома и жди", kind=VariantKind.CHECK),
            Variant(text="Пожалуйста, посиди дома", kind=VariantKind.SOFTER),
            Variant(text="Жди дома", kind=VariantKind.FIRMER),
        )
    )
    limited = FakeTextGenerator()
    limited.rewrite_error = _unavailable(UnavailableKind.RATE_LIMITED)
    by_model = {"A": ok, "B": limited, "C": ok}
    used: list[str] = []

    def factory(_client: object, settings: GigaChatRuntimeSettings) -> FakeTextGenerator:
        model = settings.gigachat_model_rewrite
        used.append(model)
        return by_model[model]

    _patch_client(monkeypatch, ok)
    monkeypatch.setattr("svoi_pravila.evals.rewrite_cli.GigaChatTextGenerator", factory)
    report = tmp_path / "multi.md"
    code = await async_main(
        _cli_args(
            data=_one_case(tmp_path),
            max_tokens=1_000_000,
            out=str(report),
            model=["A", "B", "C"],
        )
    )
    text = report.read_text(encoding="utf-8")
    assert code == 1
    assert used == ["A", "B"]
    assert "| A | COMPLETE | 1 | 0 | 0 | 0 / 0 |" in text
    assert "| B | INCOMPLETE (rate_limited) | 1 |" in text
    assert "## Model: A" in text
    assert "## Model: B" in text
    assert "- not run: C" in text


@pytest.mark.unit
async def test_cli_without_model_uses_the_settings_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    gen = FakeTextGenerator(
        rewrite_result=_result(
            Variant(text="Сиди дома и жди", kind=VariantKind.CHECK),
            Variant(text="Пожалуйста, посиди дома", kind=VariantKind.SOFTER),
            Variant(text="Жди дома", kind=VariantKind.FIRMER),
        )
    )
    used: list[str] = []

    def factory(_client: object, settings: GigaChatRuntimeSettings) -> FakeTextGenerator:
        used.append(settings.gigachat_model_rewrite)
        return gen

    _patch_client(monkeypatch, gen)
    monkeypatch.setattr("svoi_pravila.evals.rewrite_cli.GigaChatTextGenerator", factory)
    out = tmp_path / "one.md"
    await async_main(_cli_args(data=_one_case(tmp_path), max_tokens=1_000_000, out=str(out)))
    assert used == ["GigaChat-2"]
    assert "## Model: GigaChat-2" in out.read_text(encoding="utf-8")


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
    path.write_text(
        json.dumps({"id": "one", "category": "base", "draft": _DRAFT}, ensure_ascii=False) + "\n"
    )
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
async def test_cli_exit_code_follows_the_gates_and_early_stops(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    passing = _result(
        Variant(text="Сиди дома и жди", kind=VariantKind.CHECK),
        Variant(text="Пожалуйста, посиди дома", kind=VariantKind.SOFTER),
        Variant(text="Жди дома", kind=VariantKind.FIRMER),
    )
    data = _one_case(tmp_path)

    async def code_for(generator: FakeTextGenerator, name: str, **overrides: object) -> int:
        _patch_client(monkeypatch, generator)
        args = _cli_args(data=data, max_tokens=1_000_000, out=str(tmp_path / name), **overrides)
        return await async_main(args)

    assert await code_for(FakeTextGenerator(rewrite_result=passing), "ok.md") == 0
    profane = FakeTextGenerator(
        rewrite_result=_result(
            Variant(text="Сиди дома и жди", kind=VariantKind.CHECK),
            Variant(text="Посиди дома, хуй с ним", kind=VariantKind.SOFTER),
            Variant(text="Жди дома", kind=VariantKind.FIRMER),
        )
    )
    assert await code_for(profane, "g1.md") == 1
    assert "| G1 | profanity in softer/firmer | 1 of 1 calls | 0 | FAIL |" in (
        tmp_path / "g1.md"
    ).read_text(encoding="utf-8")
    invalid = FakeTextGenerator()
    invalid.rewrite_error = InvalidGenerationOutput(
        (InvalidOutputReason.EMPTY_TEXT,),
        usage=TokenUsage(output=1),
        attempts=1,
        model="m",
        prompt_version="p",
    )
    assert await code_for(invalid, "g9.md") == 1
    limited = FakeTextGenerator(rewrite_result=passing)
    limited.rewrite_error = _unavailable(UnavailableKind.RATE_LIMITED)
    assert await code_for(limited, "limited.md") == 1
    assert "INCOMPLETE (rate_limited)" in (tmp_path / "limited.md").read_text(encoding="utf-8")


@pytest.mark.unit
async def test_cli_baseline_decides_gate_g8_and_write_baseline_stores_rates_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    referenced = tmp_path / "ref.jsonl"
    referenced.write_text(
        json.dumps(
            {
                "id": "r",
                "category": "base",
                "draft": _DRAFT,
                "check_expected": ["Сиди дома и жди"],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    gen = FakeTextGenerator(
        rewrite_result=_result(
            Variant(text="Сиди дома и жди", kind=VariantKind.CHECK),
            Variant(text="Пожалуйста, посиди дома", kind=VariantKind.SOFTER),
            Variant(text="Жди дома", kind=VariantKind.FIRMER),
        )
    )
    _patch_client(monkeypatch, gen)
    written = tmp_path / "out" / "baseline.json"
    args = _cli_args(
        data=str(referenced),
        max_tokens=1_000_000,
        out=str(tmp_path / "first.md"),
        write_baseline=str(written),
    )
    assert await async_main(args) == 0
    payload = json.loads(written.read_text(encoding="utf-8"))
    assert payload["check_ok_rate"] == 1.0
    assert payload["runs"] == 1
    assert "Сиди" not in written.read_text(encoding="utf-8")
    again = _cli_args(
        data=str(referenced),
        max_tokens=1_000_000,
        out=str(tmp_path / "second.md"),
        baseline=str(written),
    )
    assert await async_main(again) == 0
    assert "| G8 |" in (tmp_path / "second.md").read_text(encoding="utf-8")
    worse = FakeTextGenerator(
        rewrite_result=_result(
            Variant(text="Сиди дома, жди", kind=VariantKind.CHECK),
            Variant(text="Пожалуйста, посиди дома", kind=VariantKind.SOFTER),
            Variant(text="Жди дома", kind=VariantKind.FIRMER),
        )
    )
    _patch_client(monkeypatch, worse)
    assert await async_main(again) == 1


@pytest.mark.unit
async def test_cli_write_baseline_needs_one_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_client(monkeypatch, FakeTextGenerator())
    args = _cli_args(
        data=_one_case(tmp_path),
        max_tokens=1000,
        out=str(tmp_path / "r.md"),
        write_baseline=str(tmp_path / "b.json"),
        model=["A", "B"],
    )
    with pytest.raises(SystemExit, match="exactly one --model"):
        await async_main(args)


@pytest.mark.unit
def test_main_accepts_repeatable_model(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("svoi_pravila.evals.rewrite_cli.LlmToolSettings", make_settings)
    monkeypatch.setattr(
        "sys.argv",
        [
            "svoi-pravila-eval-rewrite",
            "--data",
            str(_DATA),
            "--dry-run",
            "--model",
            "A",
            "--model",
            "B",
        ],
    )
    with pytest.raises(SystemExit):
        main()
    assert "Models: A, B" in capsys.readouterr().out


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
    assert f"Total planned calls: {len(load_rewrite_cases(_DATA)) * 3}\n" in capsys.readouterr().out
