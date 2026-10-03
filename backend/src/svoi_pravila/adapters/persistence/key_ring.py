"""Per-unit-of-work DEK cache and wrapping helpers."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from svoi_pravila.adapters.persistence.errors import DataKeyUnavailableError
from svoi_pravila.adapters.persistence.models import PairKeyRow, UserKeyRow
from svoi_pravila.crypto import generate_dek, unwrap_dek, wrap_dek


class KeyRing:
    """Create and unwrap DEKs; cache plaintext DEKs only for the active UoW."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        kek: bytes,
        kek_id: str,
    ) -> None:
        self._session = session
        self._kek = kek
        self._kek_id = kek_id
        self._user_deks: dict[UUID, bytes] = {}
        self._pair_deks: dict[UUID, bytes] = {}

    async def create_user_dek(self, user_id: UUID, *, created_at: datetime) -> bytes:
        """Generate, wrap, persist, and cache a user DEK."""
        dek = generate_dek()
        wrapped = wrap_dek(self._kek, dek, owner_kind="user", owner_id=user_id)
        self._session.add(
            UserKeyRow(
                user_id=user_id,
                wrapped_dek=wrapped,
                kek_id=self._kek_id,
                created_at=created_at,
            )
        )
        self._user_deks[user_id] = dek
        return dek

    async def create_pair_dek(self, pair_id: UUID, *, created_at: datetime) -> bytes:
        """Generate, wrap, persist, and cache a pair DEK."""
        dek = generate_dek()
        wrapped = wrap_dek(self._kek, dek, owner_kind="pair", owner_id=pair_id)
        self._session.add(
            PairKeyRow(
                pair_id=pair_id,
                wrapped_dek=wrapped,
                kek_id=self._kek_id,
                created_at=created_at,
            )
        )
        self._pair_deks[pair_id] = dek
        return dek

    async def user_dek(self, user_id: UUID) -> bytes:
        """Return the user DEK, loading and unwrapping if needed."""
        cached = self._user_deks.get(user_id)
        if cached is not None:
            return cached
        row = await self._session.get(UserKeyRow, user_id)
        if row is None:
            raise DataKeyUnavailableError()
        dek = unwrap_dek(self._kek, row.wrapped_dek, owner_kind="user", owner_id=user_id)
        self._user_deks[user_id] = dek
        return dek

    async def pair_dek(self, pair_id: UUID) -> bytes:
        """Return the pair DEK, loading and unwrapping if needed."""
        cached = self._pair_deks.get(pair_id)
        if cached is not None:
            return cached
        row = await self._session.get(PairKeyRow, pair_id)
        if row is None:
            raise DataKeyUnavailableError()
        dek = unwrap_dek(self._kek, row.wrapped_dek, owner_kind="pair", owner_id=pair_id)
        self._pair_deks[pair_id] = dek
        return dek
