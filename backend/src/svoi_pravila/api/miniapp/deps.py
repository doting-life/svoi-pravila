"""Mini-app API dependencies: auth, rate limit, actor resolution."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import Depends, Header, Response

from svoi_pravila.api.miniapp.errors import MiniappErrorCode
from svoi_pravila.api.miniapp.http import MiniappHttpError
from svoi_pravila.application.ports.init_data import (
    InitDataExpired,
    InitDataInvalid,
    InitDataVerifier,
)
from svoi_pravila.application.ports.pseudonymizer import Pseudonymizer
from svoi_pravila.application.ports.rate_limiter import RateLimiter
from svoi_pravila.application.use_cases.get_onboarding_step import (
    GetOnboardingStep,
    GetOnboardingStepQuery,
    OnboardingStepKind,
)
from svoi_pravila.application.use_cases.get_user_by_telegram_id import (
    GetUserByTelegramId,
    GetUserByTelegramIdQuery,
)
from svoi_pravila.domain.ids import TelegramUserId
from svoi_pravila.domain.user import User

logger = structlog.get_logger(__name__)
_AUTH_PREFIX = "tma "


@dataclass(frozen=True, slots=True)
class MiniappAuthContext:
    """Authenticated mini-app request context (no PII beyond C1 ids)."""

    telegram_user_id: TelegramUserId
    user: User | None


@dataclass(frozen=True, slots=True)
class MiniappDeps:
    """Composition-root bindings for the mini-app API."""

    init_data_verifier: InitDataVerifier
    rate_limiter: RateLimiter
    pseudonymizer: Pseudonymizer
    get_user_by_telegram_id: GetUserByTelegramId
    get_onboarding_step: GetOnboardingStep


@dataclass(slots=True)
class MiniappAuthenticator:
    """Per-router auth callables closed over composition-root bindings."""

    deps: MiniappDeps
    authenticate: Callable[..., Awaitable[MiniappAuthContext]] = field(init=False, repr=False)
    require_actor: Callable[..., Awaitable[User]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        deps = self.deps

        async def authenticate(
            authorization: Annotated[str | None, Header()] = None,
        ) -> MiniappAuthContext:
            if authorization is None or not authorization.startswith(_AUTH_PREFIX):
                logger.info("miniapp_auth_rejected", reason="missing_or_malformed_header")
                raise MiniappHttpError(MiniappErrorCode.UNAUTHORIZED, 401)
            raw = authorization[len(_AUTH_PREFIX) :]
            try:
                verified = deps.init_data_verifier.verify(raw)
            except InitDataExpired:
                logger.info("miniapp_auth_rejected", reason="init_data_expired")
                raise MiniappHttpError(MiniappErrorCode.INIT_DATA_EXPIRED, 401) from None
            except InitDataInvalid:
                logger.info("miniapp_auth_rejected", reason="init_data_invalid")
                raise MiniappHttpError(MiniappErrorCode.INIT_DATA_INVALID, 401) from None

            pseudonym = deps.pseudonymizer.pseudonymize(
                "miniapp",
                str(verified.telegram_user_id.value),
            )
            decision = await deps.rate_limiter.check(pseudonym)
            if not decision.allowed:
                logger.info("miniapp_rate_limited", reason="rate_limited")
                raise MiniappHttpError(MiniappErrorCode.RATE_LIMITED, 429)

            user_result = await deps.get_user_by_telegram_id.execute(
                GetUserByTelegramIdQuery(telegram_user_id=verified.telegram_user_id)
            )
            return MiniappAuthContext(
                telegram_user_id=verified.telegram_user_id,
                user=user_result.user,
            )

        async def require_actor(
            auth: Annotated[MiniappAuthContext, Depends(authenticate)],
        ) -> User:
            if auth.user is None:
                raise MiniappHttpError(MiniappErrorCode.ONBOARDING_REQUIRED, 403)
            step = await deps.get_onboarding_step.execute(
                GetOnboardingStepQuery(telegram_user_id=auth.telegram_user_id)
            )
            if step.step.kind is OnboardingStepKind.DONE:
                return auth.user
            if step.step.kind is OnboardingStepKind.CONSENT:
                raise MiniappHttpError(MiniappErrorCode.CONSENT_REQUIRED, 403)
            raise MiniappHttpError(MiniappErrorCode.ONBOARDING_REQUIRED, 403)

        self.authenticate = authenticate
        self.require_actor = require_actor


def parse_path_uuid(raw: str) -> UUID:
    """Parse a path UUID or raise 404."""
    try:
        return UUID(raw)
    except ValueError as exc:
        raise MiniappHttpError(MiniappErrorCode.NOT_FOUND, 404) from exc


def no_store(response: Response) -> None:
    """Attach Cache-Control: no-store to successful responses."""
    response.headers["Cache-Control"] = "no-store"
