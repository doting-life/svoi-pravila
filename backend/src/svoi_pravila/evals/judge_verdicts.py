"""Derive the judge's verdicts from its observations (ADR-0016 §13, «Судья v3»).

The judge only observes; this module is the single place where an observation becomes a verdict.

Grounding. A quote counts only when its normalised form occurs in the normalised text it is about
(``normalise``: lower case, the letter yo folded to ye, every non-word character a space, spaces
collapsed; an empty normalised quote never counts). ``rude``, ``added``, ``goal_sentence`` and
``rule_evidence`` are checked in their own variant, ``firmer_softening`` and ``firmer_pressure``
in ``firmer``. An ``added`` quote that occurs in the draft does not count; nor does an ``added``
quote that contains, or is contained in, a counted ``rude`` quote. The verdicts read counted
quotes only; a blank or ungrounded ``goal_sentence`` means no goal sentence. ``ungrounded_counts``
reports, per field, the quotes that do not occur in their own text.

The table:

- ``clean``: ``yes`` iff no ``rude`` quote counts.
- ``invented``: ``yes`` iff an ``added`` quote counts.
- ``meaning_kept``: ``yes`` iff ``position`` is ``same``.
- ``goal_done``: ``na`` iff the request has no goal; else ``yes`` iff the goal sentence counts
  and ``goal_breach`` is not a breach of that goal (``GOAL_BREACHES``).
- ``rule_done``: ``na`` iff the request has no rules or ``rule_check`` is ``not_applicable``;
  else ``yes`` iff ``rule_check`` is ``followed`` and a ``rule_evidence`` quote counts, else ``no``.
- ``ladder_ok``: ``yes`` iff no ``firmer_softening`` and no ``firmer_pressure`` quote counts,
  ``warmer`` is ``softer`` and the word multisets of the two variants differ (``normalise``
  words).
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

UNGROUNDED_FIELDS = (
    "rude",
    "added",
    "goal_sentence",
    "rule_evidence",
    "firmer_softening",
    "firmer_pressure",
)

_NON_WORD = re.compile(r"\W+")


def normalise(text: str) -> str:
    """Lower case, yo folded to ye (U+0451 to U+0435), non-word characters to single spaces."""
    return _NON_WORD.sub(" ", text.lower().replace("\u0451", "\u0435")).strip()


def _grounded(quote: str, text: str) -> bool:
    """True when the normalised quote is not empty and occurs in the normalised text."""
    needle = normalise(quote)
    return bool(needle) and needle in normalise(text)


def _counted(quotes: tuple[str, ...], text: str) -> tuple[str, ...]:
    return tuple(quote for quote in quotes if _grounded(quote, text))


def _counted_added(slot: SlotObservation, variant: str, draft: str) -> tuple[str, ...]:
    rude = [normalise(quote) for quote in _counted(slot.rude, variant)]
    kept: list[str] = []
    for quote in _counted(slot.added, variant):
        if _grounded(quote, draft):
            continue
        text = normalise(quote)
        if any(text in other or other in text for other in rude):
            continue
        kept.append(quote)
    return tuple(kept)


def _yes(*, ok: bool) -> Verdict:
    return Verdict.YES if ok else Verdict.NO


def _goal_done(request: JudgeRequest, slot: SlotObservation, variant: str) -> Verdict:
    if request.goal is None:
        return Verdict.NA
    return _yes(
        ok=_grounded(slot.goal_sentence, variant)
        and slot.goal_breach not in GOAL_BREACHES[request.goal]
    )


def _rule_done(request: JudgeRequest, slot: SlotObservation, variant: str) -> Verdict:
    if not request.rules or slot.rule_check is RuleCheck.NOT_APPLICABLE:
        return Verdict.NA
    return _yes(
        ok=slot.rule_check is RuleCheck.FOLLOWED and bool(_counted(slot.rule_evidence, variant))
    )


def slot_verdicts(request: JudgeRequest, slot: SlotObservation, variant: str) -> SlotVerdicts:
    """The verdicts of one variant (``variant`` is its text)."""
    return SlotVerdicts(
        clean=_yes(ok=not _counted(slot.rude, variant)),
        meaning_kept=_yes(ok=slot.position is Position.SAME),
        invented=_yes(ok=bool(_counted_added(slot, variant, request.draft))),
        goal_done=_goal_done(request, slot, variant),
        rule_done=_rule_done(request, slot, variant),
    )


def _words(text: str) -> Counter[str]:
    return Counter(normalise(text).split())


def words_differ(softer: str, firmer: str) -> bool:
    """True when the two texts are not the same words in the same numbers."""
    return _words(softer) != _words(firmer)


def ladder_ok(request: JudgeRequest, observation: JudgeObservation) -> Verdict:
    """``yes`` when ``firmer`` is plainer, ``softer`` is warmer and the words differ."""
    pair = observation.pair
    return _yes(
        ok=not _counted(pair.firmer_softening, request.firmer)
        and not _counted(pair.firmer_pressure, request.firmer)
        and pair.warmer is Warmer.SOFTER
        and words_differ(request.softer, request.firmer)
    )


def derive_result(request: JudgeRequest, observation: JudgeObservation) -> JudgeResult:
    """The verdicts of one judged request; ``na`` of goal and rules comes from the request."""
    return JudgeResult(
        softer=slot_verdicts(request, observation.softer, request.softer),
        firmer=slot_verdicts(request, observation.firmer, request.firmer),
        ladder_ok=ladder_ok(request, observation),
        meta=observation.meta,
    )


def ungrounded_counts(request: JudgeRequest, observation: JudgeObservation) -> dict[str, int]:
    """Per field, how many quotes of one judged request do not occur in their own text.

    A non-blank ``goal_sentence`` that does not occur in its variant counts as one.
    """
    counts = dict.fromkeys(UNGROUNDED_FIELDS, 0)
    for slot, variant in (
        (observation.softer, request.softer),
        (observation.firmer, request.firmer),
    ):
        for name in ("rude", "added", "rule_evidence"):
            quotes: tuple[str, ...] = getattr(slot, name)
            counts[name] += sum(not _grounded(quote, variant) for quote in quotes)
        if slot.goal_sentence.strip() and not _grounded(slot.goal_sentence, variant):
            counts["goal_sentence"] += 1
    pair = observation.pair
    counts["firmer_softening"] += sum(
        not _grounded(quote, request.firmer) for quote in pair.firmer_softening
    )
    counts["firmer_pressure"] += sum(
        not _grounded(quote, request.firmer) for quote in pair.firmer_pressure
    )
    return counts


def _quotes(items: tuple[str, ...], text: str) -> str:
    """The quotes for the local report; ``?`` marks a quote that is not in its text."""
    if not items:
        return "-"
    return "; ".join(item if _grounded(item, text) else f"?{item}" for item in items)


def evidence(
    request: JudgeRequest, observation: JudgeObservation, slot: str | None, criterion: str
) -> str:
    """The judge's evidence for one criterion, for the local report only."""
    pair = observation.pair
    if slot is None:
        return (
            f"softening: {_quotes(pair.firmer_softening, request.firmer)}; "
            f"pressure: {_quotes(pair.firmer_pressure, request.firmer)}; "
            f"warmer: {pair.warmer.value}"
        )
    seen: SlotObservation = getattr(observation, slot)
    variant: str = getattr(request, slot)
    sentence = _quotes((seen.goal_sentence,) if seen.goal_sentence.strip() else (), variant)
    return {
        "clean": _quotes(seen.rude, variant),
        "invented": _quotes(seen.added, variant),
        "meaning_kept": seen.position.value,
        "goal_done": f"{sentence} [{seen.goal_breach.value}]",
        "rule_done": f"{seen.rule_check.value}: {_quotes(seen.rule_evidence, variant)}",
    }[criterion]
