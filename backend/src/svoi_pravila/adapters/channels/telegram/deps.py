"""Dependencies injected into the Telegram dispatcher workflow data."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.adapters.channels.telegram.localization import TelegramStrings
from svoi_pravila.application.ports.monotonic import MonotonicClock
from svoi_pravila.application.ports.pseudonymizer import Pseudonymizer
from svoi_pravila.application.ports.rate_limiter import RateLimiter
from svoi_pravila.application.ports.update_deduplicator import UpdateDeduplicator
from svoi_pravila.application.use_cases.accept_age_confirmation import AcceptAgeConfirmation
from svoi_pravila.application.use_cases.decode_incoming import IncomingDecoder
from svoi_pravila.application.use_cases.get_consent_document import GetConsentDocument
from svoi_pravila.application.use_cases.get_onboarding_step import GetOnboardingStep
from svoi_pravila.application.use_cases.get_user_by_telegram_id import GetUserByTelegramId
from svoi_pravila.application.use_cases.grant_consent import GrantConsent


@dataclass(frozen=True, slots=True)
class TelegramDeps:
    """Application collaborators available to Telegram middlewares and handlers."""

    strings: TelegramStrings
    get_onboarding_step: GetOnboardingStep
    get_user_by_telegram_id: GetUserByTelegramId
    accept_age: AcceptAgeConfirmation
    grant_consent: GrantConsent
    get_consent_document: GetConsentDocument
    decode_incoming: IncomingDecoder
    deduplicator: UpdateDeduplicator
    rate_limiter: RateLimiter
    pseudonymizer: Pseudonymizer
    monotonic: MonotonicClock
    draft_min_interval_ms: int
