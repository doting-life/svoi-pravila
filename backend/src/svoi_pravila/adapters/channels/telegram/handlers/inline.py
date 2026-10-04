"""Inline query and chosen_inline_result handlers."""

from __future__ import annotations

import contextlib

import structlog
from aiogram import Bot, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    ChosenInlineResult,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultsButton,
    InlineQueryResultUnion,
    InputTextMessageContent,
)

from svoi_pravila.adapters.channels.telegram.dates import format_display_date
from svoi_pravila.adapters.channels.telegram.deps import TelegramDeps
from svoi_pravila.adapters.channels.telegram.localization import firmness_label
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
from svoi_pravila.application.ports.generation import AppliedRuleView, SafetyVerdict, Variant
from svoi_pravila.application.prepared_ref import is_prepared_ref
from svoi_pravila.application.use_cases.inline_compose import InlineComposeCommand
from svoi_pravila.application.use_cases.record_inline_choice import RecordInlineChoiceCommand
from svoi_pravila.domain.enums import UsageScenario
from svoi_pravila.domain.ids import TelegramUserId

_PREVIEW_MAX = 256
_PREPARED_PURPOSE = "prepared"

logger = structlog.get_logger(__name__)


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
    if result.safety is SafetyVerdict.CRISIS:
        await _answer_empty(
            query,
            bot,
            tg_deps,
            onboard=False,
            deep_link=(tg_deps.strings.inline_button_need_support, "support"),
        )
        return
    if result.safety is SafetyVerdict.REFUSE_MANIPULATION:
        await _answer_empty(
            query,
            bot,
            tg_deps,
            onboard=False,
            deep_link=(tg_deps.strings.inline_button_why_no_variants, "why"),
        )
        return
    articles = _articles(
        tg_deps,
        result.scenario,
        result.variants,
        applied_rules=result.applied_rules,
    )
    if not articles:
        await _answer_empty(query, bot, tg_deps, onboard=False)
        return
    await _send_inline_answer(
        bot,
        inline_query_id=query.id,
        results=articles,
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
        tg_deps,
        UsageScenario.DECODE,
        (Variant(text=variant.text, firmness=variant.firmness),),
    )
    await _send_inline_answer(
        bot,
        inline_query_id=query.id,
        results=articles,
        cache_time=tg_deps.inline_cache_seconds,
    )


def _articles(
    tg_deps: TelegramDeps,
    scenario: UsageScenario,
    variants: tuple[Variant, ...],
    applied_rules: tuple[AppliedRuleView, ...] = (),
) -> list[InlineQueryResultUnion]:
    articles: list[InlineQueryResultUnion] = []
    prefix = ""
    if applied_rules:
        date = format_display_date(
            applied_rules[0].effective_since,
            tg_deps.clock.now(),
            tg_deps.display_timezone,
        )
        prefix = tg_deps.strings.inline_rule_cited_prefix.format(date=date)
    for variant in variants:
        if not variant.text:
            continue
        description = variant.text if not prefix else f"{prefix} {variant.text}"
        articles.append(
            InlineQueryResultArticle(
                id=encode_inline_result_ref(scenario, variant.firmness),
                title=firmness_label(tg_deps.strings, variant.firmness),
                description=_preview(description),
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
    deep_link: tuple[str, str] | None = None,
) -> None:
    if deep_link is None:
        if onboard:
            deep_link = (tg_deps.strings.inline_button_finish_setup, "start")
        else:
            deep_link = (tg_deps.strings.inline_button_how_to, "help")
    button_text, start_parameter = deep_link
    await _send_inline_answer(
        bot,
        inline_query_id=query.id,
        results=[],
        cache_time=tg_deps.inline_cache_seconds,
        button=InlineQueryResultsButton(text=button_text, start_parameter=start_parameter),
    )


async def _send_inline_answer(
    bot: Bot,
    *,
    inline_query_id: str,
    results: list[InlineQueryResultUnion],
    cache_time: int,
    button: InlineQueryResultsButton | None = None,
) -> None:
    failed_class: str | None = None
    try:
        await bot.answer_inline_query(
            inline_query_id=inline_query_id,
            results=results,
            is_personal=True,
            cache_time=cache_time,
            button=button,
        )
    except TelegramAPIError as exc:
        failed_class = type(exc).__name__
    if failed_class is not None:
        logger.error("inline_answer_failed", exception_class=failed_class)
