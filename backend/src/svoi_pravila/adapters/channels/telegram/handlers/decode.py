"""Private-chat decode handler: stream analysis, then persist the final message."""

from __future__ import annotations

from dataclasses import dataclass

from aiogram import Bot, Router
from aiogram.types import Message

from svoi_pravila.adapters.channels.telegram.deps import TelegramDeps
from svoi_pravila.adapters.channels.telegram.handlers.helpers import render_current_step
from svoi_pravila.adapters.channels.telegram.localization import TelegramStrings
from svoi_pravila.adapters.channels.telegram.presenters import (
    TELEGRAM_MESSAGE_MAX,
    render_applied_rule_citations,
    render_decode_completed,
)
from svoi_pravila.application.decode_sealing import (
    DecodeSealPorts,
    DecodeSealRequest,
    seal_decode_outcome,
)
from svoi_pravila.application.errors import (
    AccessNotGranted,
    ApplicationError,
    GenerationRefusedByProvider,
    GenerationUnavailable,
    IncomingTextTooLong,
    IncomingTextTooShort,
    InvalidGenerationOutput,
    NotFound,
    ScenarioBusy,
    ScenarioQuotaExceeded,
)
from svoi_pravila.application.ports.generation import AnalysisChunk, DecodeCompleted, SafetyVerdict
from svoi_pravila.application.rule_source import rule_source_callback_data
from svoi_pravila.application.use_cases.decode_incoming import DecodeIncomingCommand
from svoi_pravila.application.use_cases.get_onboarding_step import (
    GetOnboardingStepQuery,
    OnboardingStepKind,
)
from svoi_pravila.application.use_cases.get_user_by_telegram_id import GetUserByTelegramIdQuery
from svoi_pravila.domain.enums import UsageSurface
from svoi_pravila.domain.ids import TelegramUserId

_COPY_MAX = 256


@dataclass
class _DraftStream:
    chat_id: int
    draft_id: int
    interval: float
    accumulated: str = ""
    last_sent: float = 0.0

    async def consider(self, bot: Bot, now: float, *, force: bool) -> None:
        if not self.accumulated:
            return
        if not force and self.last_sent != 0.0 and now - self.last_sent < self.interval:
            return
        await bot.send_message_draft(
            chat_id=self.chat_id,
            draft_id=self.draft_id,
            text=self.accumulated[:TELEGRAM_MESSAGE_MAX],
        )
        self.last_sent = now


def build_decode_router() -> Router:
    """Create the private-chat router for decode after onboarding commands."""
    router = Router(name="telegram_decode")

    @router.message()
    async def private_text(message: Message, tg_deps: TelegramDeps, bot: Bot) -> None:
        if message.from_user is None:
            return
        if message.text is not None and message.text.startswith("/"):
            return
        step = await tg_deps.get_onboarding_step.execute(
            GetOnboardingStepQuery(TelegramUserId(message.from_user.id))
        )
        if step.step.kind is not OnboardingStepKind.DONE:
            await render_current_step(message, tg_deps, message.from_user.id)
            return
        incoming = message.text
        if incoming is None:
            await message.answer(tg_deps.strings.decode_need_text)
            return
        try:
            await _stream_decode(message, tg_deps, bot, incoming)
        except (NotFound, AccessNotGranted):
            await render_current_step(message, tg_deps, message.from_user.id)
        except ApplicationError as exc:
            reply = _decode_error_reply(exc, tg_deps.strings)
            if reply is None:
                raise
            await message.answer(reply)

    return router


async def _stream_decode(
    message: Message,
    tg_deps: TelegramDeps,
    bot: Bot,
    incoming: str,
) -> None:
    if message.from_user is None:
        return
    draft = _DraftStream(
        chat_id=message.chat.id,
        draft_id=message.message_id,
        interval=tg_deps.draft_min_interval_ms / 1000.0,
    )
    async for event in tg_deps.decode_incoming.execute(
        DecodeIncomingCommand(
            telegram_user_id=TelegramUserId(message.from_user.id),
            incoming_text=incoming,
            surface=UsageSurface.DM,
        )
    ):
        if isinstance(event, AnalysisChunk):
            draft.accumulated += event.text
            await draft.consider(bot, tg_deps.monotonic.monotonic(), force=False)
            continue
        if isinstance(event, DecodeCompleted):
            await draft.consider(bot, tg_deps.monotonic.monotonic(), force=True)
            looked_up = await tg_deps.get_user_by_telegram_id.execute(
                GetUserByTelegramIdQuery(TelegramUserId(message.from_user.id))
            )
            user = looked_up.user
            active_contact_id = None if user is None else user.active_contact_id
            sealed = await seal_decode_outcome(
                DecodeSealPorts(
                    prepared_results=tg_deps.prepared_results,
                    rule_sources=tg_deps.rule_sources,
                    pseudonymizer=tg_deps.pseudonymizer,
                ),
                DecodeSealRequest(
                    telegram_user_id=message.from_user.id,
                    incoming_text=incoming,
                    completed=event,
                    active_contact_id=active_contact_id,
                ),
            )
            make_rule_callback = (
                None
                if sealed.rule_source_token is None
                else rule_source_callback_data(sealed.rule_source_token)
            )
            for text, keyboard in render_decode_completed(
                tg_deps.strings,
                event,
                copy_max=_COPY_MAX,
                insert_queries=sealed.insert_queries,
                make_rule_callback=make_rule_callback,
            ):
                await message.answer(text, reply_markup=keyboard)
            if event.result.safety is SafetyVerdict.OK:
                for citation in render_applied_rule_citations(
                    tg_deps.strings,
                    event.applied_rules,
                    now=tg_deps.clock.now(),
                    tz=tg_deps.display_timezone,
                ):
                    await message.answer(citation)


def _decode_error_reply(exc: ApplicationError, strings: TelegramStrings) -> str | None:
    mapping: tuple[tuple[type[ApplicationError], str], ...] = (
        (ScenarioBusy, strings.decode_busy),
        (ScenarioQuotaExceeded, strings.decode_quota),
        (IncomingTextTooShort, strings.decode_too_short),
        (IncomingTextTooLong, strings.decode_too_long),
        (InvalidGenerationOutput, strings.decode_invalid),
        (GenerationRefusedByProvider, strings.decode_refused),
        (GenerationUnavailable, strings.decode_unavailable),
    )
    for error_type, reply in mapping:
        if isinstance(exc, error_type):
            return reply
    return None
