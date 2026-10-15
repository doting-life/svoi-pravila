"""Corrective retry: the second request that repairs the first attempt's defect.

The correction texts live in the versioned fragment ``prompts/repair/v4.md`` (one
``key: text`` line each, ``{slot}`` and ``{words}`` filled here). The keys are the invalid-output
reasons, plus the correction keys ``keep``, ``generic`` and ``empty_answer``, which are not
reasons. The first answer is echoed back (as an ``assistant`` message) only when it parsed as JSON
and the defect is not one of ``NO_ECHO`` and the answer is not an all-empty one.

The correction is one user message naming every defect of the answer: one line per formal slot
defect, or one line per lexicon finding (obscene words, generalizations), then the ``keep`` line
once. The two kinds never share a message. When a defect has no line of its own, the message is
``generic`` alone, without ``keep``. The matched words are conversation text: they stay in
memory, out of ``repr``, and go only into that message.

An all-empty answer (every slot empty with ``safety=ok``) is not echoed and gets the single
``empty_answer`` line alone: no per-slot lines, no ``keep``. Its defects and labels stay the
ordinary ``empty_text:softer`` and ``empty_text:firmer``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from svoi_pravila.adapters.llm.gigachat.prepared import PreparedMessages
from svoi_pravila.adapters.llm.gigachat.validation import REWRITE_SLOTS, SlotDefect
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
REPAIR_VERSION = "v4"
KEEP_REASON = "keep"
EMPTY_ANSWER_KEY = "empty_answer"
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

    ``reason`` and ``slot`` are the first defect; ``defects`` are all the formal defects of the
    answer (one entry, the first defect, when it has no other). ``answer`` is the first answer's
    JSON to echo back, or ``None`` when it is not echoed. ``findings`` are the lexicon findings
    that the correction names; they are empty for formal defects. ``answer`` and ``findings`` are
    conversation text, so they are kept out of ``repr`` (and thus out of exceptions and logs).
    ``all_empty`` marks an all-empty answer: it is not echoed and gets the ``empty_answer`` line.
    """

    reason: InvalidOutputReason
    slot: VariantKind | None
    answer: str | None = field(repr=False)
    defects: tuple[SlotDefect, ...] = ()
    findings: tuple[SlotFinding, ...] = field(default=(), repr=False)
    all_empty: bool = False

    @property
    def labels(self) -> tuple[str, ...]:
        """C0 labels ``reason`` or ``reason:slot``, one per finding or defect; never text."""
        if self.findings:
            return tuple(finding.label for finding in self.findings)
        return tuple(defect.label for defect in self.defects)


def first_attempt_defect(
    reason: InvalidOutputReason,
    slot: VariantKind | None,
    answer: str | None,
    findings: tuple[SlotFinding, ...] = (),
    defects: tuple[SlotDefect, ...] = (),
) -> FirstAttemptDefect:
    """Build the defect signal; ``answer`` (parsed JSON text, if any) is dropped for ``NO_ECHO``."""
    echoed = None if reason in NO_ECHO else answer
    named = () if findings else defects or (SlotDefect(reason, slot),)
    return FirstAttemptDefect(
        reason=reason, slot=slot, answer=echoed, defects=named, findings=findings
    )


def all_empty_defect(defects: tuple[SlotDefect, ...]) -> FirstAttemptDefect:
    """The defect signal of an all-empty answer: nothing is echoed, ``empty_answer`` is sent.

    ``defects`` are the answer's ordinary slot defects (never empty); they give the labels.
    """
    first = defects[0]
    return FirstAttemptDefect(
        reason=first.reason, slot=first.slot, answer=None, defects=defects, all_empty=True
    )


def _correction_lines() -> dict[str, str]:
    template, _ = load_prompt_template("repair", REPAIR_VERSION)
    return dict(line.split(": ", 1) for line in template.splitlines() if line)


