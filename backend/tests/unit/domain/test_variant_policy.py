"""Variant policy v3: per-variant rules, licences and the ladder between softer and firmer."""

from __future__ import annotations

import pytest

from svoi_pravila.application.lexicons import load_tone_lexicons_ru_v1
from svoi_pravila.domain.enums import RewriteGoal, RuleCategory, VariantKind
from svoi_pravila.domain.variant_policy import (
    POLICY_VERSION,
    STOP_PHRASES,
    PolicyContext,
    PolicyRule,
    PolicyViolation,
    check_variant_set,
)

_LEXICONS = load_tone_lexicons_ru_v1()
_PLAIN = PolicyContext(goal=None, rule_categories=frozenset())


def _ctx(goal: RewriteGoal | None = None, *categories: RuleCategory) -> PolicyContext:
    return PolicyContext(goal=goal, rule_categories=frozenset(categories))


def _rules(
    draft: str,
    kind: VariantKind,
    text: str,
    context: PolicyContext = _PLAIN,
) -> tuple[PolicyRule, ...]:
    """Rules broken by a single variant (the set holds only that variant)."""
    violations = check_variant_set(draft, {kind: text}, context, _LEXICONS)
    assert all(violation.kind is kind for violation in violations)
    return tuple(violation.rule for violation in violations)


def _set(
    draft: str,
    softer: str,
    firmer: str,
    context: PolicyContext = _PLAIN,
    check: str | None = None,
) -> tuple[PolicyViolation, ...]:
    texts = {VariantKind.SOFTER: softer, VariantKind.FIRMER: firmer}
    if check is not None:
        texts[VariantKind.CHECK] = check
    return check_variant_set(draft, texts, context, _LEXICONS)


@pytest.mark.unit
def test_policy_version_is_three() -> None:
    assert POLICY_VERSION == 3


@pytest.mark.unit
@pytest.mark.parametrize("phrase", STOP_PHRASES)
@pytest.mark.parametrize("kind", list(VariantKind))
def test_stop_phrase_is_reported_case_insensitively(phrase: str, kind: VariantKind) -> None:
    draft = "Ты опять опоздал"
    text = f"Ты опять опоздал. {phrase.upper()}"
    assert PolicyRule.STOP_PHRASE in _rules(draft, kind, text, _ctx(RewriteGoal.RECONNECT))


@pytest.mark.unit
def test_stop_phrase_already_in_draft_is_allowed() -> None:
    draft = "Я ценю твою помощь, но так нельзя"
    assert _rules(draft, VariantKind.CHECK, "Я ценю твою помощь, но так нельзя.") == ()


@pytest.mark.unit
def test_stop_phrase_matches_ye_spelling_of_yo() -> None:
    draft = "Ты опять опоздал"
    text = "Ты опять опоздал. Давай вместе разберемся."
    assert PolicyRule.STOP_PHRASE in _rules(draft, VariantKind.SOFTER, text)


@pytest.mark.unit
def test_stop_phrase_matches_without_its_comma() -> None:
    draft = "Ты опять опоздал"
    text = "Я понимаю что ты занят."
    assert PolicyRule.STOP_PHRASE in _rules(draft, VariantKind.SOFTER, text)


@pytest.mark.unit
def test_stop_phrase_split_by_punctuation_is_reported() -> None:
    draft = "Ты опять опоздал"
    text = "Давай вместе... разберёмся!"
    assert PolicyRule.STOP_PHRASE in _rules(draft, VariantKind.SOFTER, text)


@pytest.mark.unit
def test_stop_phrase_words_must_be_contiguous() -> None:
    draft = "Ты опять опоздал"
    text = "Давай вместе всё разберёмся."
    assert PolicyRule.STOP_PHRASE not in _rules(draft, VariantKind.SOFTER, text)


@pytest.mark.unit
def test_stop_phrase_in_draft_with_other_spelling_is_allowed() -> None:
    draft = "Давай вместе разберёмся с этим"
    text = "Давай вместе разберемся с этим."
    assert _rules(draft, VariantKind.CHECK, text) == ()


@pytest.mark.unit
def test_violation_is_a_value_without_text() -> None:
    assert PolicyViolation(PolicyRule.LENGTH, VariantKind.SOFTER) == PolicyViolation(
        PolicyRule.LENGTH, VariantKind.SOFTER
    )


