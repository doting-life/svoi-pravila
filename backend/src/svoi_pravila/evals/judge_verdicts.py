"""Derive the judge's verdicts from its observations (ADR-0016 §13, «Судья v2»).

The judge only observes; this module is the single place where an observation becomes a verdict.
The table:

- ``clean``: ``yes`` iff ``rude`` is empty.
- ``invented``: ``yes`` iff ``added`` is not empty.
- ``meaning_kept``: ``yes`` iff ``position`` is ``same``.
- ``goal_done``: ``na`` iff the request has no goal; else ``yes`` iff ``goal_sentence`` is not
  blank and ``goal_breach`` is not a breach of that goal (``GOAL_BREACHES``).
- ``rule_done``: ``na`` iff the request has no rules; else ``followed`` is ``yes``, ``broken`` is
  ``no`` and ``not_applicable`` is ``na``.
- ``ladder_ok``: ``yes`` iff ``firmer_softening`` and ``firmer_pressure`` are empty, ``warmer`` is
  ``softer`` and the word multisets of the two variants differ (lower case, the letter yo folded to
  ye, words are ``\\w+``).
"""

from __future__ import annotations

import re
from collections import Counter

from svoi_pravila.application.ports.rewrite_judge import (
    GoalBreach,
    JudgeObservation,
    JudgeRequest,
    JudgeResult,
    Position,
    RuleCheck,
    SlotObservation,
    SlotVerdicts,
    Verdict,
    Warmer,
)
from svoi_pravila.domain.enums import RewriteGoal

GOAL_BREACHES: dict[RewriteGoal, frozenset[GoalBreach]] = {
    RewriteGoal.DECLINE: frozenset({GoalBreach.HEDGED, GoalBreach.CONDITIONAL}),
    RewriteGoal.SET_BOUNDARY: frozenset({GoalBreach.WISH_OR_QUESTION}),
    RewriteGoal.ADMIT_FAULT: frozenset({GoalBreach.EXCUSE, GoalBreach.SELF_BLAME}),
    RewriteGoal.RECONNECT: frozenset(
        {GoalBreach.REPROACH, GoalBreach.PROHIBITION, GoalBreach.ULTIMATUM}
    ),
}

_WORD = re.compile(r"\w+")


def _yes(*, ok: bool) -> Verdict:
    return Verdict.YES if ok else Verdict.NO


def _goal_done(request: JudgeRequest, slot: SlotObservation) -> Verdict:
    if request.goal is None:
        return Verdict.NA
    return _yes(
        ok=bool(slot.goal_sentence.strip()) and slot.goal_breach not in GOAL_BREACHES[request.goal]
    )


def _rule_done(request: JudgeRequest, slot: SlotObservation) -> Verdict:
    if not request.rules:
        return Verdict.NA
    return {
        RuleCheck.FOLLOWED: Verdict.YES,
        RuleCheck.BROKEN: Verdict.NO,
        RuleCheck.NOT_APPLICABLE: Verdict.NA,
    }[slot.rule_check]


def slot_verdicts(request: JudgeRequest, slot: SlotObservation) -> SlotVerdicts:
    """The verdicts of one variant."""
    return SlotVerdicts(
        clean=_yes(ok=not slot.rude),
        meaning_kept=_yes(ok=slot.position is Position.SAME),
        invented=Verdict.YES if slot.added else Verdict.NO,
        goal_done=_goal_done(request, slot),
        rule_done=_rule_done(request, slot),
    )


def _words(text: str) -> Counter[str]:
    return Counter(_WORD.findall(text.lower().replace("\u0451", "\u0435")))


def words_differ(softer: str, firmer: str) -> bool:
    """True when the two texts are not the same words in the same numbers."""
    return _words(softer) != _words(firmer)


def ladder_ok(request: JudgeRequest, observation: JudgeObservation) -> Verdict:
    """``yes`` when ``firmer`` is plainer, ``softer`` is warmer and the words differ."""
    pair = observation.pair
    return _yes(
        ok=not pair.firmer_softening
        and not pair.firmer_pressure
        and pair.warmer is Warmer.SOFTER
        and words_differ(request.softer, request.firmer)
    )


def derive_result(request: JudgeRequest, observation: JudgeObservation) -> JudgeResult:
    """The verdicts of one judged request; ``na`` of goal and rules comes from the request."""
    return JudgeResult(
        softer=slot_verdicts(request, observation.softer),
        firmer=slot_verdicts(request, observation.firmer),
        ladder_ok=ladder_ok(request, observation),
        meta=observation.meta,
    )


def _quotes(items: tuple[str, ...]) -> str:
    return "; ".join(items) if items else "-"


def evidence(observation: JudgeObservation, slot: str | None, criterion: str) -> str:
    """The judge's evidence for one criterion, for the local report only."""
    pair = observation.pair
    if slot is None:
        return (
            f"softening: {_quotes(pair.firmer_softening)}; "
            f"pressure: {_quotes(pair.firmer_pressure)}; warmer: {pair.warmer.value}"
        )
    seen: SlotObservation = getattr(observation, slot)
    return {
        "clean": _quotes(seen.rude),
        "invented": _quotes(seen.added),
        "meaning_kept": seen.position.value,
        "goal_done": f"{seen.goal_sentence or '-'} [{seen.goal_breach.value}]",
        "rule_done": seen.rule_check.value,
    }[criterion]
