"""Derive the next onboarding step from persisted age and consents."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from svoi_pravila.application.ports.consent_catalog import ConsentCatalog
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.domain.enums import ConsentKind
from svoi_pravila.domain.ids import TelegramUserId


class OnboardingStepKind(StrEnum):
    """High-level onboarding step."""

    AGE = "age"
    CONSENT = "consent"
    DONE = "done"


@dataclass(frozen=True, slots=True)
class OnboardingStep:
    """Next onboarding step for a Telegram user."""

    kind: OnboardingStepKind
    consent_kind: ConsentKind | None = None
    consent_version: str | None = None


@dataclass(frozen=True, slots=True)
class GetOnboardingStepQuery:
    """Input for GetOnboardingStep."""

    telegram_user_id: TelegramUserId


@dataclass(frozen=True, slots=True)
class GetOnboardingStepResult:
    """Result of GetOnboardingStep."""

    step: OnboardingStep
    consents_revoked: bool = False


class GetOnboardingStep:
    """Return the next onboarding step without creating records."""

    def __init__(self, uow_factory: UnitOfWorkFactory, catalog: ConsentCatalog) -> None:
        self._uow_factory = uow_factory
        self._catalog = catalog

    async def execute(self, query: GetOnboardingStepQuery) -> GetOnboardingStepResult:
        """Compute AGE → CONSENT(personal_data) → CONSENT(special_category) → DONE."""
        requirement = self._catalog.current_requirement()
        async with self._uow_factory() as uow:
            user = await uow.users.get_by_telegram_id(query.telegram_user_id)
            if user is None:
                return GetOnboardingStepResult(
                    step=OnboardingStep(kind=OnboardingStepKind.AGE),
                    consents_revoked=False,
                )
            consents = await uow.consents.list_for_user(user.id)
            consents_revoked = any(consent.revoked_at is not None for consent in consents)
            if user.age_confirmed_at is None:
                return GetOnboardingStepResult(
                    step=OnboardingStep(kind=OnboardingStepKind.AGE),
                    consents_revoked=consents_revoked,
                )

        for kind in (ConsentKind.PERSONAL_DATA, ConsentKind.SPECIAL_CATEGORY):
            text = requirement.for_kind(kind)
            valid = any(
                consent.kind is kind and consent.is_valid_for(text.version, text.sha256.value)
                for consent in consents
            )
            if not valid:
                return GetOnboardingStepResult(
                    step=OnboardingStep(
                        kind=OnboardingStepKind.CONSENT,
                        consent_kind=kind,
                        consent_version=text.version,
                    ),
                    consents_revoked=consents_revoked,
                )
        return GetOnboardingStepResult(
            step=OnboardingStep(kind=OnboardingStepKind.DONE),
            consents_revoked=consents_revoked,
        )
