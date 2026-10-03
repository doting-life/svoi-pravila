"""Unit tests for rule repository defensive error paths."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from svoi_pravila.adapters.persistence.registry import RowRegistry
from svoi_pravila.adapters.persistence.repositories import SqlAlchemyRuleRepository
from svoi_pravila.domain.ids import ContactId
from svoi_pravila.domain.rules import ContactScope


@pytest.mark.unit
async def test_scope_dek_raises_when_contact_missing() -> None:
    session = AsyncMock()
    session.get = AsyncMock(return_value=None)
    repo = SqlAlchemyRuleRepository(session, MagicMock(), RowRegistry())
    with pytest.raises(RuntimeError, match="contact missing"):
        await repo._scope_dek(ContactScope(contact_id=ContactId(UUID(int=1))))


@pytest.mark.unit
async def test_to_domain_raises_when_scope_missing() -> None:
    session = AsyncMock()
    repo = SqlAlchemyRuleRepository(session, MagicMock(), RowRegistry())
    row = MagicMock(
        scope_contact_id=None,
        scope_pair_id=None,
        id=UUID(int=2),
        category="other",
        status="proposed",
        approver_ids=[UUID(int=3)],
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        revisions=[],
    )
    with pytest.raises(RuntimeError, match="missing scope"):
        await repo._to_domain(row)
