"""Unit tests for Telegram export document delivery helper."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.methods import SendDocument
from aiogram.types import BufferedInputFile
from tests.fakes.clock import FakeClock
from tests.fakes.telegram_session import FakeTelegramSession

from svoi_pravila.adapters.channels.telegram.export_document import (
    TelegramExportDelivery,
    send_export_document,
)
from svoi_pravila.application.errors import BotChatUnavailable
from svoi_pravila.domain.ids import TelegramUserId

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _forbidden() -> TelegramForbiddenError:
    method = SendDocument(chat_id=1, document="x")
    return TelegramForbiddenError(method=method, message="Forbidden: bot was blocked by the user")


def _bad_request() -> TelegramBadRequest:
    method = SendDocument(chat_id=1, document="x")
    return TelegramBadRequest(method=method, message="Bad Request: chat not found")


@pytest.mark.unit
async def test_send_export_document_happy() -> None:
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    await send_export_document(
        bot,
        chat_id=42,
        payload={"export_version": 1, "метка": "x"},
        now=_NOW,
        caption="caption",
    )
    documents = [req for req in session.requests if isinstance(req, SendDocument)]
    assert len(documents) == 1
    sent = documents[0]
    assert isinstance(sent.document, BufferedInputFile)
    assert sent.document.filename == "svoi-pravila-export-20260101.json"
    assert sent.caption == "caption"


@pytest.mark.unit
async def test_send_export_forbidden_maps_to_bot_chat_unavailable() -> None:
    session = FakeTelegramSession()
    session.set_error(SendDocument, _forbidden())
    bot = Bot(token="1:TEST", session=session)
    with pytest.raises(BotChatUnavailable):
        await send_export_document(
            bot,
            chat_id=1,
            payload={"export_version": 1},
            now=_NOW,
            caption="c",
        )


@pytest.mark.unit
async def test_send_export_bad_request_maps_to_bot_chat_unavailable() -> None:
    session = FakeTelegramSession()
    session.set_error(SendDocument, _bad_request())
    bot = Bot(token="1:TEST", session=session)
    with pytest.raises(BotChatUnavailable):
        await send_export_document(
            bot,
            chat_id=1,
            payload={"export_version": 1},
            now=_NOW,
            caption="c",
        )


@pytest.mark.unit
async def test_telegram_export_delivery_uses_clock_stamp() -> None:
    session = FakeTelegramSession()
    bot = Bot(token="1:TEST", session=session)
    delivery = TelegramExportDelivery(
        bot,
        clock=FakeClock(start=_NOW),
        caption="Выгрузка ваших данных. Файл не хранится на сервере.",
    )
    await delivery.deliver(TelegramUserId(7), {"export_version": 1})
    documents = [req for req in session.requests if isinstance(req, SendDocument)]
    assert len(documents) == 1
    assert documents[0].chat_id == 7
    assert isinstance(documents[0].document, BufferedInputFile)
    assert documents[0].document.filename == "svoi-pravila-export-20260101.json"
