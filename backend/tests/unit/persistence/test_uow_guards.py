"""Unit tests for SqlAlchemyUnitOfWork inactive-session guards."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from svoi_pravila.adapters.persistence.uow import SqlAlchemyUnitOfWork


@pytest.mark.unit
async def test_aexit_without_session_is_noop() -> None:
    uow = SqlAlchemyUnitOfWork(MagicMock(), kek=b"\x00" * 32, kek_id="k")
    await uow.__aexit__(None, None, None)


@pytest.mark.unit
async def test_commit_without_session_raises() -> None:
    uow = SqlAlchemyUnitOfWork(MagicMock(), kek=b"\x00" * 32, kek_id="k")
    with pytest.raises(RuntimeError, match="not active"):
        await uow.commit()
