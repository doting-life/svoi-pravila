"""Versioned consent document value object (identity + body text)."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.domain.enums import ConsentKind
from svoi_pravila.domain.errors import InvalidValueError
from svoi_pravila.domain.text import Sha256Hex


@dataclass(frozen=True, slots=True)
class ConsentDocument:
    """Current consent document available to channels via use cases."""

    kind: ConsentKind
    version: str
    sha256: Sha256Hex
    text: str

    def __post_init__(self) -> None:
        if not self.version:
            msg = "ConsentDocument.version must be non-empty"
            raise InvalidValueError(msg)
        if not self.text:
            msg = "ConsentDocument.text must be non-empty"
            raise InvalidValueError(msg)
