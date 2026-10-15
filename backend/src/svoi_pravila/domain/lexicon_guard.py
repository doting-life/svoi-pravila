"""Lexicon findings in the tone variants: obscene words and accusatory generalizations.

See ADR-0016 §10 and §14. The check covers the ``softer`` and ``firmer`` slots only; ``check``
keeps the author's words by design. The matched words are conversation text: they live in
``SlotFinding.words`` (never in ``repr``) and must not reach logs, metrics or exceptions.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from svoi_pravila.domain.enums import VariantKind
from svoi_pravila.domain.lexicon import Lexicon, lexicon_matches

MAX_FINDING_WORDS = 5
MAX_FINDING_WORD_CHARS = 40

GUARDED_SLOTS: tuple[VariantKind, ...] = (VariantKind.SOFTER, VariantKind.FIRMER)


class FindingKind(StrEnum):
    """Which lexicon a finding came from (C0 label)."""

    PROFANITY = "profanity"
    GENERALIZATION = "generalization"


@dataclass(frozen=True, slots=True)
class GuardLexicons:
    """The two lexicons the tone slots are checked with."""

    profanity: Lexicon
    generalization: Lexicon


@dataclass(frozen=True, slots=True)
class SlotFinding:
    """One lexicon finding in one slot; ``words`` is text and stays out of ``repr``."""

    kind: FindingKind
    slot: VariantKind
    words: tuple[str, ...] = field(repr=False)

    @property
    def label(self) -> str:
        """C0 label ``kind:slot``; never text."""
        return f"{self.kind.value}:{self.slot.value}"


def find_in_slots(
    texts: Mapping[VariantKind, str], lexicons: GuardLexicons
) -> tuple[SlotFinding, ...]:
    """Return the findings of the ``softer`` and ``firmer`` texts, slot by slot.

    Obscene words are listed before generalizations of the same slot. Each finding keeps at most
    ``MAX_FINDING_WORDS`` words of at most ``MAX_FINDING_WORD_CHARS`` characters.
    """
    findings: list[SlotFinding] = []
    for slot in GUARDED_SLOTS:
        text = texts.get(slot)
        if text is None:
            continue
        for kind, lexicon in (
            (FindingKind.PROFANITY, lexicons.profanity),
            (FindingKind.GENERALIZATION, lexicons.generalization),
        ):
            words = lexicon_matches(text, lexicon)
            if words:
                bounded = tuple(word[:MAX_FINDING_WORD_CHARS] for word in words[:MAX_FINDING_WORDS])
                findings.append(SlotFinding(kind=kind, slot=slot, words=bounded))
    return tuple(findings)
