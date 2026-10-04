"""Fake aiogram BaseSession that records Bot API calls."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any, cast, get_origin, override

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import TelegramMethod
from aiogram.methods.base import TelegramType
from aiogram.types import Chat, Message, User


class FakeTelegramSession(BaseSession):
    """Record TelegramMethod calls and return configurable results."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[TelegramMethod[Any]] = []
        self._results: dict[type[Any], Any] = {}
        self.closed = False

    def set_result(self, method_type: type[Any], result: Any) -> None:
        """Configure the return value for a TelegramMethod subclass."""
        self._results[method_type] = result

    @override
    async def close(self) -> None:
        self.closed = True

    @override
    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[TelegramType],
        timeout: int | None = None,
    ) -> TelegramType:
        self.requests.append(method)
        method_type = type(method)
        if method_type in self._results:
            return cast(TelegramType, self._results[method_type])
        returning = method.__returning__
        if returning is bool:
            return cast(TelegramType, True)
        if returning is User:
            return cast(TelegramType, User(id=1, is_bot=True, first_name="bot"))
        if returning is list or get_origin(returning) is list:
            return cast(TelegramType, [])
        if returning is Message:
            return cast(
                TelegramType,
                Message(
                    message_id=1,
                    date=datetime(2026, 1, 1, tzinfo=UTC),
                    chat=Chat(id=1, type="private"),
                    text="ok",
                ),
            )
        msg = f"no fake result configured for {method_type.__name__}"
        raise LookupError(msg)

    @override
    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes]:
        _ = raise_for_status
        yield b""
