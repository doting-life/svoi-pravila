"""GetUserById use-case tests."""

from __future__ import annotations

from uuid import UUID

import pytest

from svoi_pravila.application.use_cases.get_user_by_id import GetUserById, GetUserByIdQuery
from svoi_pravila.domain.ids import UserId
from tests.unit.application.conftest import AppWorld


@pytest.mark.unit
async def test_get_user_by_id_found_and_missing(world: AppWorld) -> None:
    user = await world.ensure_granted_user(901)
    uc = GetUserById(world.uow_factory)
    found = await uc.execute(GetUserByIdQuery(user_id=user.id))
    assert found.user is not None
    assert found.user.id == user.id
    missing = await uc.execute(
        GetUserByIdQuery(user_id=UserId(UUID("01900000-0000-7000-8000-000000000099")))
    )
    assert missing.user is None
