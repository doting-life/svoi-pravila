"""Derive the judge's ``clean`` verdicts from its observations (ADR-0016 §13).

The judge only quotes; this module is the single place where an observation becomes a verdict.

Grounding. A quote counts only when its normalised form occurs in the normalised text of the
variant it was taken from (``normalise``: lower case, the letter yo folded to ye, every non-word
character a space, spaces collapsed; an empty normalised quote never counts). The verdict of a
variant is ``clean = yes`` iff no rude quote of that variant counts. ``ungrounded_counts`` reports,
per slot, the quotes that do not occur in their variant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from svoi_pravila.application.ports.rewrite_judge import (
    JudgeObservation,
    JudgeRequest,
    JudgeResult,
    Verdict,
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


def _clean(quotes: tuple[str, ...], text: str) -> Verdict:
    return Verdict.NO if _counted(quotes, text) else Verdict.YES


def derive_result(request: JudgeRequest, observation: JudgeObservation) -> JudgeResult:
    """The ``clean`` verdicts of one judged request."""
    return JudgeResult(
        softer_clean=_clean(observation.softer_rude, request.softer),
        firmer_clean=_clean(observation.firmer_rude, request.firmer),
        meta=observation.meta,
    )


@dataclass(frozen=True, slots=True)
class UngroundedCounts:
    """Quotes of one judged request that do not occur in their own variant, per slot."""

    softer: int
    firmer: int

    @property
    def total(self) -> int:
        return self.softer + self.firmer


def ungrounded_counts(request: JudgeRequest, observation: JudgeObservation) -> UngroundedCounts:
    """How many quotes of each slot are not in their variant."""
    return UngroundedCounts(
        softer=sum(not _grounded(quote, request.softer) for quote in observation.softer_rude),
        firmer=sum(not _grounded(quote, request.firmer) for quote in observation.firmer_rude),
    )


def _quotes(items: tuple[str, ...], text: str) -> str:
    """The quotes for the local report; ``?`` marks a quote that is not in its text."""
    if not items:
        return "-"
    return "; ".join(item if _grounded(item, text) else f"?{item}" for item in items)


def evidence(request: JudgeRequest, observation: JudgeObservation, slot: str) -> str:
    """The judge's rude quotes of one slot, for the local report only."""
    if slot == "softer":
        return _quotes(observation.softer_rude, request.softer)
    return _quotes(observation.firmer_rude, request.firmer)
