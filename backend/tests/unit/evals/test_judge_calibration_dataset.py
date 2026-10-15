"""The committed clean-judge calibration set: schema, quotas and independence from other texts."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from svoi_pravila.application.ports.rewrite_judge import Verdict
from svoi_pravila.domain.lexicon import normalize_lexicon_text
from svoi_pravila.evals.judge_cases import (
    SOURCE_EVAL_OUTPUT,
    SOURCE_SYNTHETIC,
    JudgeCase,
    load_judge_cases,
    parse_judge_case,
)

_ROOT = Path(__file__).resolve().parents[3]
_DATA = _ROOT / "evals" / "data" / "judge_calibration_v1.jsonl"
_PROMPTS = _ROOT / "src" / "svoi_pravila" / "adapters" / "llm" / "prompts"
_MIN_DEFECT_SHARE = 0.25
_MAX_FAMILY_ITEMS = 8
_MAX_FIRMER_PATTERN_ITEMS = 6
_MIN_EVAL_OUTPUT = 50
_FIRMER_PATTERN = re.compile(r",\s+[^\s,.!?]+[.!?]*$")
_FAMILY_SUFFIX = re.compile(r"-\d+$")
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


@pytest.mark.unit
def test_every_item_has_a_source_and_the_real_output_block_has_at_least_fifty_pairs() -> None:
    assert {case.source for case in _cases()} == {SOURCE_SYNTHETIC, SOURCE_EVAL_OUTPUT}
    assert len(_eval_output()) >= _MIN_EVAL_OUTPUT
    assert all(case.id.startswith("eo-") for case in _eval_output())
    assert not any(case.id.startswith("eo-") for case in _synthetic())


@pytest.mark.unit
def test_the_clean_defect_share_of_the_synthetic_items_is_at_least_twenty_five_percent() -> None:
    values = [
        getattr(case.expected, slot) for case in _synthetic() for slot in ("softer", "firmer")
    ]
    assert sum(value is Verdict.NO for value in values) / len(values) >= _MIN_DEFECT_SHARE


@pytest.mark.unit
def test_colloquial_words_that_are_not_vulgar_are_clean() -> None:
    clean = [
        case
        for case in _cases()
        for name in ("softer", "firmer")
        if any(word in getattr(case, name).lower() for word in _COLLOQUIAL)
        and getattr(case.expected, name) is Verdict.YES
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
        assert getattr(flagged.expected, name) is Verdict.NO, root
    assert len(_OBSCENE_ROOTS) >= _MIN_OBSCENE_CLEAN_NO


@pytest.mark.unit
def test_every_synthetic_id_family_has_at_most_eight_items() -> None:
    families = Counter(_FAMILY_SUFFIX.sub("", case.id) for case in _synthetic())
    assert max(families.values()) <= _MAX_FAMILY_ITEMS, families.most_common(3)


@pytest.mark.unit
def test_the_firmer_trailing_insult_pattern_is_used_at_most_six_times() -> None:
    users = [
        case.id
        for case in _synthetic()
        if _FIRMER_PATTERN.search(case.firmer.strip()) and case.expected.firmer is Verdict.NO
    ]
    assert len(users) <= _MAX_FIRMER_PATTERN_ITEMS, users


@pytest.mark.unit
def test_no_quoted_example_of_the_judge_rubric_is_a_dataset_text() -> None:
    rubric = (_PROMPTS / "judge_clean" / "v2.md").read_text(encoding="utf-8")
    examples = {
        normalize_lexicon_text(match.replace("\u2026", " "))
        for match in re.findall("\u00ab([^\u00bb]+)\u00bb", rubric)
    }
    texts = {
        normalize_lexicon_text(text) for case in _cases() for text in (case.softer, case.firmer)
    }
    assert len(examples) > 10
    assert not examples & texts


@pytest.mark.unit
def test_variants_are_never_blank() -> None:
    assert all(case.softer.strip() and case.firmer.strip() for case in _cases())


def _item() -> dict[str, Any]:
    return {
        "id": "x",
        "source": "synthetic",
        "softer": "s",
        "firmer": "f",
        "expected": {"softer": {"clean": "yes"}, "firmer": {"clean": "no"}},
    }


@pytest.mark.unit
def test_a_well_formed_item_parses() -> None:
    case = parse_judge_case(_item())
    assert (case.id, case.expected.softer, case.expected.firmer) == ("x", Verdict.YES, Verdict.NO)


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
@pytest.mark.parametrize("field", ["draft", "goal", "rules", "note"])
def test_an_unknown_field_is_rejected(field: str) -> None:
    raw = _item()
    raw[field] = "x"
    with pytest.raises(ValueError, match="unknown"):
        parse_judge_case(raw)


@pytest.mark.unit
def test_a_missing_label_is_rejected() -> None:
    raw = _item()
    del raw["expected"]["softer"]["clean"]
    with pytest.raises(ValueError, match="missing"):
        parse_judge_case(raw)


@pytest.mark.unit
def test_another_label_in_a_slot_is_rejected() -> None:
    raw = _item()
    raw["expected"]["softer"]["invented"] = "no"
    with pytest.raises(ValueError, match="unknown"):
        parse_judge_case(raw)


@pytest.mark.unit
@pytest.mark.parametrize("verdict", ["maybe", "na"])
def test_an_unknown_verdict_is_rejected(verdict: str) -> None:
    raw = _item()
    raw["expected"]["firmer"]["clean"] = verdict
    with pytest.raises(ValueError, match="yes, no"):
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
