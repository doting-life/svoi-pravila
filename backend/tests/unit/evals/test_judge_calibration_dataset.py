"""The committed judge calibration set: schema, quotas and independence from other texts."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from svoi_pravila.application.ports.rewrite_judge import SlotVerdicts, Verdict
from svoi_pravila.domain.enums import RewriteGoal
from svoi_pravila.domain.lexicon import normalize_lexicon_text
from svoi_pravila.evals.judge_calibration import CRITERIA, SLOT_CRITERIA, defect_value
from svoi_pravila.evals.judge_cases import (
    SOURCE_EVAL_OUTPUT,
    SOURCE_SYNTHETIC,
    JudgeCase,
    load_judge_cases,
    parse_judge_case,
)

_ROOT = Path(__file__).resolve().parents[3]
_DATA = _ROOT / "evals" / "data" / "judge_calibration_v1.jsonl"
_OTHER_DRAFTS = (
    _ROOT / "evals" / "data" / "rewrite_v2.jsonl",
    _ROOT / "evals" / "data" / "eval_v1.jsonl",
)
_PROMPTS = _ROOT / "src" / "svoi_pravila" / "adapters" / "llm" / "prompts"
_DRAFT_LINE = re.compile(r"^draft:\s*(.+)$", re.MULTILINE)
_FAILURE_PATTERNS = (
    "fail-cant-",
    "fail-cond-",
    "fail-ban-",
    "fail-recon-",
    "fail-excuse-",
    "fail-soft-",
    "fail-punch-",
    "fail-same-",
)
_MIN_ITEMS = 80
_MIN_LANGUAGE_ITEMS = 24
_MIN_PER_GOAL = 6
_MIN_WITH_RULES = 8
_MIN_PER_FAILURE_PATTERN = 3
_MIN_DEFECT_SHARE = 0.25
_MAX_FAMILY_ITEMS = 8
_MAX_SOFTER_PATTERN_ITEMS = 6
_MAX_FIRMER_PATTERN_ITEMS = 6
_MIN_ELIGIBLE_PAIRS = 40
_MIN_LADDER_NO_SHARE = 0.3
_MIN_EVAL_OUTPUT = 50
_MIN_EVAL_PROFANITY = 15
_MIN_EVAL_GOAL = 15
_MIN_EVAL_LADDER_OR_BASE = 10
_MIN_EVAL_FULLY_CORRECT = 10
_SOFTER_PATTERN = re.compile(r"иногда|бывает|бывают|случа(ет|ют)ся", re.IGNORECASE)
_FIRMER_PATTERN = re.compile(r",\s+[^\s,.!?]+[.!?]*$")
_FAMILY_SUFFIX = re.compile(r"-\d+$")
_MIN_SINGLE_DEFECT_SHARE = 0.6
_MAX_WRECK_ITEMS = 16
_MIN_COLLOQUIAL_CLEAN = 6
_MIN_OBSCENE_CLEAN_NO = 6
_COLLOQUIAL = ("чёрт", "офигеть", "ни фига себе", "фиг с ним", "блин")
_OBSCENE_ROOTS = (
    "нихера",
    "сучий",
    "залупа",
    "дрочишь",
    "падла",
    "елдак",
    "гондон",
    "гомна",
)


def _cases() -> list[JudgeCase]:
    return load_judge_cases(_DATA)


def _synthetic() -> list[JudgeCase]:
    return [case for case in _cases() if case.source == SOURCE_SYNTHETIC]


def _eval_output() -> list[JudgeCase]:
    return [case for case in _cases() if case.source == SOURCE_EVAL_OUTPUT]


def _values(cases: list[JudgeCase], criterion: str) -> list[Verdict]:
    if criterion == "ladder_ok":
        return [case.expected.ladder_ok for case in cases]
    return [
        getattr(getattr(case.expected, slot), criterion)
        for case in cases
        for slot in ("softer", "firmer")
    ]


@pytest.mark.unit
def test_the_set_has_at_least_eighty_items_with_unique_ids() -> None:
    cases = _cases()
    assert len(cases) >= _MIN_ITEMS
    assert len({case.id for case in cases}) == len(cases)


@pytest.mark.unit
def test_language_items_without_goal_and_rules_are_at_least_twenty_four() -> None:
    cases = [case for case in _cases() if case.goal is None and not case.rules]
    assert len(cases) >= _MIN_LANGUAGE_ITEMS
    flagged = [
        case
        for case in cases
        if Verdict.NO in (case.expected.softer.clean, case.expected.firmer.clean)
    ]
    passing = [
        case
        for case in cases
        if Verdict.YES in (case.expected.softer.clean, case.expected.firmer.clean)
    ]
    assert len(flagged) >= _MIN_LANGUAGE_ITEMS // 2
    assert len(passing) >= _MIN_LANGUAGE_ITEMS // 2


@pytest.mark.unit
def test_every_goal_has_six_items_with_passing_and_failing_variants() -> None:
    cases = _cases()
    per_goal = Counter(case.goal for case in cases if case.goal is not None)
    assert set(per_goal) == set(RewriteGoal)
    assert all(count >= _MIN_PER_GOAL for count in per_goal.values())
    for goal in RewriteGoal:
        verdicts = set(_values([case for case in cases if case.goal is goal], "goal_done"))
        assert {Verdict.YES, Verdict.NO} <= verdicts, goal


@pytest.mark.unit
def test_at_least_eight_items_have_rules_with_satisfied_and_violated_labels() -> None:
    cases = [case for case in _cases() if case.rules]
    assert len(cases) >= _MIN_WITH_RULES
    verdicts = set(_values(cases, "rule_done"))
    assert {Verdict.YES, Verdict.NO} <= verdicts
    categories = {rule.category.value for case in cases for rule in case.rules}
    assert {"how_to_ask", "apology", "taboo_topic"} <= categories


@pytest.mark.unit
@pytest.mark.parametrize("prefix", _FAILURE_PATTERNS)
def test_every_failure_pattern_has_at_least_three_items(prefix: str) -> None:
    assert sum(case.id.startswith(prefix) for case in _cases()) >= _MIN_PER_FAILURE_PATTERN


@pytest.mark.unit
@pytest.mark.parametrize("criterion", CRITERIA)
def test_defect_label_share_per_criterion_is_at_least_twenty_five_percent(criterion: str) -> None:
    # The synthetic items that are not part of the good-pair coverage; the real outputs
    # keep the distribution of the model and the eligible pairs are good by definition.
    cases = [case for case in _synthetic() if not case.ladder_eligible]
    values = [value for value in _values(cases, criterion) if value is not Verdict.NA]
    assert values
    share = sum(value is defect_value(criterion) for value in values) / len(values)
    assert share >= _MIN_DEFECT_SHARE


def _is_defect(slot: SlotVerdicts, criterion: str) -> bool:
    return bool(getattr(slot, criterion) is defect_value(criterion))


@pytest.mark.unit
@pytest.mark.parametrize("criterion", SLOT_CRITERIA)
def test_most_defect_labels_of_a_criterion_are_its_only_defect_in_the_slot(criterion: str) -> None:
    slots = [getattr(case.expected, name) for case in _cases() for name in ("softer", "firmer")]
    defects = [slot for slot in slots if _is_defect(slot, criterion)]
    only = [
        slot
        for slot in defects
        if not any(_is_defect(slot, other) for other in SLOT_CRITERIA if other != criterion)
    ]
    assert defects
    assert len(only) / len(defects) >= _MIN_SINGLE_DEFECT_SHARE


@pytest.mark.unit
def test_at_most_sixteen_items_are_wrecks_and_the_rest_are_at_least_eighty() -> None:
    cases = _cases()
    wrecks = [case for case in cases if case.id.startswith("wreck-")]
    assert len(wrecks) <= _MAX_WRECK_ITEMS
    assert len(cases) - len(wrecks) >= _MIN_ITEMS


@pytest.mark.unit
def test_colloquial_words_that_are_not_vulgar_are_clean() -> None:
    clean = [
        case
        for case in _cases()
        for name in ("softer", "firmer")
        if any(word in getattr(case, name).lower() for word in _COLLOQUIAL)
        and getattr(case.expected, name).clean is Verdict.YES
    ]
    assert len({case.id for case in clean}) >= _MIN_COLLOQUIAL_CLEAN


@pytest.mark.unit
def test_obscene_forms_are_flagged_and_each_form_is_used_once() -> None:
    cases = _synthetic()
    texts = [
        (case.id, name, getattr(case, name)) for case in cases for name in ("softer", "firmer")
    ]
    for root in _OBSCENE_ROOTS:
        users = {(item, name) for item, name, text in texts if root in text.lower()}
        assert len(users) == 1, root
        ((item, name),) = users
        flagged = next(case for case in cases if case.id == item)
        assert getattr(flagged.expected, name).clean is Verdict.NO, root
    assert len(_OBSCENE_ROOTS) >= _MIN_OBSCENE_CLEAN_NO


@pytest.mark.unit
def test_labels_follow_the_na_rules() -> None:
    for case in _cases():
        for slot in (case.expected.softer, case.expected.firmer):
            assert (case.goal is None) == (slot.goal_done is Verdict.NA), case.id
            assert case.rules or slot.rule_done is Verdict.NA, case.id
            assert Verdict.NA not in (slot.clean, slot.meaning_kept, slot.invented), case.id


@pytest.mark.unit
def test_no_synthetic_draft_equals_another_dataset_draft_or_a_prompt_example() -> None:
    other = {
        normalize_lexicon_text(str(json.loads(line).get("draft", "")))
        for path in _OTHER_DRAFTS
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    examples = {
        normalize_lexicon_text(match)
        for path in _PROMPTS.rglob("*.md")
        for match in _DRAFT_LINE.findall(path.read_text(encoding="utf-8"))
    }
    drafts = {normalize_lexicon_text(case.draft) for case in _synthetic()}
    assert not drafts & (other | examples)


@pytest.mark.unit
def test_variants_are_never_blank_and_synthetic_drafts_are_unique() -> None:
    assert all(case.softer.strip() and case.firmer.strip() for case in _cases())
    drafts = [normalize_lexicon_text(case.draft) for case in _synthetic() if case.goal is None]
    assert len(set(drafts)) == len(drafts)


def _is_good(slot: SlotVerdicts) -> bool:
    return slot.all_good


@pytest.mark.unit
def test_every_item_has_a_source_and_the_real_output_block_has_at_least_fifty_pairs() -> None:
    assert {case.source for case in _cases()} == {SOURCE_SYNTHETIC, SOURCE_EVAL_OUTPUT}
    assert len(_eval_output()) >= _MIN_EVAL_OUTPUT
    assert all(case.id.startswith("eo-") for case in _eval_output())
    assert not any(case.id.startswith("eo-") for case in _synthetic())


@pytest.mark.unit
def test_the_real_output_block_is_stratified() -> None:
    cases = _eval_output()
    profanity = [case for case in cases if case.id.startswith("eo-prof-")]
    goal = [case for case in cases if case.id.startswith("eo-goal-")]
    ladder_or_base = [case for case in cases if case.id.startswith(("eo-ladder-", "eo-i"))]
    fully_correct = [
        case for case in cases if case.ladder_eligible and case.expected.ladder_ok is Verdict.YES
    ]
    assert len(profanity) >= _MIN_EVAL_PROFANITY
    assert len(goal) >= _MIN_EVAL_GOAL
    assert len(ladder_or_base) >= _MIN_EVAL_LADDER_OR_BASE
    assert len(fully_correct) >= _MIN_EVAL_FULLY_CORRECT


@pytest.mark.unit
def test_every_synthetic_id_family_has_at_most_eight_items() -> None:
    families = Counter(_FAMILY_SUFFIX.sub("", case.id) for case in _synthetic())
    assert max(families.values()) <= _MAX_FAMILY_ITEMS, families.most_common(3)


@pytest.mark.unit
def test_the_softer_aside_pattern_is_used_at_most_six_times() -> None:
    users = [case.id for case in _synthetic() if _SOFTER_PATTERN.search(case.softer)]
    assert len(users) <= _MAX_SOFTER_PATTERN_ITEMS, users


@pytest.mark.unit
def test_the_firmer_trailing_insult_pattern_is_used_at_most_six_times() -> None:
    users = [
        case.id
        for case in _synthetic()
        if _FIRMER_PATTERN.search(case.firmer.strip()) and case.expected.firmer.clean is Verdict.NO
    ]
    assert len(users) <= _MAX_FIRMER_PATTERN_ITEMS, users


@pytest.mark.unit
def test_at_least_forty_pairs_are_eligible_and_a_third_of_them_break_the_ladder() -> None:
    eligible = [case for case in _cases() if case.ladder_eligible]
    assert len(eligible) >= _MIN_ELIGIBLE_PAIRS
    broken = [case for case in eligible if case.expected.ladder_ok is Verdict.NO]
    assert len(broken) / len(eligible) >= _MIN_LADDER_NO_SHARE


@pytest.mark.unit
def test_an_eligible_pair_has_only_good_slot_labels() -> None:
    for case in _cases():
        good = _is_good(case.expected.softer) and _is_good(case.expected.firmer)
        assert case.ladder_eligible is good, case.id


def _item() -> dict[str, Any]:
    labels = {
        "clean": "yes",
        "meaning_kept": "yes",
        "invented": "no",
        "goal_done": "na",
        "rule_done": "na",
    }
    return {
        "id": "x",
        "source": "synthetic",
        "draft": "d",
        "goal": None,
        "rules": [],
        "softer": "s",
        "firmer": "f",
        "expected": {"softer": dict(labels), "firmer": dict(labels), "ladder_ok": "yes"},
    }


@pytest.mark.unit
def test_a_well_formed_item_parses() -> None:
    assert parse_judge_case(_item()).id == "x"


@pytest.mark.unit
def test_an_unknown_source_is_rejected() -> None:
    raw = _item()
    raw["source"] = "invented"
    with pytest.raises(ValueError, match="source"):
        parse_judge_case(raw)


@pytest.mark.unit
def test_a_missing_source_is_rejected() -> None:
    raw = _item()
    del raw["source"]
    with pytest.raises(ValueError, match="missing"):
        parse_judge_case(raw)


@pytest.mark.unit
def test_an_unknown_field_is_rejected() -> None:
    raw = _item()
    raw["note"] = "x"
    with pytest.raises(ValueError, match="unknown"):
        parse_judge_case(raw)


@pytest.mark.unit
def test_a_missing_label_is_rejected() -> None:
    raw = _item()
    del raw["expected"]["softer"]["clean"]
    with pytest.raises(ValueError, match="missing"):
        parse_judge_case(raw)


@pytest.mark.unit
def test_an_unknown_verdict_is_rejected() -> None:
    raw = _item()
    raw["expected"]["ladder_ok"] = "maybe"
    with pytest.raises(ValueError, match="yes, no, na"):
        parse_judge_case(raw)


@pytest.mark.unit
def test_ladder_ok_is_never_na() -> None:
    raw = _item()
    raw["expected"]["ladder_ok"] = "na"
    with pytest.raises(ValueError, match="never na"):
        parse_judge_case(raw)


@pytest.mark.unit
def test_goal_done_is_na_exactly_when_there_is_no_goal() -> None:
    raw = _item()
    raw["goal"] = "decline"
    with pytest.raises(ValueError, match="goal_done"):
        parse_judge_case(raw)


@pytest.mark.unit
def test_rule_done_must_be_na_without_rules() -> None:
    raw = _item()
    raw["expected"]["firmer"]["rule_done"] = "yes"
    with pytest.raises(ValueError, match="rule_done"):
        parse_judge_case(raw)


@pytest.mark.unit
def test_loading_reports_the_line_number(tmp_path: Path) -> None:
    path = tmp_path / "items.jsonl"
    path.write_text(json.dumps(_item()) + "\n" + json.dumps({"id": "y"}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"items\.jsonl:2"):
        load_judge_cases(path)


@pytest.mark.unit
def test_duplicate_ids_and_empty_files_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "items.jsonl"
    path.write_text(json.dumps(_item()) + "\n" + json.dumps(_item()) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unique ids"):
        load_judge_cases(path)
    path.write_text("\n", encoding="utf-8")
    with pytest.raises(ValueError, match="at least one item"):
        load_judge_cases(path)
