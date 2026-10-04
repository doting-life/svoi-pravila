"""Grant a consent bound to a shown catalog version."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from svoi_pravila.application.errors import NotFound
from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.consent_catalog import ConsentCatalog
from svoi_pravila.application.ports.id_generator import IdGenerator
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.domain.consent import Consent
from svoi_pravila.domain.enums import ConsentKind
from svoi_pravila.domain.ids import ConsentId, UserId


class GrantConsentOutcome(StrEnum):
    """Whether a grant was recorded, reused, or rejected as stale."""

    GRANTED = "granted"
    IDEMPOTENT = "idempotent"
    STALE_VERSION = "stale_version"


@dataclass(frozen=True, slots=True)
class GrantConsentCommand:
    """Input for GrantConsent."""

    user_id: UserId
    kind: ConsentKind
    text_version: str


@dataclass(frozen=True, slots=True)
class GrantConsentResult:
    """Result of GrantConsent."""

    outcome: GrantConsentOutcome
    consent: Consent | None


class GrantConsent:
    """Record consent for a bound catalog version; idempotent when valid."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        catalog: ConsentCatalog,
        ids: IdGenerator,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._catalog = catalog
        self._ids = ids
        self._clock = clock

    async def execute(self, command: GrantConsentCommand) -> GrantConsentResult:
        """Grant, return an already-valid consent, or report a stale version."""
        text = self._catalog.current_requirement().for_kind(command.kind)
        if command.text_version != text.version:
            return GrantConsentResult(outcome=GrantConsentOutcome.STALE_VERSION, consent=None)

        async with self._uow_factory() as uow:
            user = await uow.users.get(command.user_id)
            if user is None:
                raise NotFound()
            existing = await uow.consents.list_for_user(command.user_id)
            for consent in existing:
                if consent.kind is command.kind and consent.is_valid_for(
                    text.version, text.sha256.value
                ):
                    return GrantConsentResult(
                        outcome=GrantConsentOutcome.IDEMPOTENT,
                        consent=consent,
                    )
            consent = Consent(
                id=ConsentId(self._ids.new_id()),
                user_id=command.user_id,
                kind=command.kind,
                text_version=text.version,
                text_sha256=text.sha256,
                granted_at=self._clock.now(),
                revoked_at=None,
            )
            await uow.consents.add(consent)
            await uow.commit()
            return GrantConsentResult(outcome=GrantConsentOutcome.GRANTED, consent=consent)
