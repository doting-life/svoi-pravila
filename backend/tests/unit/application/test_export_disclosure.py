"""Export payload section keys stay aligned with the shared privacy catalog."""

from __future__ import annotations

from uuid import UUID

import pytest

from svoi_pravila.application.use_cases.approve_rule import ApproveRule, ApproveRuleCommand
from svoi_pravila.application.use_cases.export_my_data import (
    EXPORT_CONTACT_SECTION_KEYS,
    EXPORT_CONTENT_SECTION_KEYS,
    EXPORT_SECTION_CONSENTS,
    EXPORT_SECTION_CONTACTS,
    ExportMyData,
    ExportMyDataCommand,
)
from svoi_pravila.application.use_cases.propose_rule import ProposeRule, ProposeRuleCommand
from svoi_pravila.domain.enums import Firmness, RuleCategory
from svoi_pravila.domain.ids import RuleSuggestionId
from svoi_pravila.domain.rule_suggestion import RuleSuggestion, ToneSignal
from svoi_pravila.domain.text import RuleText
from svoi_pravila.privacy import load_privacy_catalog
from tests.unit.application.conftest import AppWorld
from tests.unit.application.test_rules_and_invites import _pair_world


def _content_section_keys_in_payload(payload: dict[str, object]) -> frozenset[str]:
    found: set[str] = set()
    for key in payload:
        if key in EXPORT_CONTENT_SECTION_KEYS:
            found.add(key)
    contacts = payload.get(EXPORT_SECTION_CONTACTS)
    if isinstance(contacts, list):
        for contact in contacts:
            if not isinstance(contact, dict):
                continue
            for key in contact:
                if key in EXPORT_CONTACT_SECTION_KEYS:
                    found.add(key)
    return frozenset(found)


@pytest.mark.unit
def test_catalog_covers_every_export_section_key() -> None:
    catalog = load_privacy_catalog()
    for key in EXPORT_CONTENT_SECTION_KEYS:
        assert key in catalog.export.sections
        name = catalog.export.sections[key]
        assert name in catalog.export.description


@pytest.mark.unit
async def test_paired_export_shape_hides_partner_labels(world: AppWorld) -> None:
    inviter, invitee, contact, accepted = await _pair_world(world)
    shared = await ProposeRule(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        ProposeRuleCommand(
            inviter.id,
            contact.id,
            RuleCategory.OTHER,
            RuleText("paired export shared"),
            shared=True,
        )
    )
    await ApproveRule(world.uow_factory, world.catalog, world.clock, world.notifier).execute(
        ApproveRuleCommand(invitee.id, shared.rule.id)
    )
    dumped = await ExportMyData(world.uow_factory, world.clock).execute(
        ExportMyDataCommand(inviter.telegram_user_id)
    )
    assert dumped.found is True
    assert dumped.payload is not None
    contacts = dumped.payload["контакты"]
    assert isinstance(contacts, list)
    assert len(contacts) == 1
    row = contacts[0]
    assert row["в_паре"] is True
    assert row["подпись"] == "Partner"
    assert "общие_правила" in row
    shared_rules = row["общие_правила"]
    assert isinstance(shared_rules, list)
    assert shared_rules
    authors = {rev["автор"] for rev in shared_rules[0]["редакции"]}
    assert authors <= {"я", "партнёр"}
    blob = str(dumped.payload)
    assert "Inviter" not in blob
    assert str(invitee.telegram_user_id.value) not in blob
    assert str(accepted.pair.id) not in blob
    assert str(invitee.id) not in blob


@pytest.mark.unit
async def test_rich_export_uses_only_declared_content_section_keys(world: AppWorld) -> None:
    inviter, invitee, contact, _accepted = await _pair_world(world)
    await ProposeRule(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        ProposeRuleCommand(
            inviter.id,
            contact.id,
            RuleCategory.OTHER,
            RuleText("private disclosure rule"),
            shared=False,
        )
    )
    shared = await ProposeRule(
        world.uow_factory, world.catalog, world.ids, world.clock, world.notifier
    ).execute(
        ProposeRuleCommand(
            inviter.id,
            contact.id,
            RuleCategory.OTHER,
            RuleText("shared disclosure rule"),
            shared=True,
        )
    )
    await ApproveRule(world.uow_factory, world.catalog, world.clock, world.notifier).execute(
        ApproveRuleCommand(invitee.id, shared.rule.id)
    )
    async with world.uow_factory() as uow:
        await uow.rule_suggestions.add(
            RuleSuggestion.create_tone(
                suggestion_id=RuleSuggestionId(UUID(int=7015)),
                user_id=inviter.id,
                contact_id=contact.id,
                category=RuleCategory.HOW_TO_ASK,
                text=RuleText("Говорить мягко в disclosure"),
                firmness=Firmness.GENTLE,
                now=world.clock.now(),
            )
        )
        await uow.tone_signals.upsert(
            ToneSignal(
                user_id=inviter.id,
                contact_id=contact.id,
                values=(Firmness.GENTLE,) * 5,
            )
        )
        await uow.commit()

    dumped = await ExportMyData(world.uow_factory, world.clock).execute(
        ExportMyDataCommand(inviter.telegram_user_id)
    )
    assert dumped.found is True
    assert dumped.payload is not None
    keys = _content_section_keys_in_payload(dumped.payload)
    assert keys <= EXPORT_CONTENT_SECTION_KEYS
    assert EXPORT_SECTION_CONSENTS in keys
    assert EXPORT_SECTION_CONTACTS in keys
    assert keys == EXPORT_CONTENT_SECTION_KEYS
