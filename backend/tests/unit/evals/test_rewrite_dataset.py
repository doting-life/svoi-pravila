"""The committed rewrite_v2 dataset: required content, quotas and independence from the prompt."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

import pytest

from svoi_pravila.domain.enums import RewriteGoal, RuleCategory
from svoi_pravila.domain.lexicon import normalize_lexicon_text
from svoi_pravila.evals.rewrite_cases import RewriteCase, RewriteCategory, load_rewrite_cases

_ROOT = Path(__file__).resolve().parents[3]
_DATA = _ROOT / "evals" / "data" / "rewrite_v2.jsonl"
_PROMPTS = _ROOT / "src" / "svoi_pravila" / "adapters" / "llm" / "prompts" / "rewrite"
_REQUIRED_STEMS = ("иначе", "последний раз", "разговор окончен", "нет, значит нет")
_PROHIBITION_STEM = "не пиш"
_RETIRED_STEM = "больше не"
_DRAFT_LINE = re.compile(r"^draft:\s*(.+)$", re.MULTILINE)


def _cases(category: RewriteCategory) -> list[RewriteCase]:
    return [case for case in load_rewrite_cases(_DATA) if case.category is category]


@pytest.mark.unit
def test_no_dataset_draft_equals_a_draft_example_of_the_prompts() -> None:
    examples = {
        normalize_lexicon_text(match)
        for path in _PROMPTS.glob("*.md")
        for match in _DRAFT_LINE.findall(path.read_text(encoding="utf-8"))
    }
    assert examples
    drafts = {normalize_lexicon_text(case.draft) for case in load_rewrite_cases(_DATA)}
    assert not drafts & examples


@pytest.mark.unit
def test_dataset_ids_are_unique_and_drafts_are_not_blank() -> None:
    cases = load_rewrite_cases(_DATA)
    assert len({case.id for case in cases}) == len(cases)
    assert all(case.draft.strip() for case in cases)


@pytest.mark.unit
def test_profanity_has_at_least_fifteen_cases_and_two_with_a_goal() -> None:
    cases = _cases(RewriteCategory.PROFANITY)
    assert len(cases) >= 15
    assert sum(case.goal is not None for case in cases) >= 2


@pytest.mark.unit
def test_every_goal_has_five_cases_with_the_required_forbidden_stems() -> None:
    cases = _cases(RewriteCategory.GOAL)
    per_goal = Counter(case.goal for case in cases)
    assert set(per_goal) == set(RewriteGoal)
    assert all(count >= 5 for count in per_goal.values())
    for case in cases:
        stems = set(case.forbidden_stems)
        assert set(_REQUIRED_STEMS) <= stems, case.id
        assert _RETIRED_STEM not in stems, case.id
        assert (_PROHIBITION_STEM in stems) is (case.goal is not RewriteGoal.SET_BOUNDARY), case.id


@pytest.mark.unit
def test_rules_come_in_pairs_with_and_without_the_rule_on_the_same_draft() -> None:
    cases = _cases(RewriteCategory.RULE)
    pairs: dict[str, dict[str, RewriteCase]] = defaultdict(dict)
    for case in cases:
        stem, _, side = case.id.rpartition("-")
        pairs[stem][side] = case
    assert len(pairs) >= 4
    for stem, sides in pairs.items():
        assert set(sides) == {"with", "without"}, stem
        assert sides["with"].rules
        assert not sides["without"].rules
        assert sides["with"].draft == sides["without"].draft
        assert sides["with"].goal == sides["without"].goal
    ask_pairs = [
        s for s in pairs.values() if s["with"].rules[0].category is RuleCategory.HOW_TO_ASK
    ]
    assert len(ask_pairs) >= 2
    assert all(s["with"].effect_pair == s["without"].effect_pair is not None for s in ask_pairs)
    categories = {s["with"].rules[0].category for s in pairs.values()}
    assert {RuleCategory.APOLOGY, RuleCategory.TABOO_TOPIC} <= categories
    apology = next(s for s in pairs.values() if s["with"].rules[0].category is RuleCategory.APOLOGY)
    assert apology["with"].goal is RewriteGoal.ADMIT_FAULT


@pytest.mark.unit
def test_ladder_has_ten_cases_including_a_long_draft_and_one_in_vy() -> None:
    cases = _cases(RewriteCategory.LADDER)
    assert len(cases) >= 10
    assert any(len(case.draft.split()) >= 40 for case in cases)
    assert any(re.search(r"\b(вы|вам|вас|ваш\w*)\b", case.draft.casefold()) for case in cases)


@pytest.mark.unit
def test_smoke_is_twelve_cases_spanning_every_category() -> None:
    smoke = [case for case in load_rewrite_cases(_DATA) if case.smoke]
    assert len(smoke) == 12
    assert {case.category for case in smoke} == set(RewriteCategory)
