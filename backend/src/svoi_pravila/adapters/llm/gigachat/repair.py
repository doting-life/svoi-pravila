"""Corrective retry: the second request that repairs the first attempt's defect.

The correction texts live in the versioned fragment ``prompts/repair/v1.md`` (one
``reason: text`` line each, ``{slot}`` filled here). Reasons without their own line use
``generic``. The first answer is echoed back (as an ``assistant`` message) only when it parsed
as JSON and the defect is not one of ``NO_ECHO``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from svoi_pravila.adapters.llm.gigachat.prepared import PreparedMessages
from svoi_pravila.adapters.llm.registry import load_prompt_template
from svoi_pravila.application.errors import InvalidOutputReason
from svoi_pravila.domain.enums import VariantKind

GENERIC_REASON = "generic"
_SLOT_PLACEHOLDER = "{slot}"

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
    It is conversation text, so it is kept out of ``repr`` (and thus out of exceptions and logs).
    """

    reason: InvalidOutputReason
    slot: VariantKind | None
    answer: str | None = field(repr=False)

    @property
    def label(self) -> str:
        """C0 label ``reason`` or ``reason:slot``; never text."""
        return self.reason.value if self.slot is None else f"{self.reason.value}:{self.slot.value}"


def first_attempt_defect(
    reason: InvalidOutputReason, slot: VariantKind | None, answer: str | None
) -> FirstAttemptDefect:
    """Build the defect signal; ``answer`` (parsed JSON text, if any) is dropped for ``NO_ECHO``."""
    echoed = None if reason in NO_ECHO else answer
    return FirstAttemptDefect(reason=reason, slot=slot, answer=echoed)


def _correction_lines() -> dict[str, str]:
    template, _ = load_prompt_template("repair", "v1")
    return dict(line.split(": ", 1) for line in template.splitlines() if line)


def correction_text(reason: InvalidOutputReason, slot: VariantKind | None) -> str:
    """The correction for ``reason``; ``generic`` when it has no line or the line needs a slot."""
    lines = _correction_lines()
    body = lines.get(reason.value)
    if body is None or (_SLOT_PLACEHOLDER in body and slot is None):
        return lines[GENERIC_REASON]
    return body.replace(_SLOT_PLACEHOLDER, "" if slot is None else slot.value)


def longest_correction_chars() -> int:
    """Length of the longest filled correction line (worst case for the token estimate)."""
    widest_slot = max(VariantKind, key=lambda kind: len(kind.value)).value
    return max(
        len(body.replace(_SLOT_PLACEHOLDER, widest_slot)) for body in _correction_lines().values()
    )


def retry_messages(prepared: PreparedMessages, defect: FirstAttemptDefect) -> list[dict[str, str]]:
    """Messages of the second request: system, user, [assistant first JSON], user correction."""
    messages = [
        {"role": "system", "content": prepared.system},
        {"role": "user", "content": prepared.user},
    ]
    if defect.answer is not None:
        messages.append({"role": "assistant", "content": defect.answer})
    messages.append({"role": "user", "content": correction_text(defect.reason, defect.slot)})
    return messages