@pytest.mark.unit
def test_empty_set_has_no_violations() -> None:
    assert check_variant_set("Ты опять опоздал", {}, _PLAIN, _LEXICONS) == ()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("draft_words", "text_words", "kind", "violates"),
    [
        (10, 12, VariantKind.FIRMER, False),
        (10, 13, VariantKind.FIRMER, True),
        (3, 4, VariantKind.FIRMER, False),
        (3, 5, VariantKind.FIRMER, True),
        (10, 16, VariantKind.SOFTER, False),
        (10, 17, VariantKind.SOFTER, True),
        (100, 120, VariantKind.SOFTER, False),
        (100, 121, VariantKind.SOFTER, True),
        (2, 8, VariantKind.SOFTER, False),
        (2, 9, VariantKind.SOFTER, True),
        (2, 50, VariantKind.CHECK, False),
    ],
)
def test_length_limits_without_licence(
    draft_words: int, text_words: int, kind: VariantKind, *, violates: bool
) -> None:
    draft = " ".join(["слово"] * draft_words)
    text = " ".join(["слово"] * text_words)
    assert (PolicyRule.LENGTH in _rules(draft, kind, text)) is violates


@pytest.mark.unit
@pytest.mark.parametrize(
    ("draft_words", "text_words", "kind", "violates"),
    [
        (10, 20, VariantKind.FIRMER, False),
        (10, 21, VariantKind.FIRMER, True),
        (3, 12, VariantKind.FIRMER, False),
        (3, 13, VariantKind.FIRMER, True),
        (10, 24, VariantKind.SOFTER, False),
        (10, 25, VariantKind.SOFTER, True),
        (100, 120, VariantKind.SOFTER, False),
        (100, 121, VariantKind.SOFTER, True),
        (2, 16, VariantKind.SOFTER, False),
        (2, 17, VariantKind.SOFTER, True),
        (2, 50, VariantKind.CHECK, False),
    ],
)
@pytest.mark.parametrize(
    "licence",
    [_ctx(RewriteGoal.DECLINE), _ctx(None, RuleCategory.HOW_TO_ASK)],
    ids=["goal", "rule"],
)
def test_length_limits_are_extended_by_a_goal_or_a_rule(
    licence: PolicyContext,
    draft_words: int,
    text_words: int,
    kind: VariantKind,
    *,
    violates: bool,
) -> None:
    draft = " ".join(["слово"] * draft_words)
    text = " ".join(["слово"] * text_words)
    assert (PolicyRule.LENGTH in _rules(draft, kind, text, licence)) is violates


@pytest.mark.unit
def test_check_edit_ratio_boundary() -> None:
    draft = "а" * 100
    assert _rules(draft, VariantKind.CHECK, draft) == ()
    assert PolicyRule.EDIT_RATIO in _rules(draft, VariantKind.CHECK, "б" * 100)
    assert PolicyRule.EDIT_RATIO not in _rules(draft, VariantKind.SOFTER, "б" * 10)


@pytest.mark.unit
def test_check_with_small_correction_passes() -> None:
    draft = "Ты опять не сделал то что обещал. Сколько можно?"
    text = "Ты опять не сделал то, что обещал. Сколько можно?"
    assert _rules(draft, VariantKind.CHECK, text) == ()


@pytest.mark.unit
def test_check_keeps_profanity_harsh_hedge_and_apology_of_the_author() -> None:
    draft = "Ты всегда мудак, извини, пожалуйста"
    assert _rules(draft, VariantKind.CHECK, "Ты всегда мудак, извини, пожалуйста.") == ()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("draft", "text", "violates"),
    [
        ("Ты опять не сделал", "Вы снова не сделали", True),
        ("Ты опять не сделал", "Ты снова не сделал", False),
        ("Вы опять не сделали", "Ты снова не сделал", True),
        ("Вам стоит подумать", "Вам стоит подумать", False),
        ("Ты и вы оба тут", "Ты и вы оба тут", False),
        ("Ты и вы оба тут", "Вы оба тут", False),
        ("Сиди дома и жди", "Вы сидите дома", False),
        ("Сиди дома и жди", "Ты сиди дома", False),
    ],
)
def test_register_rule(draft: str, text: str, *, violates: bool) -> None:
    assert (PolicyRule.REGISTER in _rules(draft, VariantKind.SOFTER, text)) is violates


@pytest.mark.unit
@pytest.mark.parametrize("kind", [VariantKind.SOFTER, VariantKind.FIRMER])
def test_profanity_in_softer_or_firmer_is_reported(kind: VariantKind) -> None:
    assert PolicyRule.PROFANITY in _rules("Ну что, ребята, готовы?", kind, "Ну что, хуй с ними")
    assert PolicyRule.PROFANITY in _rules("Ну что, ребята, готовы?", kind, "Ну что, бл*дь")
    assert PolicyRule.PROFANITY not in _rules("Ну что, ребята, готовы?", kind, "Ну что, готовы?")


@pytest.mark.unit
@pytest.mark.parametrize("kind", [VariantKind.SOFTER, VariantKind.FIRMER])
def test_profanity_is_reported_even_when_the_draft_has_it(kind: VariantKind) -> None:
    assert PolicyRule.PROFANITY in _rules("Это пиздец", kind, "Это пиздец")


