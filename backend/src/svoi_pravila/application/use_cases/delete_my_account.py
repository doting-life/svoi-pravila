"""Delete the caller's account, pairs, rules, and usage events."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.application.ports.id_generator import IdGenerator
from svoi_pravila.application.ports.pseudonymizer import Pseudonymizer
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.use_cases._leave_pair import dissolve_pair_for_leaving_member
from svoi_pravila.domain.ids import TelegramUserId
from svoi_pravila.domain.rules import ContactScope

_ANALYTICS_PURPOSE = "analytics"


@dataclass(frozen=True, slots=True)
class DeleteMyAccountCommand:
    """Input for DeleteMyAccount."""

    telegram_user_id: TelegramUserId


@dataclass(frozen=True, slots=True)
class DeleteMyAccountResult:
    """Result of DeleteMyAccount. ``found`` is False when the user is unknown."""

    found: bool


class DeleteMyAccount:
    """Erase the user in one unit of work, including D-7 leave of every pair."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        ids: IdGenerator,
        pseudonymizer: Pseudonymizer,
    ) -> None:
        self._uow_factory = uow_factory
        self._ids = ids
        self._pseudonymizer = pseudonymizer

    async def execute(self, command: DeleteMyAccountCommand) -> DeleteMyAccountResult:
        """Leave pairs, delete owned data, shred DEK, delete the user row."""
        analytics = self._pseudonymizer.pseudonymize(
            _ANALYTICS_PURPOSE,
            str(command.telegram_user_id.value),
        )
        async with self._uow_factory() as uow:
            user = await uow.users.get_by_telegram_id(command.telegram_user_id)
            if user is None:
                return DeleteMyAccountResult(found=False)
            for pair in await uow.pairs.list_for_member(user.id):
                await dissolve_pair_for_leaving_member(
                    uow,
                    self._ids,
                    actor_id=user.id,
                    pair=pair,
                )
            if user.active_contact_id is not None:
                await uow.users.update(user.clear_active_contact())
            for invite in await uow.invites.list_involving(user.id):
                await uow.invites.delete(invite.id)
            for contact in await uow.contacts.list_for_owner(user.id):
                for rule in await uow.rules.list_for_scope(ContactScope(contact_id=contact.id)):
                    await uow.rules.delete(rule.id)
                await uow.contacts.delete(contact.id)
            await uow.consents.delete_for_user(user.id)
            await uow.usage_events.delete_for_pseudonym(analytics)
            await uow.users.delete(user.id)
            await uow.commit()
            return DeleteMyAccountResult(found=True)
