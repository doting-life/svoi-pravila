"""Repository contract fixtures — memory and PostgreSQL unit-of-work factories."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import cast

import pytest

from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from tests.fakes.uow import InMemoryUnitOfWorkFactory


@pytest.fixture
async def uow_factory_memory() -> AsyncIterator[UnitOfWorkFactory]:
    """In-memory unit-of-work factory for contract tests."""
    yield InMemoryUnitOfWorkFactory()


@pytest.fixture(
    params=[
        pytest.param("uow_factory_memory", id="memory", marks=pytest.mark.unit),
        pytest.param("uow_factory_postgres", id="postgres", marks=pytest.mark.integration),
    ]
)
def uow_factory(request: pytest.FixtureRequest) -> UnitOfWorkFactory:
    """Provide a UnitOfWorkFactory for memory or PostgreSQL.

    Sync wrapper avoids nesting ``getfixturevalue`` of an async fixture inside
    another async fixture (unsupported by pytest-asyncio).
    """
    return cast(UnitOfWorkFactory, request.getfixturevalue(request.param))
