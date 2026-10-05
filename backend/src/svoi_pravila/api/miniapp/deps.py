"""Mini-app API dependencies: auth, rate limit, actor resolution."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, Request, Response

from svoi_pravila.api.miniapp.errors import MiniappErrorCode
from svoi_pravila.api.miniapp.http import MiniappHttpError
from svoi_pravila.application.errors import AccessNotGranted
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

_LOG = logging.getLogger(__name__)
_AUTH_PREFIX = "tma "
MAX_BODY_BYTES = 16 * 1024


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


def get_miniapp_deps(request: Request) -> MiniappDeps:
    """Load MiniappDeps from app state."""
    deps = getattr(request.app.state, "miniapp_deps", None)
    if deps is None:
        msg = "miniapp_deps not configured"
        raise RuntimeError(msg)
    if not isinstance(deps, MiniappDeps):
        msg = "miniapp_deps has unexpected type"
        raise TypeError(msg)
    return deps


async def enforce_body_limit(request: Request) -> None:
    """Reject bodies larger than 16 KiB."""
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            length = int(content_length)
        except ValueError as exc:
            raise MiniappHttpError(MiniappErrorCode.VALIDATION_ERROR, 422) from exc
        if length > MAX_BODY_BYTES:
            raise MiniappHttpError(MiniappErrorCode.BODY_TOO_LARGE, 413)
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise MiniappHttpError(MiniappErrorCode.BODY_TOO_LARGE, 413)


async def authenticate_miniapp(
    deps: Annotated[MiniappDeps, Depends(get_miniapp_deps)],
    authorization: Annotated[str | None, Header()] = None,
) -> MiniappAuthContext:
    """Parse Authorization: tma …, verify initData, rate-limit, load user."""
    if authorization is None or not authorization.startswith(_AUTH_PREFIX):
        _LOG.info("miniapp_auth reason=missing_or_malformed_header")
        raise MiniappHttpError(MiniappErrorCode.UNAUTHORIZED, 401)
    raw = authorization[len(_AUTH_PREFIX) :]
    try:
        verified = deps.init_data_verifier.verify(raw)
    except InitDataExpired:
        _LOG.info("miniapp_auth reason=init_data_expired")
        raise MiniappHttpError(MiniappErrorCode.INIT_DATA_EXPIRED, 401) from None
    except InitDataInvalid:
        _LOG.info("miniapp_auth reason=init_data_invalid")
        raise MiniappHttpError(MiniappErrorCode.INIT_DATA_INVALID, 401) from None

    pseudonym = deps.pseudonymizer.pseudonymize(
        "miniapp",
        str(verified.telegram_user_id.value),
    )
    decision = await deps.rate_limiter.check(pseudonym)
    if not decision.allowed:
        _LOG.info("miniapp_auth reason=rate_limited")
        raise MiniappHttpError(MiniappErrorCode.RATE_LIMITED, 429)

    user_result = await deps.get_user_by_telegram_id.execute(
        GetUserByTelegramIdQuery(telegram_user_id=verified.telegram_user_id)
    )
    return MiniappAuthContext(
        telegram_user_id=verified.telegram_user_id,
        user=user_result.user,
    )


async def require_actor(
    auth: Annotated[MiniappAuthContext, Depends(authenticate_miniapp)],
    deps: Annotated[MiniappDeps, Depends(get_miniapp_deps)],
) -> User:
    """Require a persisted user with completed onboarding (DONE)."""
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


def map_access_error(exc: AccessNotGranted) -> MiniappHttpError:
    """Map AccessNotGranted to onboarding_required or consent_required."""
    if not exc.status.age_confirmed:
        return MiniappHttpError(MiniappErrorCode.ONBOARDING_REQUIRED, 403)
    if exc.status.missing_consents:
        return MiniappHttpError(MiniappErrorCode.CONSENT_REQUIRED, 403)
    return MiniappHttpError(MiniappErrorCode.ONBOARDING_REQUIRED, 403)


def parse_path_uuid(raw: str) -> UUID:
    """Parse a path UUID or raise 404."""
    try:
        return UUID(raw)
    except ValueError as exc:
        raise MiniappHttpError(MiniappErrorCode.NOT_FOUND, 404) from exc


def no_store(response: Response) -> None:
    """Attach Cache-Control: no-store to successful responses."""
    response.headers["Cache-Control"] = "no-store"