@pytest.mark.unit
@pytest.mark.parametrize("kind", [VariantKind.SOFTER, VariantKind.FIRMER])
def test_harsh_markers_in_softer_or_firmer_are_reported(kind: VariantKind) -> None:
    assert PolicyRule.HARSH in _rules("Ты опоздал", kind, "Ты всегда опаздываешь")
    assert PolicyRule.HARSH in _rules("Ты опоздал", kind, "Не будь идиотом")
    assert PolicyRule.HARSH not in _rules("Ты опоздал", kind, "Я ждал тебя")


@pytest.mark.unit
def test_check_is_not_measured_for_profanity_or_harsh() -> None:
    rules = _rules("Ты тупой", VariantKind.CHECK, "Ты тупой, хуй.")
    assert PolicyRule.PROFANITY not in rules
    assert PolicyRule.HARSH not in rules


@pytest.mark.unit
def test_new_hedge_in_firmer_is_reported_without_rules() -> None:
    assert PolicyRule.HEDGE in _rules(
        "Верни деньги", VariantKind.FIRMER, "Верни деньги, пожалуйста"
    )


@pytest.mark.unit
def test_hedge_already_in_the_draft_is_not_new() -> None:
    draft = "Пожалуйста, верни деньги"
    assert PolicyRule.HEDGE not in _rules(draft, VariantKind.FIRMER, "Пожалуйста, верни деньги")
    assert PolicyRule.HEDGE in _rules(draft, VariantKind.FIRMER, "Пожалуйста, можешь вернуть")


@pytest.mark.unit
def test_hedge_is_checked_on_firmer_only() -> None:
    assert PolicyRule.HEDGE not in _rules("Верни деньги", VariantKind.SOFTER, "Верни, пожалуйста")


@pytest.mark.unit
def test_hedge_is_not_checked_when_the_context_has_rules() -> None:
    context = _ctx(None, RuleCategory.OTHER)
    text = "Верни деньги, пожалуйста"
    assert PolicyRule.HEDGE not in _rules("Верни деньги", VariantKind.FIRMER, text, context)


@pytest.mark.unit
def test_hedge_is_checked_when_only_a_goal_is_set() -> None:
    context = _ctx(RewriteGoal.DECLINE)
    text = "Верни деньги, пожалуйста"
    assert PolicyRule.HEDGE in _rules("Верни деньги", VariantKind.FIRMER, text, context)


@pytest.mark.unit
def test_davai_is_not_a_hedge() -> None:
    assert PolicyRule.HEDGE not in _rules("Помиримся", VariantKind.FIRMER, "Давай мириться")


@pytest.mark.unit
@pytest.mark.parametrize("kind", [VariantKind.SOFTER, VariantKind.FIRMER])
def test_new_apology_without_licence_is_reported(kind: VariantKind) -> None:
    assert PolicyRule.APOLOGY in _rules("Я опоздал", kind, "Извини, я опоздал")
    assert PolicyRule.APOLOGY not in _rules("Я опоздал", kind, "Я опоздал")


@pytest.mark.unit
@pytest.mark.parametrize("kind", [VariantKind.SOFTER, VariantKind.FIRMER])
@pytest.mark.parametrize(
    "licence",
    [_ctx(RewriteGoal.ADMIT_FAULT), _ctx(None, RuleCategory.APOLOGY)],
    ids=["goal", "rule"],
)
def test_one_new_apology_is_allowed_by_a_licence(kind: VariantKind, licence: PolicyContext) -> None:
    assert PolicyRule.APOLOGY not in _rules("Я опоздал", kind, "Извини, я опоздал", licence)
    assert PolicyRule.APOLOGY in _rules("Я опоздал", kind, "Извини, я опоздал, мне жаль", licence)


@pytest.mark.unit
def test_an_apology_already_in_the_draft_is_not_new() -> None:
    assert PolicyRule.APOLOGY not in _rules("Извини, я опоздал", VariantKind.SOFTER, "Извини")
    assert PolicyRule.APOLOGY in _rules(
        "Извини, я опоздал", VariantKind.SOFTER, "Извини, прости, мне жаль"
    )


@pytest.mark.unit
def test_other_goals_and_categories_do_not_license_an_apology() -> None:
    assert PolicyRule.APOLOGY in _rules(
        "Я опоздал",
        VariantKind.SOFTER,
        "Извини, я опоздал",
        _ctx(RewriteGoal.RECONNECT, RuleCategory.HOW_TO_ASK),
    )


