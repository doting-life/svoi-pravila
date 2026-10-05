"""Persistence errors and write-error translation."""

from __future__ import annotations

from typing import NoReturn
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from svoi_pravila.application.errors import ConflictError

_CONFLICT_CONSTRAINTS = frozenset(
    {
        "uq_users_telegram_user_id",
        "uq_invites_token_hash",
        "uq_pairs_member_low_member_high",
        "uq_rule_suggestions_tone_user_contact_firmness",
        "uq_rule_suggestions_pending_user_contact_source",
    }
)
_UNIQUE_VIOLATION = "23505"


class RowNotLoadedError(RuntimeError):
    """Update attempted for an entity that was not loaded or added in this UoW."""

    def __init__(self, row_type: type[object], identity: UUID) -> None:
        self.row_type = row_type
        self.identity = identity
        super().__init__(f"{row_type.__name__} {identity} was not loaded in this unit of work")


class DataKeyUnavailableError(RuntimeError):
    """Wrapped DEK row is missing (e.g. crypto-shredding)."""


def _constraint_name(exc: IntegrityError) -> str | None:
    """Walk the DBAPI error chain for a structured constraint name."""
    current: BaseException | None = exc.orig
    while current is not None:
        name = getattr(current, "constraint_name", None)
        if isinstance(name, str):
            return name
        driver = getattr(current, "driver_exception", None)
        if driver is not None:
            name = getattr(driver, "constraint_name", None)
            if isinstance(name, str):
                return name
        current = current.__cause__
    return None


def _is_unique_violation(exc: IntegrityError) -> bool:
    """True when the driver reports PostgreSQL unique_violation (23505)."""
    current: BaseException | None = exc.orig
    while current is not None:
        for attr in ("sqlstate", "pgcode"):
            code = getattr(current, attr, None)
            if code == _UNIQUE_VIOLATION:
                return True
        driver = getattr(current, "driver_exception", None)
        if driver is not None:
            for attr in ("sqlstate", "pgcode"):
                code = getattr(driver, attr, None)
                if code == _UNIQUE_VIOLATION:
                    return True
        current = current.__cause__
    return False


def raise_write_error(exc: BaseException) -> NoReturn:
    """Map stale-version and known unique violations to ConflictError; else re-raise."""
    if isinstance(exc, StaleDataError):
        raise ConflictError() from exc
    if isinstance(exc, IntegrityError):
        name = _constraint_name(exc)
        if name in _CONFLICT_CONSTRAINTS and _is_unique_violation(exc):
            raise ConflictError() from exc
        raise exc
    raise exc


async def flush_or_raise(session: AsyncSession) -> None:
    """Flush the session; translate known write conflicts to ``ConflictError``."""
    try:
        await session.flush()
    except (IntegrityError, StaleDataError) as exc:
        raise_write_error(exc)
