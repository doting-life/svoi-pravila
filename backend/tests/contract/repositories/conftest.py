"""Repository contract fixtures — parametrize by UoW factory for 0003 reuse."""

from __future__ import annotations

import pytest

from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from tests.fakes.uow import InMemoryUnitOfWorkFactory


@pytest.fixture(params=["memory"])
def uow_factory(request: pytest.FixtureRequest) -> UnitOfWorkFactory:
    """Provide a UnitOfWorkFactory; 0003 will add a PostgreSQL param."""
    if request.param == "memory":
        return InMemoryUnitOfWorkFactory()
    msg = f"unknown uow factory: {request.param}"
    raise AssertionError(msg)
