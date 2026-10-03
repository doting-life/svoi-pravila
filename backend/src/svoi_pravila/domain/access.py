"""Access policy: age confirmation and versioned consents."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from svoi_pravila.domain.consent import Consent
from svoi_pravila.domain.enums import ConsentKind
from svoi_pravila.domain.errors import InvalidValueError
from svoi_pravila.domain.text import Sha256Hex
from svoi_pravila.domain.user import User


@dataclass(frozen=True, slots=True)
class ConsentText:
    """Versioned consent text identity (version + content hash)."""

    version: str
    sha256: Sha256Hex

    def __post_init__(self) -> None:
        if not self.version:
            msg = "ConsentText.version must be non-empty"
            raise InvalidValueError(msg)


@dataclass(frozen=True, slots=True)
class AccessRequirement:
    """Current required consent texts for every ConsentKind."""

    personal_data: ConsentText
    special_category: ConsentText

    def for_kind(self, kind: ConsentKind) -> ConsentText:
        """Return the required text for ``kind``."""
        if kind is ConsentKind.PERSONAL_DATA:
            return self.personal_data
        return self.special_category

    def texts_by_kind(self) -> dict[ConsentKind, ConsentText]:
        """Return a mapping of every ConsentKind to its required text."""
        return {
            ConsentKind.PERSONAL_DATA: self.personal_data,
            ConsentKind.SPECIAL_CATEGORY: self.special_category,
        }

    @classmethod
    def from_kinds(cls, texts: dict[ConsentKind, ConsentText]) -> AccessRequirement:
        """Build a requirement that must contain exactly every ConsentKind."""
        missing = set(ConsentKind) - set(texts)
        extra = set(texts) - set(ConsentKind)
        if missing or extra:
            msg = "AccessRequirement must contain exactly every ConsentKind"
            raise InvalidValueError(msg)
        return cls(
            personal_data=texts[ConsentKind.PERSONAL_DATA],
            special_category=texts[ConsentKind.SPECIAL_CATEGORY],
        )


@dataclass(frozen=True, slots=True)
class AccessStatus:
    """Result of evaluating whether a user may use protected scenarios."""

    age_confirmed: bool
    missing_consents: frozenset[ConsentKind]
    granted: bool


def evaluate_access(
    user: User,
    consents: Sequence[Consent],
    requirement: AccessRequirement,
) -> AccessStatus:
    """Evaluate age confirmation and whether all required consents are current."""
    age_confirmed = user.age_confirmed_at is not None
    missing: set[ConsentKind] = set()
    for kind, text in requirement.texts_by_kind().items():
        matching = next(
            (
                c
                for c in consents
                if c.kind is kind and c.is_valid_for(text.version, text.sha256.value)
            ),
            None,
        )
        if matching is None:
            missing.add(kind)
    missing_frozen = frozenset(missing)
    granted = age_confirmed and not missing_frozen
    return AccessStatus(
        age_confirmed=age_confirmed,
        missing_consents=missing_frozen,
        granted=granted,
    )
