"""Analytics store error mapping without a live database."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from svoi_pravila.adapters.persistence.analytics_store import SqlAlchemyAnalyticsStore
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
