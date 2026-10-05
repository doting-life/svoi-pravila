"""Schema-derived export disclosure phrases must match the mini-app blurb."""

from __future__ import annotations

from uuid import UUID

import pytest

from svoi_pravila.application.use_cases.create_contact import CreateContact, CreateContactCommand
from svoi_pravila.application.use_cases.export_my_data import (
    EXPORT_DISCLOSURE_BLURB,
    ExportMyData,
    ExportMyDataCommand,
    disclosure_phrases_from_export_payload,
)
from svoi_pravila.application.use_cases.propose_rule import ProposeRule, ProposeRuleCommand
from svoi_pravila.domain.enums import Firmness, RelationshipKind, RuleCategory
from svoi_pravila.domain.ids import RuleSuggestionId
from svoi_pravila.domain.rule_suggestion import RuleSuggestion, ToneSignal
from svoi_pravila.domain.text import ContactLabel, RuleText
from tests.unit.application.conftest import AppWorld


@pytest.mark.unit
def test_disclosure_phrases_helper_edge_cases() -> None:
    assert disclosure_phrases_from_export_payload({}) == frozenset()
    assert disclosure_phrases_from_export_payload({"согласия": []}) == frozenset({"согласия"})
    assert disclosure_phrases_from_export_payload({"контакты": "bad"}) == frozenset({"контакты"})
    mixed = disclosure_phrases_from_export_payload({"контакты": ["skip", {"общие_правила": []}]})
    assert mixed == frozenset({"контакты", "правила"})


@pytest.mark.unit
async def test_disclosure_phrases_cover_rich_export_sections(world: AppWorld) -> None:
    user = await world.ensure_granted_user(6015)
    created = await CreateContact(world.uow_factory, world.catalog, world.ids, world.clock).execute(
        CreateContactCommand(user.id, ContactLabel("disclosure contact"), RelationshipKind.FRIEND)
    )
    await ProposeRule(world.uow_factory, world.catalog, world.ids, world.clock).execute(
        ProposeRuleCommand(
            user.id,
            created.contact.id,
            RuleCategory.OTHER,
            RuleText("disclosure private rule"),
            shared=False,
        )
    )
    async with world.uow_factory() as uow:
        await uow.rule_suggestions.add(
            RuleSuggestion.create_tone(
                suggestion_id=RuleSuggestionId(UUID(int=6015)),
                user_id=user.id,
                contact_id=created.contact.id,
                category=RuleCategory.HOW_TO_ASK,
                text=RuleText("Говорить мягко в disclosure"),
                firmness=Firmness.GENTLE,
                now=world.clock.now(),
            )
        )
        await uow.tone_signals.upsert(
            ToneSignal(
                user_id=user.id,
                contact_id=created.contact.id,
                values=(Firmness.GENTLE,) * 5,
            )
        )
        await uow.commit()

    dumped = await ExportMyData(world.uow_factory, world.clock).execute(
        ExportMyDataCommand(user.telegram_user_id)
    )
    assert dumped.found is True
    assert dumped.payload is not None
    phrases = disclosure_phrases_from_export_payload(dumped.payload)
    assert phrases == frozenset(
        {
            "контакты",
            "согласия",
            "правила",
            "предложения правил",
            "историю выбора тона",
        }
    )
    for phrase in phrases:
        assert phrase in EXPORT_DISCLOSURE_BLURB
    assert EXPORT_DISCLOSURE_BLURB == (
        "Выгрузка содержит контакты, согласия, правила, предложения правил и историю выбора тона. "
        "Файл придёт в чат с ботом и не хранится на сервере."
    )
