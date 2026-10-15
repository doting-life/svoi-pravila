"""Derive the judge's ``clean`` verdict from its observation (ADR-0016 §13).

The judge only quotes; this module is the single place where an observation becomes a verdict.

Grounding. A quote counts only when its normalised form occurs in the normalised judged text
(``normalise``: lower case, the letter yo folded to ye, every non-word character a space, spaces
collapsed; an empty normalised quote never counts). The verdict of a text is ``clean = yes`` iff no
rude quote counts. ``ungrounded`` reports the quotes that do not occur in the text.
"""

from __future__ import annotations

import re

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


def derive_result(request: JudgeRequest, observation: JudgeObservation) -> JudgeResult:
    """The ``clean`` verdict of one judged text."""
    counted = any(_grounded(quote, request.text) for quote in observation.rude)
    return JudgeResult(clean=Verdict.NO if counted else Verdict.YES, meta=observation.meta)


def ungrounded(request: JudgeRequest, observation: JudgeObservation) -> int:
    """How many quotes of the observation are not in the judged text."""
    return sum(not _grounded(quote, request.text) for quote in observation.rude)


def evidence(request: JudgeRequest, observation: JudgeObservation) -> str:
    """The judge's rude quotes, for the local report only; ``?`` marks an ungrounded quote."""
    if not observation.rude:
        return "-"
    return "; ".join(
        quote if _grounded(quote, request.text) else f"?{quote}" for quote in observation.rude
    )
