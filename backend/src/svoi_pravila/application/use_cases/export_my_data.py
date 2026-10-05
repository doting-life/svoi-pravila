"""Export user-visible data as an in-memory DTO (never persisted)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.use_cases._effective_rules import collect_visible_rules
from svoi_pravila.domain.ids import TelegramUserId, UserId
from svoi_pravila.domain.rules import RuleRevision

# Content-section keys in the export payload (schema identifiers, not UI copy).
EXPORT_SECTION_CONSENTS = "согласия"
EXPORT_SECTION_CONTACTS = "контакты"
EXPORT_SECTION_RULES = "правила"
EXPORT_SECTION_SHARED_RULES = "общие_правила"
EXPORT_SECTION_SUGGESTIONS = "предложения"
EXPORT_SECTION_TONE_SIGNALS = "сигналы_тона"

EXPORT_CONTENT_SECTION_KEYS: frozenset[str] = frozenset(
    {
        EXPORT_SECTION_CONSENTS,
        EXPORT_SECTION_CONTACTS,
        EXPORT_SECTION_RULES,
        EXPORT_SECTION_SHARED_RULES,
        EXPORT_SECTION_SUGGESTIONS,
        EXPORT_SECTION_TONE_SIGNALS,
    }
)

EXPORT_CONTACT_SECTION_KEYS: frozenset[str] = frozenset(
    {
        EXPORT_SECTION_RULES,
        EXPORT_SECTION_SHARED_RULES,
        EXPORT_SECTION_SUGGESTIONS,
        EXPORT_SECTION_TONE_SIGNALS,
    }
)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _revision_payload(revision: RuleRevision, *, actor_id: UserId) -> dict[str, object]:
    author = "я" if revision.author_id == actor_id else "партнёр"
    return {
        "номер": revision.number,
        "текст": revision.text.value,
        "предложено": _iso(revision.proposed_at),
        "начало_действия": _iso(revision.effective_since),
        "автор": author,
    }


@dataclass(frozen=True, slots=True)
class ExportMyDataCommand:
    """Input for ExportMyData."""

    telegram_user_id: TelegramUserId


@dataclass(frozen=True, slots=True)
class ExportMyDataResult:
    """In-memory export payload. ``found`` is False when the user is unknown."""

    found: bool
    payload: dict[str, object] | None


class ExportMyData:
    """Build a JSON-serializable dump of data the user may see."""

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, command: ExportMyDataCommand) -> ExportMyDataResult:
        """Return visible contacts, consents, and rules; partner private rules omitted."""
        async with self._uow_factory() as uow:
            user = await uow.users.get_by_telegram_id(command.telegram_user_id)
            if user is None:
                return ExportMyDataResult(found=False, payload=None)
            consents = await uow.consents.list_for_user(user.id)
            suggestions = await uow.rule_suggestions.list_for_user(user.id)
            tone_signals = await uow.tone_signals.list_for_user(user.id)
            contacts_payload: list[dict[str, object]] = []
            for contact in await uow.contacts.list_for_owner(user.id):
                pair = None
                if contact.pair_id is not None:
                    pair = await uow.pairs.get(contact.pair_id)
                visible = await collect_visible_rules(uow, contact, pair)
                private = [view for view in visible if view.scope_kind == "contact"]
                shared = [view for view in visible if view.scope_kind == "pair"]
                contact_suggestions = [s for s in suggestions if s.contact_id == contact.id]
                contact_tones = [s for s in tone_signals if s.contact_id == contact.id]
                contacts_payload.append(
                    {
                        "подпись": contact.label.value,
                        "отношение": contact.relationship.value,
                        "создан": _iso(contact.created_at),
                        "в_паре": contact.pair_id is not None,
                        EXPORT_SECTION_RULES: [
                            {
                                "категория": view.category.value,
                                "статус": view.status.value,
                                "создано": _iso(view.created_at),
                                "редакции": [
                                    _revision_payload(rev, actor_id=user.id)
                                    for rev in view.revisions
                                ],
                            }
                            for view in private
                        ],
                        EXPORT_SECTION_SHARED_RULES: [
                            {
                                "категория": view.category.value,
                                "статус": view.status.value,
                                "создано": _iso(view.created_at),
                                "редакции": [
                                    _revision_payload(rev, actor_id=user.id)
                                    for rev in view.revisions
                                ],
                            }
                            for view in shared
                        ],
                        EXPORT_SECTION_SUGGESTIONS: [
                            {
                                "источник": suggestion.source.value,
                                "категория": suggestion.category.value,
                                "текст": suggestion.text.value,
                                "жёсткость": (
                                    None
                                    if suggestion.firmness is None
                                    else suggestion.firmness.value
                                ),
                                "статус": suggestion.status.value,
                                "создано": _iso(suggestion.created_at),
                                "решено": _iso(suggestion.decided_at),
                            }
                            for suggestion in contact_suggestions
                        ],
                        EXPORT_SECTION_TONE_SIGNALS: [
                            {"значения": [v.value for v in signal.values]}
                            for signal in contact_tones
                        ],
                    }
                )
            payload: dict[str, object] = {
                "export_version": 1,
                "выгружено": _iso(self._clock.now()),
                "пользователь": {
                    "создан": _iso(user.created_at),
                    "возраст_подтверждён": _iso(user.age_confirmed_at),
                },
                EXPORT_SECTION_CONSENTS: [
                    {
                        "вид": consent.kind.value,
                        "версия": consent.text_version,
                        "sha256": consent.text_sha256.value,
                        "выдано": _iso(consent.granted_at),
                        "отозвано": _iso(consent.revoked_at),
                    }
                    for consent in consents
                ],
                EXPORT_SECTION_CONTACTS: contacts_payload,
            }
            return ExportMyDataResult(found=True, payload=payload)
