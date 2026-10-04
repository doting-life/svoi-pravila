"""Inline query and chosen_inline_result handlers."""

from __future__ import annotations

import contextlib

from aiogram import Bot, Router
from aiogram.types import (
    ChosenInlineResult,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultsButton,
    InlineQueryResultUnion,
    InputTextMessageContent,
)

from svoi_pravila.adapters.channels.telegram.deps import TelegramDeps
from svoi_pravila.adapters.channels.telegram.localization import (
    TelegramStrings,
    firmness_label,
)
from svoi_pravila.application.errors import (
    AccessNotGranted,
    GenerationRefusedByProvider,
    GenerationUnavailable,
    IncomingTextTooLong,
    InlineQueryTooShort,
    InvalidGenerationOutput,
    InvalidInlineResultRef,
    NotFound,
    PreparedResultUnavailable,
    ScenarioQuotaExceeded,
)
from svoi_pravila.application.inline_result_ref import encode_inline_result_ref
from svoi_pravila.application.ports.generation import Variant
from svoi_pravila.application.prepared_ref import is_prepared_ref
from svoi_pravila.application.use_cases.inline_compose import InlineComposeCommand
from svoi_pravila.application.use_cases.record_inline_choice import RecordInlineChoiceCommand
from svoi_pravila.domain.enums import UsageScenario
from svoi_pravila.domain.ids import TelegramUserId

_PREVIEW_MAX = 64
_PREPARED_PURPOSE = "prepared"


def build_inline_router() -> Router:
    """Create the router for inline_query and chosen_inline_result."""
    router = Router(name="telegram_inline")

    @router.inline_query()
    async def inline_query(query: InlineQuery, tg_deps: TelegramDeps, bot: Bot) -> None:
        if is_prepared_ref(query.query):
            await _answer_prepared(query, bot, tg_deps)
            return
        tg_deps.inline_queries.submit(query, bot, tg_deps, _answer_composed)

    @router.chosen_inline_result()
    async def chosen_inline_result(
        chosen: ChosenInlineResult,
        tg_deps: TelegramDeps,
    ) -> None:
        user_id = chosen.from_user.id
        result_id = chosen.result_id
        query = chosen.query
        if is_prepared_ref(query):
            pseudonym = tg_deps.pseudonymizer.pseudonymize(_PREPARED_PURPOSE, str(user_id))
            with contextlib.suppress(PreparedResultUnavailable):
                await tg_deps.prepared_results.delete(pseudonym, query)
        try:
            await tg_deps.record_inline_choice.execute(
                RecordInlineChoiceCommand(TelegramUserId(user_id), result_id)
            )
        except InvalidInlineResultRef:
            return

    return router


async def _answer_composed(query: InlineQuery, bot: Bot, tg_deps: TelegramDeps) -> None:
    user_id = query.from_user.id
    try:
        result = await tg_deps.inline_compose.execute(
            InlineComposeCommand(TelegramUserId(user_id), query.query)
        )
    except (NotFound, AccessNotGranted):
        if tg_deps.inline_queries.is_current_task(user_id):
            await _answer_empty(query, bot, tg_deps, onboard=True)
        return
    except (
        InlineQueryTooShort,
        IncomingTextTooLong,
        ScenarioQuotaExceeded,
        InvalidGenerationOutput,
        GenerationRefusedByProvider,
        GenerationUnavailable,
    ):
        if tg_deps.inline_queries.is_current_task(user_id):
            await _answer_empty(query, bot, tg_deps, onboard=False)
        return
    if not tg_deps.inline_queries.is_current_task(user_id):
        return
    articles = _articles(tg_deps.strings, result.scenario, result.variants)
    if not articles:
        await _answer_empty(query, bot, tg_deps, onboard=False)
        return
    await bot.answer_inline_query(
        inline_query_id=query.id,
        results=articles,
        is_personal=True,
        cache_time=tg_deps.inline_cache_seconds,
    )


async def _answer_prepared(query: InlineQuery, bot: Bot, tg_deps: TelegramDeps) -> None:
    pseudonym = tg_deps.pseudonymizer.pseudonymize(_PREPARED_PURPOSE, str(query.from_user.id))
    try:
        variant = await tg_deps.prepared_results.redeem(pseudonym, query.query)
    except PreparedResultUnavailable:
        await _answer_empty(query, bot, tg_deps, onboard=False)
        return
    articles = _articles(
        tg_deps.strings,
        UsageScenario.DECODE,
        (Variant(text=variant.text, firmness=variant.firmness),),
    )
    await bot.answer_inline_query(
        inline_query_id=query.id,
        results=articles,
        is_personal=True,
        cache_time=tg_deps.inline_cache_seconds,
    )


def _articles(
    strings: TelegramStrings,
    scenario: UsageScenario,
    variants: tuple[Variant, ...],
) -> list[InlineQueryResultUnion]:
    articles: list[InlineQueryResultUnion] = []
    for variant in variants:
        if not variant.text:
            continue
        articles.append(
            InlineQueryResultArticle(
                id=encode_inline_result_ref(scenario, variant.firmness),
                title=firmness_label(strings, variant.firmness),
                description=_preview(variant.text),
                input_message_content=InputTextMessageContent(
                    message_text=variant.text,
                    parse_mode=None,
                ),
            )
        )
    return articles


def _preview(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= _PREVIEW_MAX:
        return collapsed
    return collapsed[:_PREVIEW_MAX]


async def _answer_empty(
    query: InlineQuery,
    bot: Bot,
    tg_deps: TelegramDeps,
    *,
    onboard: bool,
) -> None:
    if onboard:
        text = tg_deps.strings.inline_button_finish_setup
        start_parameter = "start"
    else:
        text = tg_deps.strings.inline_button_how_to
        start_parameter = "help"
    await bot.answer_inline_query(
        inline_query_id=query.id,
        results=[],
        is_personal=True,
        cache_time=tg_deps.inline_cache_seconds,
        button=InlineQueryResultsButton(text=text, start_parameter=start_parameter),
    )
