"""Offline tests for deterministic rule_leak / rule_effect checkers."""

from __future__ import annotations

import pytest
from tests.fakes.generation import FakeTextGenerator

from svoi_pravila.application.crisis_screen import CrisisScreen
from svoi_pravila.application.ports.generation import (
    GenerationMeta,
    SafetyVerdict,
    SoftenResult,
    TokenUsage,
    Variant,
)
from svoi_pravila.benchmarks.out_writer import OutWriter
from svoi_pravila.benchmarks.runner import SpendTracker
from svoi_pravila.domain.enums import Firmness
from svoi_pravila.evals.cases import filter_cases, load_cases, parse_case
from svoi_pravila.evals.metrics import (
    rule_effect_compliance_with,
    rule_effect_compliance_without,
    rule_effect_delta,
    rule_leak_count,
)
from svoi_pravila.evals.rule_checks import (
    variant_complies_with_closing_ask,
    variant_leaks_stems,
    variants_comply_with_closing_ask,
)
from svoi_pravila.evals.runner import EvalRuntime, run_eval_case

_DATA = (
    __import__("pathlib").Path(__file__).resolve().parents[3] / "evals" / "data" / "eval_v1.jsonl"
)


@pytest.mark.unit
def test_variant_leaks_stems_detects_absent_stem() -> None:
    assert variant_leaks_stems(
        draft="я тебя очень люблю",
        variants=("давай обсудим договор заранее",),
        stop_stems=("договор", "заранее"),
    )
    assert not variant_leaks_stems(
        draft="я тебя очень люблю",
        variants=("я тоже тебя очень люблю",),
        stop_stems=("договор", "заранее"),
    )
    assert not variant_leaks_stems(
        draft="наш договор важен",
        variants=("давай вернёмся к договору",),
        stop_stems=("договор",),
    )


@pytest.mark.unit
def test_closing_ask_compliance() -> None:
    assert variant_complies_with_closing_ask("Мне важно это обсудить. Можешь ответить?")
    assert variant_complies_with_closing_ask("Давай спокойно поговорим об этом.")
    assert not variant_complies_with_closing_ask("Мне важно это обсудить без давления.")
    assert variants_comply_with_closing_ask(("Вопрос один?", "Можешь помочь с этим?"))
    assert not variants_comply_with_closing_ask(("Вопрос один?", "Просто факт."))


@pytest.mark.unit
def test_rule_leak_cases_parse_and_filter() -> None:
    cases = load_cases(_DATA)
    leaks = filter_cases(cases, categories=("rule_leak",))
    assert len(leaks) >= 10
    assert all(c.stop_stems for c in leaks)
    effects = filter_cases(cases, categories=("rule_effect",))
    assert len(effects) == 8
    with_rules = [c for c in effects if c.has_rules()]
    without = [c for c in effects if not c.has_rules()]
    assert len(with_rules) == 4
    assert len(without) == 4


@pytest.mark.unit
async def test_runner_rule_metrics_offline() -> None:
    leak_case = parse_case(
        {
            "id": "offline-leak",
            "operation": "soften",
            "category": "rule_leak",
            "expected": "ok",
            "draft": "я тебя очень люблю",
            "rules": [
                {
                    "category": "how_to_ask",
                    "text": "Не обсуждать договоры заранее",
                    "effective_since": "2026-01-01T00:00:00+00:00",
                }
            ],
            "stop_stems": ["договор", "заранее"],
        }
    )
    effect_with = parse_case(
        {
            "id": "offline-effect-with",
            "operation": "soften",
            "category": "rule_effect",
            "expected": "ok",
            "draft": "Мне нужно чтобы ты предупреждал",
            "rules": [
                {
                    "category": "how_to_ask",
                    "text": "Каждый вариант заканчивать одним конкретным вопросом или просьбой",
                    "effective_since": "2026-01-01T00:00:00+00:00",
                }
            ],
            "effect_pair": "offline-1",
        }
    )
    effect_without = parse_case(
        {
            "id": "offline-effect-without",
            "operation": "soften",
            "category": "rule_effect",
            "expected": "ok",
            "draft": "Мне нужно чтобы ты предупреждал",
            "effect_pair": "offline-1",
        }
    )
    gen = FakeTextGenerator(
        soften_result=SoftenResult(
            variants=(
                Variant(text="давай обсудим договор заранее", firmness=Firmness.GENTLE),
                Variant(text="можешь сказать мягче?", firmness=Firmness.BALANCED),
            ),
            applied_rule_indexes=(0,),
            safety=SafetyVerdict.OK,
            meta=GenerationMeta(
                model="fake",
                prompt_version="soften@v4",
                latency_ms=1,
                attempts=1,
                usage=TokenUsage(input=1, output=1),
            ),
        )
    )
    runtime = EvalRuntime(
        out=OutWriter(None),
        spend=SpendTracker(),
        screen=CrisisScreen.load_ru_v2(),
    )
    leak_rec = await run_eval_case(gen, leak_case, runtime, deadline=1.0, show_outputs=False)
    assert leak_rec.rule_leak is True
    assert rule_leak_count([leak_rec]) == 1

    gen.soften_result = SoftenResult(
        variants=(
            Variant(
                text="Мне важно знать заранее. Можешь предупреждать?", firmness=Firmness.GENTLE
            ),
            Variant(text="Давай договоримся о предупреждениях.", firmness=Firmness.BALANCED),
        ),
        applied_rule_indexes=(0,),
        safety=SafetyVerdict.OK,
        meta=GenerationMeta(
            model="fake",
            prompt_version="soften@v4",
            latency_ms=1,
            attempts=1,
            usage=TokenUsage(input=1, output=1),
        ),
    )
    with_rec = await run_eval_case(gen, effect_with, runtime, deadline=1.0, show_outputs=False)
    gen.soften_result = SoftenResult(
        variants=(
            Variant(text="Мне важно знать заранее о планах.", firmness=Firmness.GENTLE),
            Variant(text="Предупреждай меня если опаздываешь.", firmness=Firmness.BALANCED),
        ),
        applied_rule_indexes=(),
        safety=SafetyVerdict.OK,
        meta=GenerationMeta(
            model="fake",
            prompt_version="soften@v4",
            latency_ms=1,
            attempts=1,
            usage=TokenUsage(input=1, output=1),
        ),
    )
    without_rec = await run_eval_case(
        gen, effect_without, runtime, deadline=1.0, show_outputs=False
    )
    rows = [with_rec, without_rec]
    assert rule_effect_compliance_with(rows) == 1.0
    assert rule_effect_compliance_without(rows) == 0.0
    assert rule_effect_delta(rows) == 1.0
