"""Text value objects."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from svoi_pravila.domain.errors import (
    InvalidValueError,
    RuleTextEmptyError,
    RuleTextInvalidCharsError,
    RuleTextTooLongError,
)

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_CRLF_RE = re.compile(r"\r\n|\r")
_MULTI_BLANK_LINE_RE = re.compile(r"\n{3,}")

RULE_TEXT_MAX_CHARS = 500


@dataclass(frozen=True, slots=True)
class Sha256Hex:
    """64 lowercase hexadecimal characters (SHA-256 digest)."""

    value: str

    def __post_init__(self) -> None:
        if _SHA256_HEX_RE.fullmatch(self.value) is None:
            msg = "Sha256Hex must be 64 lowercase hex characters"
            raise InvalidValueError(msg)


def _normalize_text(raw: str, *, min_len: int, max_len: int, name: str) -> str:
    normalized = unicodedata.normalize("NFC", raw).strip()
    if not normalized:
        msg = f"{name} must not be empty"
        raise InvalidValueError(msg)
    for char in normalized:
        if unicodedata.category(char) == "Cc":
            msg = f"{name} must not contain control characters"
            raise InvalidValueError(msg)
    length = len(normalized)
    if length < min_len or length > max_len:
        msg = f"{name} length must be between {min_len} and {max_len}"
        raise InvalidValueError(msg)
    return normalized


def _normalize_rule_text(raw: str) -> str:
    normalized = unicodedata.normalize("NFC", raw)
    normalized = _CRLF_RE.sub("\n", normalized)
    lines = [line.rstrip() for line in normalized.split("\n")]
    normalized = "\n".join(lines)
    normalized = _MULTI_BLANK_LINE_RE.sub("\n\n", normalized)
    normalized = normalized.strip()
    for char in normalized:
        if char != "\n" and unicodedata.category(char) == "Cc":
            msg = "RuleText must not contain control characters"
            raise RuleTextInvalidCharsError(msg)
    if not normalized:
        msg = "RuleText must not be empty"
        raise RuleTextEmptyError(msg)
    length = len(normalized)
    if length > RULE_TEXT_MAX_CHARS:
        raise RuleTextTooLongError(maximum=RULE_TEXT_MAX_CHARS, actual=length)
    return normalized


@dataclass(frozen=True, slots=True)
class RuleText:
    """NFC-normalized rule text, 1-500 characters, newlines allowed."""

    value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _normalize_rule_text(self.value))


@dataclass(frozen=True, slots=True)
class ContactLabel:
    """NFC-normalized contact label, 1-32 characters, no control characters."""

    value: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "value",
            _normalize_text(self.value, min_len=1, max_len=32, name="ContactLabel"),
        )
