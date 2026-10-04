"""Per-request random boundary markers for untrusted payloads."""

from __future__ import annotations

import secrets
from collections.abc import Callable, Sequence

from svoi_pravila.application.errors import InvalidGenerationOutput, InvalidOutputReason
from svoi_pravila.application.ports.generation import TokenUsage

# 22 url-safe bytes ≈ 176 bits of entropy (≥ 128-bit requirement).
_MARKER_BYTES = 22
_MAX_ALLOCATE_ATTEMPTS = 16
BOUNDARY_PREFIX = "SPBOUND_"


def new_boundary_marker() -> str:
    """Return a high-entropy boundary marker."""
    return f"{BOUNDARY_PREFIX}{secrets.token_urlsafe(_MARKER_BYTES)}"


def allocate_token(untrusted: Sequence[str], *, factory: Callable[[], str]) -> str:
    """Allocate a marker that does not appear in untrusted texts."""
    texts = tuple(untrusted)
    for _ in range(_MAX_ALLOCATE_ATTEMPTS):
        token = factory()
        if all(token not in text for text in texts):
            return token
    raise InvalidGenerationOutput(
        (InvalidOutputReason.BOUNDARY_COLLISION,),
        usage=TokenUsage(),
        attempts=0,
    )


def allocate_boundary_marker(untrusted: Sequence[str]) -> str:
    """Allocate a boundary marker absent from all untrusted strings."""
    return allocate_token(untrusted, factory=new_boundary_marker)


def wrap_untrusted(marker: str, label: str, text: str) -> str:
    """Wrap a single labeled untrusted section between matching boundary markers."""
    return f"{marker} {label}\n{text}\n{marker}"


def wrap_untrusted_payload(marker: str, sections: Sequence[tuple[str, str]]) -> str:
    """Wrap multiple labeled untrusted sections in one boundary pair.

    GigaChat structured generation fails when the exact marker token is repeated
    too often across the request; a single envelope keeps the count low while
    still delimiting all untrusted fields.
    """
    lines: list[str] = [f"{marker} payload"]
    for label, text in sections:
        lines.append(f"{label}:")
        lines.append(text)
    lines.append(marker)
    return "\n".join(lines)
