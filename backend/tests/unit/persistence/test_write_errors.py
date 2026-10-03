"""Unit tests for persistence write-error translation."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from svoi_pravila.adapters.persistence.errors import (
    _constraint_name,
    _is_unique_violation,
    raise_write_error,
)
from svoi_pravila.application.errors import ConflictError


class _DriverError(Exception):
    def __init__(
        self,
        *,
        constraint_name: str | None = None,
        sqlstate: str | None = None,
        pgcode: str | None = None,
    ) -> None:
        self.constraint_name = constraint_name
        self.sqlstate = sqlstate
        self.pgcode = pgcode
        super().__init__("driver")


class _WrappedOrigError(Exception):
    def __init__(self, driver: BaseException) -> None:
        self.driver_exception = driver
        super().__init__("wrapped")


def _integrity(orig: BaseException) -> IntegrityError:
    return IntegrityError("stmt", {}, orig)


def test_raise_write_error_maps_stale_data() -> None:
    with pytest.raises(ConflictError):
        raise_write_error(StaleDataError())


def test_raise_write_error_maps_known_unique_constraint() -> None:
    orig = _DriverError(constraint_name="uq_users_telegram_user_id", sqlstate="23505")
    with pytest.raises(ConflictError):
        raise_write_error(_integrity(orig))


def test_raise_write_error_maps_via_driver_exception_chain() -> None:
    driver = _DriverError(constraint_name="uq_invites_token_hash", pgcode="23505")
    with pytest.raises(ConflictError):
        raise_write_error(_integrity(_WrappedOrigError(driver)))


def test_raise_write_error_rethrows_fk_integrity() -> None:
    orig = _DriverError(constraint_name="fk_contacts_pair_id_pairs", sqlstate="23503")
    exc = _integrity(orig)
    with pytest.raises(IntegrityError):
        raise_write_error(exc)


def test_raise_write_error_rethrows_unknown() -> None:
    with pytest.raises(ValueError, match="boom"):
        raise_write_error(ValueError("boom"))


def test_constraint_name_walks_cause_chain() -> None:
    leaf = _DriverError(constraint_name="uq_pairs_member_low_member_high")
    mid = Exception("mid")
    mid.__cause__ = leaf
    assert _constraint_name(_integrity(mid)) == "uq_pairs_member_low_member_high"


def test_constraint_name_none_when_absent() -> None:
    assert _constraint_name(_integrity(Exception("plain"))) is None


def test_is_unique_violation_false_without_codes() -> None:
    assert _is_unique_violation(_integrity(Exception("plain"))) is False


def test_is_unique_violation_via_driver_sqlstate() -> None:
    driver = _DriverError(sqlstate="23505")
    assert _is_unique_violation(_integrity(_WrappedOrigError(driver))) is True
