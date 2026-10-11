"""Mini-app API dependencies: auth, rate limit, actor resolution."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import Header, Response

from svoi_pravila.api.miniapp.errors import MiniappErrorCode
from svoi_pravila.api.miniapp.http import MiniappHttpError
from svoi_pravila.application.errors import (
    ComposeSessionUnavailable,
    NotFound,
)
from svoi_pravila.application.ports.compose_sessions import ComposeSessions
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
from svoi_pravila.application.use_cases.get_user_by_id import GetUserById, GetUserByIdQuery
from svoi_pravila.application.use_cases.get_user_by_telegram_id import (
    GetUserByTelegramId,
    GetUserByTelegramIdQuery,
)
from svoi_pravila.domain.ids import ContactId, TelegramUserId
from svoi_pravila.domain.user import User

logger = structlog.get_logger(__name__)
_TMA_PREFIX = "tma "
_COMPOSE_PREFIX = "compose "
_HANDOFF_PREFIX = "handoff "
_INVITE_START_PREFIX = "inv_"


class MiniappScope(StrEnum):
    """Authorization scope for a mini-app principal."""

    FULL = "full"
    COMPOSE = "compose"


@dataclass(frozen=True, slots=True)
class TmaCredential:
    """Parsed ``Authorization: tma <initData>``."""

    raw: str


@dataclass(frozen=True, slots=True)
class ComposeCredential:
    """Parsed ``Authorization: compose <cs_>``."""

    token: str


@dataclass(frozen=True, slots=True)
class MiniappAuthContext:
    """Authenticated mini-app request context from ``tma`` initData (no PII beyond C1)."""

    telegram_user_id: TelegramUserId
    user: User | None
    start_param: str | None


@dataclass(frozen=True, slots=True)
class MiniappPrincipal:
    """Resolved actor with an explicit scope."""

    user: User
    scope: MiniappScope
    bound_contact_id: ContactId | None = None


@dataclass(frozen=True, slots=True)
class MiniappDeps:
    """Composition-root bindings for the mini-app API."""

    init_data_verifier: InitDataVerifier
    rate_limiter: RateLimiter
    pseudonymizer: Pseudonymizer
    get_user_by_telegram_id: GetUserByTelegramId
    get_user_by_id: GetUserById
    get_onboarding_step: GetOnboardingStep
    compose_sessions: ComposeSessions


def parse_session_authorization(authorization: str | None) -> TmaCredential | ComposeCredential:
    """Parse session ``Authorization`` into a tagged credential; Missing/Unknown → 401."""
    if authorization is None:
        logger.info("miniapp_auth_rejected", reason="missing_or_malformed_header")
        raise MiniappHttpError(MiniappErrorCode.UNAUTHORIZED, 401)
    if authorization.startswith(_TMA_PREFIX):
        return TmaCredential(raw=authorization[len(_TMA_PREFIX) :])
    if authorization.startswith(_COMPOSE_PREFIX):
        return ComposeCredential(token=authorization[len(_COMPOSE_PREFIX) :])
    logger.info("miniapp_auth_rejected", reason="unknown_scheme")
    raise MiniappHttpError(MiniappErrorCode.UNAUTHORIZED, 401)


async def _rate_limit(deps: MiniappDeps, telegram_user_id: TelegramUserId) -> None:
    pseudonym = deps.pseudonymizer.pseudonymize("miniapp", str(telegram_user_id.value))
    decision = await deps.rate_limiter.check(pseudonym)
    if not decision.allowed:
        logger.info("miniapp_rate_limited", reason="rate_limited")
        raise MiniappHttpError(MiniappErrorCode.RATE_LIMITED, 429)


async def _require_onboarded(deps: MiniappDeps, user: User) -> User:
    step = await deps.get_onboarding_step.execute(
        GetOnboardingStepQuery(telegram_user_id=user.telegram_user_id)
    )
    if step.step.kind is OnboardingStepKind.DONE:
        return user
    if step.step.kind is OnboardingStepKind.CONSENT:
        raise MiniappHttpError(MiniappErrorCode.CONSENT_REQUIRED, 403)
    raise MiniappHttpError(MiniappErrorCode.ONBOARDING_REQUIRED, 403)


async def _principal_from_compose(
    deps: MiniappDeps, credential: ComposeCredential
) -> MiniappPrincipal:
    try:
        session = await deps.compose_sessions.load(credential.token)
    except ComposeSessionUnavailable:
        logger.info("compose_auth_rejected", reason="session_unavailable")
        raise MiniappHttpError(MiniappErrorCode.COMPOSE_SESSION_EXPIRED, 401) from None
    user_result = await deps.get_user_by_id.execute(GetUserByIdQuery(user_id=session.user_id))
    user = user_result.user
    if user is None:
        logger.info("compose_auth_rejected", reason="user_missing")
        raise MiniappHttpError(MiniappErrorCode.COMPOSE_SESSION_EXPIRED, 401)
    await _rate_limit(deps, user.telegram_user_id)
    user = await _require_onboarded(deps, user)
    return MiniappPrincipal(
        user=user,
        scope=MiniappScope.COMPOSE,
        bound_contact_id=session.contact_id,
    )


async def _authenticate_tma(deps: MiniappDeps, credential: TmaCredential) -> MiniappAuthContext:
    try:
        verified = deps.init_data_verifier.verify(credential.raw)
    except InitDataExpired:
        logger.info("miniapp_auth_rejected", reason="init_data_expired")
        raise MiniappHttpError(MiniappErrorCode.INIT_DATA_EXPIRED, 401) from None
    except InitDataInvalid:
        logger.info("miniapp_auth_rejected", reason="init_data_invalid")
        raise MiniappHttpError(MiniappErrorCode.INIT_DATA_INVALID, 401) from None

    await _rate_limit(deps, verified.telegram_user_id)
    user_result = await deps.get_user_by_telegram_id.execute(
        GetUserByTelegramIdQuery(telegram_user_id=verified.telegram_user_id)
    )
    return MiniappAuthContext(
        telegram_user_id=verified.telegram_user_id,
        user=user_result.user,
        start_param=verified.start_param,
    )


@dataclass(slots=True)
class MiniappAuthenticator:
    """Per-router auth callables closed over composition-root bindings."""

    deps: MiniappDeps
    authenticate: Callable[..., Awaitable[MiniappAuthContext]] = field(init=False, repr=False)
    require_actor: Callable[..., Awaitable[User]] = field(init=False, repr=False)
    require_compose_actor: Callable[..., Awaitable[MiniappPrincipal]] = field(
        init=False, repr=False
    )

    def __post_init__(self) -> None:
        deps = self.deps

        async def authenticate(
            authorization: Annotated[str | None, Header()] = None,
        ) -> MiniappAuthContext:
            credential = parse_session_authorization(authorization)
            if isinstance(credential, ComposeCredential):
                await _principal_from_compose(deps, credential)
                raise MiniappHttpError(MiniappErrorCode.SCOPE_INSUFFICIENT, 403)
            return await _authenticate_tma(deps, credential)

        async def require_actor(
            authorization: Annotated[str | None, Header()] = None,
        ) -> User:
            credential = parse_session_authorization(authorization)
            if isinstance(credential, ComposeCredential):
                await _principal_from_compose(deps, credential)
                raise MiniappHttpError(MiniappErrorCode.SCOPE_INSUFFICIENT, 403)
            auth = await _authenticate_tma(deps, credential)
            if auth.user is None:
                raise MiniappHttpError(MiniappErrorCode.ONBOARDING_REQUIRED, 403)
            return await _require_onboarded(deps, auth.user)

        async def require_compose_actor(
            authorization: Annotated[str | None, Header()] = None,
        ) -> MiniappPrincipal:
            credential = parse_session_authorization(authorization)
            if isinstance(credential, ComposeCredential):
                return await _principal_from_compose(deps, credential)
            auth = await _authenticate_tma(deps, credential)
            if auth.user is None:
                raise MiniappHttpError(MiniappErrorCode.ONBOARDING_REQUIRED, 403)
            user = await _require_onboarded(deps, auth.user)
            return MiniappPrincipal(user=user, scope=MiniappScope.FULL)

        self.authenticate = authenticate
        self.require_actor = require_actor
        self.require_compose_actor = require_compose_actor


def parse_handoff_authorization(authorization: str | None) -> str:
    """Extract ``h_`` token from ``Authorization: handoff <h>`` or raise 401."""
    if authorization is None or not authorization.startswith(_HANDOFF_PREFIX):
        logger.info("compose_auth_rejected", reason="handoff_header")
        raise MiniappHttpError(MiniappErrorCode.HANDOFF_EXPIRED, 401)
    token = authorization[len(_HANDOFF_PREFIX) :]
    if not token:
        raise MiniappHttpError(MiniappErrorCode.HANDOFF_EXPIRED, 401)
    return token


def enforce_compose_contact(principal: MiniappPrincipal, contact_id: ContactId | None) -> None:
    """Under COMPOSE, contact must be null or equal the bound contact; else 404."""
    if principal.scope is MiniappScope.FULL:
        return
    if contact_id is None:
        return
    if principal.bound_contact_id is None or contact_id != principal.bound_contact_id:
        raise MiniappHttpError(MiniappErrorCode.NOT_FOUND, 404)


def invite_raw_token_from_start_param(start_param: str | None) -> str:
    """Extract the raw invite token from ``start_param``; missing/forged → NotFound."""
    if start_param is None or not start_param.startswith(_INVITE_START_PREFIX):
        raise NotFound()
    raw = start_param.removeprefix(_INVITE_START_PREFIX)
    if not raw:
        raise NotFound()
    return raw


def parse_path_uuid(raw: str) -> UUID:
    """Parse a path UUID or raise 404."""
    try:
        return UUID(raw)
    except ValueError as exc:
        raise MiniappHttpError(MiniappErrorCode.NOT_FOUND, 404) from exc


def no_store(response: Response) -> None:
    """Attach Cache-Control: no-store to successful responses."""
    response.headers["Cache-Control"] = "no-store"
