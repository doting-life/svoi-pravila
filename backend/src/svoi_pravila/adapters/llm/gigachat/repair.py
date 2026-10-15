"""Corrective retry: the second request that repairs the first attempt's defect.

The correction texts live in the versioned fragment ``prompts/repair/v2.md`` (one
``reason: text`` line each, ``{slot}`` and ``{words}`` filled here). Reasons without their own
line use ``generic``. The first answer is echoed back (as an ``assistant`` message) only when it
parsed as JSON and the defect is not one of ``NO_ECHO``.

Lexicon findings (obscene words, generalizations) are corrected together: one user message with
one line per finding. The matched words are conversation text: they stay in memory, out of
``repr``, and go only into that message.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from svoi_pravila.adapters.llm.gigachat.prepared import PreparedMessages
from svoi_pravila.adapters.llm.registry import load_prompt_template
from svoi_pravila.application.errors import InvalidOutputReason
from svoi_pravila.domain.enums import VariantKind
from svoi_pravila.domain.lexicon_guard import (
    GUARDED_SLOTS,
    MAX_FINDING_WORD_CHARS,
    MAX_FINDING_WORDS,
    FindingKind,
    SlotFinding,
)

GENERIC_REASON = "generic"
REPAIR_VERSION = "v2"
_SLOT_PLACEHOLDER = "{slot}"
_WORDS_PLACEHOLDER = "{words}"
_WORDS_SEPARATOR = ", "
_LINE_SEPARATOR = "\n"

# The first answer is useless or untrusted for these defects: send only the correction.
NO_ECHO: frozenset[InvalidOutputReason] = frozenset(
    {
        InvalidOutputReason.PROMPT_LEAK,
        InvalidOutputReason.JSON_DECODE,
        InvalidOutputReason.SCHEMA_VIOLATION,
        InvalidOutputReason.EMPTY_MESSAGE,
        InvalidOutputReason.LENGTH,
    }
)


@dataclass(frozen=True, slots=True)
class FirstAttemptDefect:
    """The first attempt's defect, carried to the second attempt.

    ``answer`` is the first answer's JSON to echo back, or ``None`` when it is not echoed.
    ``findings`` are the lexicon findings that the correction names; they are empty for formal
    defects. Both are conversation text, so they are kept out of ``repr`` (and thus out of
    exceptions and logs).
    """

    reason: InvalidOutputReason
    slot: VariantKind | None
    answer: str | None = field(repr=False)
    findings: tuple[SlotFinding, ...] = field(default=(), repr=False)

    @property
    def labels(self) -> tuple[str, ...]:
        """C0 labels ``reason`` or ``reason:slot``, one per finding; never text."""
        if self.findings:
            return tuple(finding.label for finding in self.findings)
        return (
            self.reason.value if self.slot is None else f"{self.reason.value}:{self.slot.value}",
        )


def first_attempt_defect(
    reason: InvalidOutputReason,
    slot: VariantKind | None,
    answer: str | None,
    findings: tuple[SlotFinding, ...] = (),
) -> FirstAttemptDefect:
    """Build the defect signal; ``answer`` (parsed JSON text, if any) is dropped for ``NO_ECHO``."""
    echoed = None if reason in NO_ECHO else answer
    return FirstAttemptDefect(reason=reason, slot=slot, answer=echoed, findings=findings)


def _correction_lines() -> dict[str, str]:
    template, _ = load_prompt_template("repair", REPAIR_VERSION)
    return dict(line.split(": ", 1) for line in template.splitlines() if line)


def _fill(body: str, slot: str, words: str) -> str:
    """Fill the slot first and the words last, so that words are never re-interpreted."""
    return body.replace(_SLOT_PLACEHOLDER, slot).replace(_WORDS_PLACEHOLDER, words)


def correction_text(reason: InvalidOutputReason, slot: VariantKind | None) -> str:
    """The correction for ``reason``; ``generic`` when it has no line or the line needs more.

    A line that needs a slot (without one) or words (formal defects have none) is replaced by
    ``generic``; lines with words are built by ``findings_correction``.
    """
    lines = _correction_lines()
    body = lines.get(reason.value)
    if body is None or _WORDS_PLACEHOLDER in body or (_SLOT_PLACEHOLDER in body and slot is None):
        return lines[GENERIC_REASON]
    return body.replace(_SLOT_PLACEHOLDER, "" if slot is None else slot.value)


def findings_correction(findings: tuple[SlotFinding, ...]) -> str:
    """One correction message with one line per finding: reason, slot and words."""
    lines = _correction_lines()
    return _LINE_SEPARATOR.join(
        _fill(lines[finding.kind.value], finding.slot.value, _WORDS_SEPARATOR.join(finding.words))
        for finding in findings
    )


def longest_correction_chars() -> int:
    """Length of the longest possible correction message (worst case for the token estimate).

    It is either the longest single formal line, or one line for every kind and guarded slot with
    the most and the longest words allowed.
    """
    lines = _correction_lines()
    widest_slot = max(VariantKind, key=lambda kind: len(kind.value)).value
    widest_words = _WORDS_SEPARATOR.join(("x" * MAX_FINDING_WORD_CHARS,) * MAX_FINDING_WORDS)
    single = max(len(_fill(body, widest_slot, widest_words)) for body in lines.values())
    finding_lines = [
        len(_fill(lines[kind.value], widest_slot, widest_words)) for kind in FindingKind
    ]
    count = len(GUARDED_SLOTS)
    findings = count * sum(finding_lines) + len(_LINE_SEPARATOR) * (count * len(finding_lines) - 1)
    return max(single, findings)


def retry_messages(prepared: PreparedMessages, defect: FirstAttemptDefect) -> list[dict[str, str]]:
    """Messages of the second request: system, user, [assistant first JSON], user correction."""
    messages = [
        {"role": "system", "content": prepared.system},
        {"role": "user", "content": prepared.user},
    ]
    if defect.answer is not None:
        messages.append({"role": "assistant", "content": defect.answer})
    correction = (
        findings_correction(defect.findings)
        if defect.findings
        else correction_text(defect.reason, defect.slot)
    )
    messages.append({"role": "user", "content": correction})
    return messages
