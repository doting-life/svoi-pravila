"""Export user-visible data as an in-memory DTO (never persisted)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.unit_of_work import UnitOfWorkFactory
from svoi_pravila.application.use_cases._effective_rules import collect_visible_rules
from svoi_pravila.domain.ids import TelegramUserId, UserId
from svoi_pravila.domain.rules import RuleRevision

# Canonical mini-app disclosure string for export contents (must match UI catalog).
# Preposition U+0441 is via chr() to avoid RUF001 confusable-literal false positive.
EXPORT_DISCLOSURE_BLURB = (
    "Выгрузка содержит контакты, согласия, правила, предложения правил и историю выбора тона. "
    f"Файл придёт в чат {chr(0x0441)} ботом и не хранится на сервере."
)

_SECTION_DISCLOSURE_PHRASES: dict[str, str] = {
    "согласия": "согласия",
    "контакты": "контакты",
    "правила": "правила",
    "общие_правила": "правила",
    "предложения": "предложения правил",
    "сигналы_тона": "историю выбора тона",
}


def disclosure_phrases_from_export_payload(payload: dict[str, object]) -> frozenset[str]:
    """Map content section keys present in an export payload to disclosure substrings."""
    phrases: set[str] = set()
    if "согласия" in payload:
        phrases.add(_SECTION_DISCLOSURE_PHRASES["согласия"])
    if "контакты" in payload:
        phrases.add(_SECTION_DISCLOSURE_PHRASES["контакты"])
        contacts = payload["контакты"]
        if isinstance(contacts, list):
            for contact in contacts:
                if not isinstance(contact, dict):
                    continue
                for key in ("правила", "общие_правила", "предложения", "сигналы_тона"):
                    if key in contact:
                        phrases.add(_SECTION_DISCLOSURE_PHRASES[key])
    return frozenset(phrases)


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
                        "правила": [
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
                        "общие_правила": [
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
                        "предложения": [
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
                        "сигналы_тона": [
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
                "согласия": [
                    {
                        "вид": consent.kind.value,
                        "версия": consent.text_version,
                        "sha256": consent.text_sha256.value,
                        "выдано": _iso(consent.granted_at),
                        "отозвано": _iso(consent.revoked_at),
                    }
                    for consent in consents
                ],
                "контакты": contacts_payload,
            }
            return ExportMyDataResult(found=True, payload=payload)
