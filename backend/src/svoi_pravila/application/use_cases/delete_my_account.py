"""Delete the caller's account, pairs, rules, and usage events."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.id_generator import IdGenerator
from svoi_pravila.application.ports.inline_result_reuse import InlineResultReuse
from svoi_pravila.application.ports.pair_notifier import PairNotifier
from svoi_pravila.application.ports.pseudonymizer import Pseudonymizer
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.use_cases._leave_pair import dissolve_pair_for_leaving_member
from svoi_pravila.application.use_cases._pair_notify import notify_after_commit
from svoi_pravila.domain.ids import ContactId, TelegramUserId, UserId
from svoi_pravila.domain.rules import ContactScope

_ANALYTICS_PURPOSE = "analytics"


@dataclass(frozen=True, slots=True)
class DeleteMyAccountPorts:
    """Collaborators for DeleteMyAccount."""

    uow_factory: UnitOfWorkFactory
    ids: IdGenerator
    pseudonymizer: Pseudonymizer
    clock: Clock
    reuse: InlineResultReuse
    notifier: PairNotifier


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

    def __init__(self, ports: DeleteMyAccountPorts) -> None:
        self._uow_factory = ports.uow_factory
        self._ids = ports.ids
        self._pseudonymizer = ports.pseudonymizer
        self._clock = ports.clock
        self._reuse = ports.reuse
        self._notifier = ports.notifier

    async def execute(self, command: DeleteMyAccountCommand) -> DeleteMyAccountResult:
        """Leave pairs, delete owned data, shred DEK, delete the user row."""
        user_key = str(command.telegram_user_id.value)
        self._reuse.forget(user_key)
        analytics = self._pseudonymizer.pseudonymize(_ANALYTICS_PURPOSE, user_key)
        now = self._clock.now()
        partner_notices: list[tuple[UserId, ContactId]] = []
        async with self._uow_factory() as uow:
            user = await uow.users.get_by_telegram_id(command.telegram_user_id)
            if user is None:
                return DeleteMyAccountResult(found=False)
            for pair in await uow.pairs.list_for_member(user.id):
                remaining_id = pair.other_member(user.id)
                remaining_contact = await uow.contacts.get_for_owner_and_pair(remaining_id, pair.id)
                if remaining_contact is not None:
                    partner_notices.append((remaining_id, remaining_contact.id))
                await dissolve_pair_for_leaving_member(
                    uow,
                    self._ids,
                    actor_id=user.id,
                    pair=pair,
                    now=now,
                )
            if user.active_contact_id is not None:
                await uow.users.update(user.clear_active_contact())
            await uow.rule_suggestions.delete_for_user(user.id)
            await uow.tone_signals.delete_for_user(user.id)
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

        for partner_id, partner_contact_id in partner_notices:
            notice_user = partner_id
            notice_contact = partner_contact_id

            async def _notify(uid: UserId = notice_user, cid: ContactId = notice_contact) -> None:
                await self._notifier.partner_left(uid, cid)

            await notify_after_commit(_notify)
        return DeleteMyAccountResult(found=True)
