"""Fixtures for persistence integration tests."""

from __future__ import annotations

import pytest

from svoi_pravila.adapters.persistence.uow import SqlAlchemyUnitOfWorkFactory


@pytest.fixture
async def uow_factory(
    uow_factory_postgres: SqlAlchemyUnitOfWorkFactory,
) -> SqlAlchemyUnitOfWorkFactory:
    """Alias shared postgres UoW factory for persistence tests."""
    return uow_factory_postgres
