"""Contact use-case tests."""

from __future__ import annotations

from uuid import UUID

import pytest

from svoi_pravila.application.errors import ContactLimitReached, NotFound
from svoi_pravila.application.use_cases.create_contact import (
    CreateContact,
    CreateContactCommand,
)
from svoi_pravila.application.use_cases.list_contacts import ListContacts, ListContactsCommand
from svoi_pravila.application.use_cases.rename_contact import (
    RenameContact,
    RenameContactCommand,
)
from svoi_pravila.domain.contact import MAX_CONTACTS_PER_USER
from svoi_pravila.domain.enums import RelationshipKind
from svoi_pravila.domain.ids import ContactId
from svoi_pravila.domain.text import ContactLabel
from tests.unit.application.conftest import AppWorld


@pytest.mark.unit
async def test_contact_crud_owner_only(world: AppWorld) -> None:
    owner = await world.ensure_granted_user(10)
    stranger = await world.ensure_granted_user(11)
    created = await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
        CreateContactCommand(owner.id, ContactLabel("Alex"), RelationshipKind.FRIEND)
    )
    renamed = await RenameContact(world.uow_factory, world.catalog).execute(
        RenameContactCommand(owner.id, created.contact.id, ContactLabel("Sam"))
    )
    assert renamed.contact.label.value == "Sam"
    listed = await ListContacts(world.uow_factory, world.catalog).execute(
        ListContactsCommand(owner.id)
    )
    assert len(listed.contacts) == 1

    with pytest.raises(NotFound):
        await RenameContact(world.uow_factory, world.catalog).execute(
            RenameContactCommand(stranger.id, created.contact.id, ContactLabel("X"))
        )
    with pytest.raises(NotFound):
        await RenameContact(world.uow_factory, world.catalog).execute(
            RenameContactCommand(owner.id, ContactId(UUID(int=9999)), ContactLabel("X"))
        )


@pytest.mark.unit
async def test_contact_limit(world: AppWorld) -> None:
    owner = await world.ensure_granted_user(12)
    uc = CreateContact(world.uow_factory, world.catalog, world.ids, world.clock)
    for i in range(MAX_CONTACTS_PER_USER):
        await uc.execute(
            CreateContactCommand(owner.id, ContactLabel(f"c{i}"), RelationshipKind.OTHER)
        )
    with pytest.raises(ContactLimitReached):
        await uc.execute(
            CreateContactCommand(owner.id, ContactLabel("overflow"), RelationshipKind.OTHER)
        )
