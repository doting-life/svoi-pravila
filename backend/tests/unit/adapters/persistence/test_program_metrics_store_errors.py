"""Program-metrics store error mapping without a live database."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import cast

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from svoi_pravila.adapters.persistence.program_metrics_store import (
    SqlAlchemyProgramMetricsStore,
    _decimal,
    _decimal_or_none,
)
from svoi_pravila.application.errors import AnalyticsErrorKind, AnalyticsJobFailed
from svoi_pravila.domain.llm_pricing import LlmPrice


class _Raise:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def __aenter__(self) -> object:
        raise self._error

    async def __aexit__(self, *args: object) -> None:
        return None


class _Engine:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def connect(self) -> _Raise:
        return _Raise(self._error)

    def begin(self) -> _Raise:
        return _Raise(self._error)


def _store(error: Exception) -> SqlAlchemyProgramMetricsStore:
    return SqlAlchemyProgramMetricsStore(cast(AsyncEngine, _Engine(error)))


@pytest.mark.unit
@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (OSError("down"), AnalyticsErrorKind.NETWORK),
        (SQLAlchemyError("sql"), AnalyticsErrorKind.DATABASE),
    ],
)
async def test_reads_and_writes_map_errors_to_typed_kinds(
    error: Exception, kind: AnalyticsErrorKind
) -> None:
    store = _store(error)
    day = date(2026, 3, 1)
    with pytest.raises(AnalyticsJobFailed) as read_error:
        await store.fetch_daily(day, day)
    assert read_error.value.kind is kind
    with pytest.raises(AnalyticsJobFailed) as list_error:
        await store.list_prices()
    assert list_error.value.kind is kind
    with pytest.raises(AnalyticsJobFailed) as write_error:
        await store.upsert_price(LlmPrice("m", day, Decimal(1)))
    assert write_error.value.kind is kind


@pytest.mark.unit
def test_decimal_helpers_reject_foreign_types_and_pass_none() -> None:
    assert _decimal(Decimal("1.5")) == Decimal("1.5")
    assert _decimal_or_none(None) is None
    assert _decimal_or_none(Decimal(2)) == Decimal(2)
    with pytest.raises(AnalyticsJobFailed) as caught:
        _decimal(1.5)
    assert caught.value.kind is AnalyticsErrorKind.DATABASE