def _fill(body: str, slot: str, words: str) -> str:
    """Fill the slot first and the words last, so that words are never re-interpreted."""
    return body.replace(_SLOT_PLACEHOLDER, slot).replace(_WORDS_PLACEHOLDER, words)


def _defect_line(lines: dict[str, str], defect: SlotDefect) -> str | None:
    """The line of a formal defect, or ``None`` when it has none or needs a slot it lacks."""
    body = lines.get(defect.reason.value)
    if body is None or _WORDS_PLACEHOLDER in body:
        return None
    if _SLOT_PLACEHOLDER in body and defect.slot is None:
        return None
    return body.replace(_SLOT_PLACEHOLDER, "" if defect.slot is None else defect.slot.value)


def _with_keep(lines: dict[str, str], body: list[str]) -> str:
    return _LINE_SEPARATOR.join([*body, lines[KEEP_REASON]])


def formal_correction(defects: tuple[SlotDefect, ...]) -> str:
    """One message for every formal defect: one line each, then ``keep``.

    A defect without a line of its own (or without the slot its line needs) makes the whole
    message ``generic``, which stands alone, without ``keep``.
    """
    lines = _correction_lines()
    texts = [_defect_line(lines, defect) for defect in defects]
    found = [text for text in texts if text is not None]
    if not found or len(found) != len(texts):
        return lines[GENERIC_REASON]
    return _with_keep(lines, found)


def findings_correction(findings: tuple[SlotFinding, ...]) -> str:
    """One message with one line per finding (reason, slot and words), then ``keep``."""
    lines = _correction_lines()
    return _with_keep(
        lines,
        [
            _fill(
                lines[finding.kind.value], finding.slot.value, _WORDS_SEPARATOR.join(finding.words)
            )
            for finding in findings
        ],
    )


def longest_correction_chars() -> int:
    """Length of the longest possible correction message (worst case for the token estimate).

    It is the widest of: ``generic``; ``empty_answer``; the formal message (every formal line,
    once per rewrite slot for the lines that name a slot, then ``keep``); and the findings
    message (one line for every kind and guarded slot with the most and the longest words
    allowed, then ``keep``).
    """
    lines = _correction_lines()
    widest_slot = max(VariantKind, key=lambda kind: len(kind.value)).value
    widest_words = _WORDS_SEPARATOR.join(("x" * MAX_FINDING_WORD_CHARS,) * MAX_FINDING_WORDS)
    not_formal = {
        KEEP_REASON,
        GENERIC_REASON,
        EMPTY_ANSWER_KEY,
        *(kind.value for kind in FindingKind),
    }
    formal = [
        _fill(body, widest_slot, widest_words)
        for key, body in lines.items()
        if key not in not_formal
        for _ in range(len(REWRITE_SLOTS) if _SLOT_PLACEHOLDER in body else 1)
    ]
    findings = [
        _fill(lines[kind.value], widest_slot, widest_words)
        for _ in GUARDED_SLOTS
        for kind in FindingKind
    ]
    return max(
        len(lines[GENERIC_REASON]),
        len(lines[EMPTY_ANSWER_KEY]),
        len(_with_keep(lines, formal)),
        len(_with_keep(lines, findings)),
    )


def retry_messages(prepared: PreparedMessages, defect: FirstAttemptDefect) -> list[dict[str, str]]:
    """Messages of the second request: system, user, [assistant first JSON], user correction."""
    messages = [
        {"role": "system", "content": prepared.system},
        {"role": "user", "content": prepared.user},
    ]
    if defect.answer is not None:
        messages.append({"role": "assistant", "content": defect.answer})
    messages.append({"role": "user", "content": _correction(defect)})
    return messages


def _correction(defect: FirstAttemptDefect) -> str:
    if defect.all_empty:
        return _correction_lines()[EMPTY_ANSWER_KEY]
    if defect.findings:
        return findings_correction(defect.findings)
    return formal_correction(defect.defects)