@pytest.mark.unit
def test_ladder_length_is_reported_on_firmer_when_it_is_longer() -> None:
    violations = _set("Верни деньги", "Верни деньги сегодня", "Верни деньги сегодня вечером")
    assert PolicyViolation(PolicyRule.LADDER_LENGTH, VariantKind.FIRMER) in violations
    assert all(
        v.kind is VariantKind.FIRMER for v in violations if v.rule is PolicyRule.LADDER_LENGTH
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("softer", "firmer"),
    [
        ("Верни деньги сегодня вечером", "Верни деньги сегодня"),
        ("Верни деньги сегодня", "Деньги верни завтра"),
    ],
)
def test_ladder_length_passes_when_firmer_is_not_longer(softer: str, firmer: str) -> None:
    rules = [v.rule for v in _set("Верни деньги", softer, firmer)]
    assert PolicyRule.LADDER_LENGTH not in rules


@pytest.mark.unit
def test_ladder_similar_is_reported_on_firmer_for_near_identical_variants() -> None:
    violations = _set(
        "Денег не дам, выходи на работу",
        "Денег не дам, выходи на работу",
        "Денег не дам. Выходи на работу",
    )
    assert PolicyViolation(PolicyRule.LADDER_SIMILAR, VariantKind.FIRMER) in violations


@pytest.mark.unit
def test_ladder_similar_passes_for_distinct_variants() -> None:
    violations = _set(
        "Денег не дам, выходи на работу",
        "Мне не подходит давать деньги, найди работу",
        "Денег не дам. Работай",
    )
    assert PolicyRule.LADDER_SIMILAR not in [v.rule for v in violations]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("words", "changed", "violates"),
    [(10, 0, True), (11, 1, True), (10, 1, False), (20, 2, False), (30, 2, True)],
)
def test_ladder_similar_distance_boundary(words: int, changed: int, *, violates: bool) -> None:
    base = ["слово"] * words
    other = [*base[: words - changed], *(["иное"] * changed)]
    rules = [v.rule for v in _set("слово", " ".join(base), " ".join(other))]
    assert (PolicyRule.LADDER_SIMILAR in rules) is violates


@pytest.mark.unit
def test_ladder_rules_need_both_variants() -> None:
    only_softer = check_variant_set(
        "Верни деньги", {VariantKind.SOFTER: "Верни деньги"}, _PLAIN, _LEXICONS
    )
    only_firmer = check_variant_set(
        "Верни деньги", {VariantKind.FIRMER: "Верни деньги"}, _PLAIN, _LEXICONS
    )
    ladder = {PolicyRule.LADDER_LENGTH, PolicyRule.LADDER_SIMILAR}
    assert not ladder & {v.rule for v in only_softer}
    assert not ladder & {v.rule for v in only_firmer}


@pytest.mark.unit
def test_a_clean_ladder_has_no_violations() -> None:
    violations = check_variant_set(
        "Ты опять не отвечаешь на мои сообщения!",
        {
            VariantKind.CHECK: "Ты опять не отвечаешь на мои сообщения!",
            VariantKind.SOFTER: "Мне важно, чтобы ты отвечал на мои сообщения",
            VariantKind.FIRMER: "Отвечай на мои сообщения",
        },
        _PLAIN,
        _LEXICONS,
    )
    assert violations == ()


@pytest.mark.unit
def test_violations_are_listed_per_kind_then_ladder_rules() -> None:
    violations = check_variant_set(
        "Верни деньги",
        {
            VariantKind.FIRMER: "Верни деньги, идиот",
            VariantKind.SOFTER: "Верни деньги, хуй",
        },
        _PLAIN,
        _LEXICONS,
    )
    assert violations == (
        PolicyViolation(PolicyRule.PROFANITY, VariantKind.SOFTER),
        PolicyViolation(PolicyRule.HARSH, VariantKind.FIRMER),
    )
    same = check_variant_set(
        "Верни деньги",
        {VariantKind.FIRMER: "Верни, хуй", VariantKind.SOFTER: "Верни, хуй"},
        _PLAIN,
        _LEXICONS,
    )
    assert same == (
        PolicyViolation(PolicyRule.PROFANITY, VariantKind.SOFTER),
        PolicyViolation(PolicyRule.PROFANITY, VariantKind.FIRMER),
        PolicyViolation(PolicyRule.LADDER_SIMILAR, VariantKind.FIRMER),
    )


@pytest.mark.unit
def test_context_licences() -> None:
    assert not _PLAIN.addition_licensed
    assert not _PLAIN.apology_licensed
    assert _ctx(RewriteGoal.RECONNECT).addition_licensed
    assert _ctx(None, RuleCategory.TABOO_TOPIC).addition_licensed
    assert not _ctx(RewriteGoal.RECONNECT).apology_licensed
    assert _ctx(RewriteGoal.ADMIT_FAULT).apology_licensed
    assert _ctx(None, RuleCategory.APOLOGY).apology_licensed
    assert not _ctx(None, RuleCategory.OTHER).apology_licensed
