"""The judge port: verdict invariants of the result dataclasses."""

from __future__ import annotations

import pytest

from svoi_pravila.application.ports.generation import GenerationMeta, TokenUsage
from svoi_pravila.application.ports.rewrite_judge import JudgeResult, SlotVerdicts, Verdict

_META = GenerationMeta(
    model="judge",
    prompt_version="judge_rewrite@v3",
    latency_ms=1,
    attempts=1,
    usage=TokenUsage(1, 1, 0),
)


def _slot(**changes: Verdict) -> SlotVerdicts:
    values = {
        "clean": Verdict.YES,
        "meaning_kept": Verdict.YES,
        "invented": Verdict.NO,
        "goal_done": Verdict.NA,
        "rule_done": Verdict.NA,
    }
    return SlotVerdicts(**{**values, **changes})


@pytest.mark.unit
def test_goal_and_rule_verdicts_may_be_not_applicable() -> None:
    slot = _slot(goal_done=Verdict.NA, rule_done=Verdict.NA)
    assert slot.goal_done is Verdict.NA
    assert slot.rule_done is Verdict.NA


@pytest.mark.unit
@pytest.mark.parametrize("criterion", ["clean", "meaning_kept", "invented"])
def test_always_applicable_criteria_reject_na(criterion: str) -> None:
    with pytest.raises(ValueError, match="never na"):
        _slot(**{criterion: Verdict.NA})


@pytest.mark.unit
def test_ladder_ok_rejects_na() -> None:
    with pytest.raises(ValueError, match="ladder_ok is never na"):
        JudgeResult(softer=_slot(), firmer=_slot(), ladder_ok=Verdict.NA, meta=_META)
    result = JudgeResult(softer=_slot(), firmer=_slot(), ladder_ok=Verdict.NO, meta=_META)
    assert result.ladder_ok is Verdict.NO
