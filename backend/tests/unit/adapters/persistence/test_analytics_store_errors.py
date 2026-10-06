"""Analytics store error mapping without a live database."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from svoi_pravila.adapters.persistence.analytics_store import (
    SqlAlchemyAnalyticsStore,
    _as_int,
    _float_or_none,
)
from svoi_pravila.application.errors import AnalyticsErrorKind, AnalyticsJobFailed


class _RaiseOS:
    async def __aenter__(self) -> object:
        raise OSError("down")

    async def __aexit__(self, *args: object) -> None:
        return None


class _RaiseSQL:
    async def __aenter__(self) -> object:
        raise SQLAlchemyError("sql")

    async def __aexit__(self, *args: object) -> None:
        return None


class _BoomConnect:
    def connect(self) -> _RaiseOS:
        return _RaiseOS()

    def begin(self) -> _RaiseOS:
        return _RaiseOS()


class _BoomSql:
    def connect(self) -> _RaiseSQL:
        return _RaiseSQL()

    def begin(self) -> _RaiseSQL:
        return _RaiseSQL()


def _store(engine: object) -> SqlAlchemyAnalyticsStore:
    return SqlAlchemyAnalyticsStore(cast(AsyncEngine, engine))


@pytest.mark.unit
async def test_hold_lock_maps_oserror() -> None:
    with pytest.raises(AnalyticsJobFailed) as exc:
        async with _store(_BoomConnect()).hold_lock():
            pass
    assert exc.value.kind is AnalyticsErrorKind.NETWORK


@pytest.mark.unit
async def test_hold_lock_maps_sqlalchemy_error() -> None:
    with pytest.raises(AnalyticsJobFailed) as exc:
        async with _store(_BoomSql()).hold_lock():
            pass
    assert exc.value.kind is AnalyticsErrorKind.DATABASE


@pytest.mark.unit
async def test_scalar_maps_errors() -> None:
    with pytest.raises(AnalyticsJobFailed) as exc:
        await _store(_BoomConnect()).earliest_event_day("Europe/Moscow")
    assert exc.value.kind is AnalyticsErrorKind.NETWORK
    with pytest.raises(AnalyticsJobFailed) as exc_sql:
        await _store(_BoomSql()).earliest_event_day("Europe/Moscow")
    assert exc_sql.value.kind is AnalyticsErrorKind.DATABASE


@pytest.mark.unit
async def test_execute_maps_errors() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(AnalyticsJobFailed) as exc:
        await _store(_BoomConnect()).purge_usage_events(now, 10)
    assert exc.value.kind is AnalyticsErrorKind.NETWORK
    with pytest.raises(AnalyticsJobFailed) as exc_sql:
        await _store(_BoomSql()).purge_usage_events(now, 10)
    assert exc_sql.value.kind is AnalyticsErrorKind.DATABASE


@pytest.mark.unit
async def test_fetch_day_empty_uses_connect_error() -> None:
    with pytest.raises(AnalyticsJobFailed) as exc:
        await _store(_BoomSql()).fetch_day(datetime(2026, 1, 1, tzinfo=UTC).date())
    assert exc.value.kind is AnalyticsErrorKind.DATABASE
    with pytest.raises(AnalyticsJobFailed) as exc_os:
        await _store(_BoomConnect()).fetch_day(datetime(2026, 1, 1, tzinfo=UTC).date())
    assert exc_os.value.kind is AnalyticsErrorKind.NETWORK


class _LockedTrue:
    def scalar(self) -> bool:
        return True


class _ConnExecuteFail:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def execute(self, *_args: object, **_kwargs: object) -> object:
        raise self._exc

    async def commit(self) -> None:
        return None


class _ConnUnlockFail:
    def __init__(self) -> None:
        self._n = 0

    async def execute(self, *_args: object, **_kwargs: object) -> object:
        self._n += 1
        if self._n == 1:
            return _LockedTrue()
        raise OSError("unlock")

    async def commit(self) -> None:
        return None


class _Enter:
    def __init__(self, conn: object) -> None:
        self._conn = conn

    async def __aenter__(self) -> object:
        return self._conn

    async def __aexit__(self, *_args: object) -> None:
        return None


class _EngineConn:
    def __init__(self, conn: object) -> None:
        self._conn = conn

    def connect(self) -> _Enter:
        return _Enter(self._conn)


@pytest.mark.unit
async def test_hold_lock_execute_maps_errors() -> None:
    with pytest.raises(AnalyticsJobFailed) as exc:
        async with _store(_EngineConn(_ConnExecuteFail(OSError("x")))).hold_lock():
            pass
    assert exc.value.kind is AnalyticsErrorKind.NETWORK
    with pytest.raises(AnalyticsJobFailed) as exc_sql:
        async with _store(_EngineConn(_ConnExecuteFail(SQLAlchemyError("x")))).hold_lock():
            pass
    assert exc_sql.value.kind is AnalyticsErrorKind.DATABASE


@pytest.mark.unit
async def test_hold_lock_unlock_maps_errors() -> None:
    with pytest.raises(AnalyticsJobFailed) as exc:
        async with _store(_EngineConn(_ConnUnlockFail())).hold_lock() as acquired:
            assert acquired is True
    assert exc.value.kind is AnalyticsErrorKind.NETWORK


class _ConnUnlockFailSql:
    def __init__(self) -> None:
        self._n = 0

    async def execute(self, *_args: object, **_kwargs: object) -> object:
        self._n += 1
        if self._n == 1:
            return _LockedTrue()
        raise SQLAlchemyError("unlock")

    async def commit(self) -> None:
        return None


@pytest.mark.unit
async def test_hold_lock_unlock_maps_sqlalchemy_error() -> None:
    with pytest.raises(AnalyticsJobFailed) as exc:
        async with _store(_EngineConn(_ConnUnlockFailSql())).hold_lock() as acquired:
            assert acquired is True
    assert exc.value.kind is AnalyticsErrorKind.DATABASE


class _EmptyResult:
    def all(self) -> list[object]:
        return []

    def scalar(self) -> None:
        return None


class _EmptyConn:
    async def execute(self, *_args: object, **_kwargs: object) -> _EmptyResult:
        return _EmptyResult()

    async def commit(self) -> None:
        return None


@pytest.mark.unit
async def test_compute_day_maps_begin_errors() -> None:
    day = datetime(2026, 1, 1, tzinfo=UTC).date()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(AnalyticsJobFailed) as exc:
        await _store(_BoomConnect()).compute_day(day, "Europe/Moscow", now)
    assert exc.value.kind is AnalyticsErrorKind.NETWORK
    with pytest.raises(AnalyticsJobFailed) as exc_sql:
        await _store(_BoomSql()).compute_day(day, "Europe/Moscow", now)
    assert exc_sql.value.kind is AnalyticsErrorKind.DATABASE


@pytest.mark.unit
async def test_fetch_day_none_when_empty() -> None:
    got = await _store(_EngineConn(_EmptyConn())).fetch_day(datetime(2026, 1, 1, tzinfo=UTC).date())
    assert got is None


class _FailOnNthExecute:
    def __init__(self, n: int, exc: BaseException) -> None:
        self._n = 0
        self._fail_at = n
        self._exc = exc

    async def execute(self, *_args: object, **_kwargs: object) -> _EmptyResult:
        self._n += 1
        if self._n == self._fail_at:
            raise self._exc
        return _EmptyResult()


class _FailNthBegin:
    def __init__(self, n: int, exc: BaseException) -> None:
        self._conn = _FailOnNthExecute(n, exc)

    def begin(self) -> _Enter:
        return _Enter(self._conn)


@pytest.mark.unit
async def test_compute_day_maps_mid_transaction_oserror() -> None:
    with pytest.raises(AnalyticsJobFailed) as exc:
        await _store(_FailNthBegin(2, OSError("x"))).compute_day(
            datetime(2026, 1, 1, tzinfo=UTC).date(),
            "Europe/Moscow",
            datetime(2026, 1, 1, tzinfo=UTC),
        )
    assert exc.value.kind is AnalyticsErrorKind.NETWORK


@pytest.mark.unit
def test_numeric_helpers_accept_driver_types() -> None:
    assert _as_int(3) == 3
    assert _as_int(Decimal("4")) == 4
    assert _float_or_none(None) is None
    assert _float_or_none(2) == 2.0
    assert _float_or_none(1.5) == 1.5
    assert _float_or_none(Decimal("1.5")) == 1.5


@pytest.mark.unit
def test_numeric_helpers_reject_unexpected() -> None:
    flag = True
    with pytest.raises(AnalyticsJobFailed) as exc_bool:
        _as_int(flag)
    assert exc_bool.value.kind is AnalyticsErrorKind.DATABASE
    with pytest.raises(AnalyticsJobFailed) as exc_float:
        _as_int(4.9)
    assert exc_float.value.kind is AnalyticsErrorKind.DATABASE
    with pytest.raises(AnalyticsJobFailed) as exc_str:
        _as_int("x")
    assert exc_str.value.kind is AnalyticsErrorKind.DATABASE
    with pytest.raises(AnalyticsJobFailed) as exc_none:
        _as_int(None)
    assert exc_none.value.kind is AnalyticsErrorKind.DATABASE
    with pytest.raises(AnalyticsJobFailed) as exc_fbool:
        _float_or_none(flag)
    assert exc_fbool.value.kind is AnalyticsErrorKind.DATABASE
    with pytest.raises(AnalyticsJobFailed) as exc_fstr:
        _float_or_none("no")
    assert exc_fstr.value.kind is AnalyticsErrorKind.DATABASE


class _ScalarConn:
    def __init__(self, value: object) -> None:
        self._value = value

    async def execute(self, *_args: object, **_kwargs: object) -> object:
        class _Result:
            def __init__(self, value: object) -> None:
                self._value = value

            def scalar(self) -> object:
                return self._value

        return _Result(self._value)


@pytest.mark.unit
async def test_earliest_event_day_none_and_rejects_non_datetime() -> None:
    none_store = _store(_EngineConn(_ScalarConn(None)))
    assert await none_store.earliest_event_day("Europe/Moscow") is None
    bad_store = _store(_EngineConn(_ScalarConn("not-a-datetime")))
    with pytest.raises(AnalyticsJobFailed) as exc:
        await bad_store.earliest_event_day("Europe/Moscow")
    assert exc.value.kind is AnalyticsErrorKind.DATABASE


@pytest.mark.unit
async def test_earliest_event_day_converts_min_timestamp() -> None:
    when = datetime(2026, 3, 15, 21, 0, tzinfo=UTC)
    store = _store(_EngineConn(_ScalarConn(when)))
    assert (
        await store.earliest_event_day("Europe/Moscow")
        == when.astimezone(ZoneInfo("Europe/Moscow")).date()
    )
