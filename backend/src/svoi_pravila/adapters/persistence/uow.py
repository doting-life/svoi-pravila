"""SQLAlchemy async unit of work."""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from svoi_pravila.adapters.persistence.key_ring import KeyRing
from svoi_pravila.adapters.persistence.registry import RowRegistry
from svoi_pravila.adapters.persistence.repositories import (
    SqlAlchemyConsentRepository,
    SqlAlchemyContactRepository,
    SqlAlchemyInviteRepository,
    SqlAlchemyPairRepository,
    SqlAlchemyRuleRepository,
    SqlAlchemyRuleSuggestionRepository,
    SqlAlchemyToneSignalRepository,
    SqlAlchemyUsageEventRepository,
    SqlAlchemyUserRepository,
)
from svoi_pravila.application.ports.repositories import (
    ConsentRepository,
    ContactRepository,
    InviteRepository,
    PairRepository,
    RuleRepository,
    RuleSuggestionRepository,
    ToneSignalRepository,
    UsageEventRepository,
    UserRepository,
)
from svoi_pravila.application.ports.unit_of_work import UnitOfWork


class SqlAlchemyUnitOfWork:
    """One AsyncSession transaction with a per-UoW key ring and row registry."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        kek: bytes,
        kek_id: str,
    ) -> None:
        self._session_factory = session_factory
        self._kek = kek
        self._kek_id = kek_id
        self._session: AsyncSession | None = None
        self._committed = False
        self.users: UserRepository
        self.consents: ConsentRepository
        self.contacts: ContactRepository
        self.pairs: PairRepository
        self.rules: RuleRepository
        self.invites: InviteRepository
        self.usage_events: UsageEventRepository
        self.rule_suggestions: RuleSuggestionRepository
        self.tone_signals: ToneSignalRepository

    async def __aenter__(self) -> SqlAlchemyUnitOfWork:
        self._session = self._session_factory()
        self._committed = False
        await self._session.begin()
        keys = KeyRing(self._session, kek=self._kek, kek_id=self._kek_id)
        registry = RowRegistry()
        self.users = SqlAlchemyUserRepository(self._session, keys, registry)
        self.consents = SqlAlchemyConsentRepository(self._session, registry)
        self.contacts = SqlAlchemyContactRepository(self._session, keys, registry)
        self.pairs = SqlAlchemyPairRepository(self._session, keys, registry)
        self.rules = SqlAlchemyRuleRepository(self._session, keys, registry)
        self.invites = SqlAlchemyInviteRepository(self._session, registry)
        self.usage_events = SqlAlchemyUsageEventRepository(self._session, registry)
        self.rule_suggestions = SqlAlchemyRuleSuggestionRepository(self._session, keys, registry)
        self.tone_signals = SqlAlchemyToneSignalRepository(self._session, registry)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._session is None:
            return
        try:
            if not self._committed:
                await self._session.rollback()
        finally:
            await self._session.close()
            self._session = None

    async def commit(self) -> None:
        if self._session is None:
            msg = "unit of work is not active"
            raise RuntimeError(msg)
        await self._session.commit()
        self._committed = True


class SqlAlchemyUnitOfWorkFactory:
    """Factory producing SQLAlchemy units of work over a shared engine."""

    def __init__(self, engine: AsyncEngine, *, kek: bytes, kek_id: str) -> None:
        self._session_factory = async_sessionmaker(
            engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )
        self._kek = kek
        self._kek_id = kek_id

    def __call__(self) -> UnitOfWork:
        return SqlAlchemyUnitOfWork(
            self._session_factory,
            kek=self._kek,
            kek_id=self._kek_id,
        )
