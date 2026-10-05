"""Inline query and chosen_inline_result handlers."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from enum import StrEnum

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
from svoi_pravila.adapters.channels.telegram.keyboards import suggestion_decision_keyboard
from svoi_pravila.adapters.channels.telegram.localization import firmness_label
from svoi_pravila.adapters.channels.telegram.presenters import render_suggestion_dm
from svoi_pravila.application.errors import (
    AccessNotGranted,
    GenerationRefusedByProvider,
    GenerationUnavailable,
    IncomingTextTooLong,
    InlineComposeFailed,
    InlineQueryTooShort,
    InvalidGenerationOutput,
    InvalidInlineResultRef,
    NotFound,
    PreparedResultUnavailable,
    ScenarioQuotaExceeded,
)
from svoi_pravila.application.inline_result_ref import encode_inline_result_ref
from svoi_pravila.application.inline_reuse_status import InlineReuseStatus
from svoi_pravila.application.ports.generation import AppliedRuleView, SafetyVerdict, Variant
from svoi_pravila.application.ports.monotonic import MonotonicClock
from svoi_pravila.application.prepared_ref import is_prepared_ref
from svoi_pravila.application.use_cases.get_user_by_telegram_id import GetUserByTelegramIdQuery
from svoi_pravila.application.use_cases.inline_compose import (
    InlineComposeCommand,
    InlineComposeResult,
)
from svoi_pravila.application.use_cases.list_contacts import ListContactsCommand
from svoi_pravila.application.use_cases.list_suggestions import ListSuggestionsCommand
from svoi_pravila.application.use_cases.record_inline_choice import RecordInlineChoiceCommand
from svoi_pravila.domain.enums import UsageScenario
from svoi_pravila.domain.ids import RuleSuggestionId, TelegramUserId

_PREVIEW_MAX = 256
_PREPARED_PURPOSE = "prepared"

logger = structlog.get_logger(__name__)


class InlineAnsweredOutcome(StrEnum):
    """C0 vocabulary for ``inline_answered.outcome``."""

    OK = "ok"
    SCREENED = "screened"
    EMPTY = "empty"
    INVALID_OUTPUT = "invalid_output"
    REFUSED = "refused"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    AUTH = "auth"
    SERVER = "server"
    NETWORK = "network"
    TELEGRAM_API = "telegram_api"


@dataclass(frozen=True, slots=True)
class _AnsweredLog:
    """C0 fields for ``inline_answered``."""

    started: float
    monotonic: MonotonicClock
    scenario: UsageScenario | None
    outcome: InlineAnsweredOutcome
    reuse: str


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
        bot: Bot,
    ) -> None:
        user_id = chosen.from_user.id
        result_id = chosen.result_id
        query = chosen.query
        if is_prepared_ref(query):
            pseudonym = tg_deps.pseudonymizer.pseudonymize(_PREPARED_PURPOSE, str(user_id))
            with contextlib.suppress(PreparedResultUnavailable):
                await tg_deps.prepared_results.delete(pseudonym, query)
        try:
            recorded = await tg_deps.record_inline_choice.execute(
                RecordInlineChoiceCommand(TelegramUserId(user_id), result_id)
            )
        except InvalidInlineResultRef:
            return
        if recorded.suggestion_id is not None:
            await _send_tone_suggestion_dm(bot, tg_deps, user_id, recorded.suggestion_id)

    return router


async def _send_tone_suggestion_dm(
    bot: Bot,
    tg_deps: TelegramDeps,
    telegram_user_id: int,
    suggestion_id: RuleSuggestionId,
) -> None:
    """One-time DM for a new tone suggestion; Telegram API failures are C0-logged only."""
    try:
        lookup = await tg_deps.get_user_by_telegram_id.execute(
            GetUserByTelegramIdQuery(TelegramUserId(telegram_user_id))
        )
        user = lookup.user
        if user is None or user.active_contact_id is None:
            return
        pending = await tg_deps.list_suggestions.execute(
            ListSuggestionsCommand(user.id, user.active_contact_id)
        )
        suggestion = next((s for s in pending.suggestions if s.id == suggestion_id), None)
        if suggestion is None or suggestion.firmness is None:
            return
        contacts = await tg_deps.list_contacts.execute(ListContactsCommand(user.id))
        contact = next((c for c in contacts.contacts if c.id == suggestion.contact_id), None)
        if contact is None:
            return
        text = render_suggestion_dm(
            tg_deps.strings,
            firmness=suggestion.firmness,
            contact_label=contact.label.value,
            rule_text=suggestion.text.value,
        )
        await bot.send_message(
            telegram_user_id,
            text,
            reply_markup=suggestion_decision_keyboard(tg_deps.strings, suggestion.id),
        )
    except (TelegramAPIError, NotFound, AccessNotGranted) as exc:
        logger.info(
            "tone_suggestion_dm_failed",
            error_type=type(exc).__name__,
        )


async def _answer_composed(query: InlineQuery, bot: Bot, tg_deps: TelegramDeps) -> None:
    user_id = query.from_user.id
    started = tg_deps.monotonic.monotonic()
    try:
        result = await tg_deps.inline_compose.execute(
            InlineComposeCommand(TelegramUserId(user_id), query.query)
        )
    except (
        NotFound,
        AccessNotGranted,
        InlineQueryTooShort,
        IncomingTextTooLong,
        ScenarioQuotaExceeded,
        InlineComposeFailed,
    ) as exc:
        await _answer_compose_error(query, bot, tg_deps, started=started, error=exc)
        return
    if not tg_deps.inline_queries.is_current_task(user_id):
        return
    await _answer_compose_result(query, bot, tg_deps, started=started, result=result)


async def _answer_compose_error(
    query: InlineQuery,
    bot: Bot,
    tg_deps: TelegramDeps,
    *,
    started: float,
    error: (
        NotFound
        | AccessNotGranted
        | InlineQueryTooShort
        | IncomingTextTooLong
        | ScenarioQuotaExceeded
        | InlineComposeFailed
    ),
) -> None:
    user_id = query.from_user.id
    if not tg_deps.inline_queries.is_current_task(user_id):
        return
    onboard = isinstance(error, (NotFound, AccessNotGranted))
    telegram_ok = await _answer_empty(query, bot, tg_deps, onboard=onboard)
    outcome = _error_outcome(error, telegram_ok=telegram_ok)
    _log_answered(
        _AnsweredLog(
            started=started,
            monotonic=tg_deps.monotonic,
            scenario=None,
            outcome=outcome,
            reuse=_reuse_from_error(error),
        )
    )


def _error_outcome(
    error: (
        NotFound
        | AccessNotGranted
        | InlineQueryTooShort
        | IncomingTextTooLong
        | ScenarioQuotaExceeded
        | InlineComposeFailed
    ),
    *,
    telegram_ok: bool,
) -> InlineAnsweredOutcome:
    if not telegram_ok:
        return InlineAnsweredOutcome.TELEGRAM_API
    cause = error.cause if isinstance(error, InlineComposeFailed) else error
    if isinstance(cause, InvalidGenerationOutput):
        return InlineAnsweredOutcome.INVALID_OUTPUT
    if isinstance(cause, GenerationRefusedByProvider):
        return InlineAnsweredOutcome.REFUSED
    if isinstance(cause, GenerationUnavailable):
        return InlineAnsweredOutcome(cause.kind.value)
    return InlineAnsweredOutcome.EMPTY


async def _answer_compose_result(
    query: InlineQuery,
    bot: Bot,
    tg_deps: TelegramDeps,
    *,
    started: float,
    result: InlineComposeResult,
) -> None:
    reuse = _reuse_label(result.reuse)
    if result.safety is SafetyVerdict.CRISIS:
        telegram_ok = await _answer_empty(
            query,
            bot,
            tg_deps,
            onboard=False,
            deep_link=(tg_deps.strings.inline_button_need_support, "support"),
        )
        outcome = (
            InlineAnsweredOutcome.TELEGRAM_API
            if not telegram_ok
            else InlineAnsweredOutcome.SCREENED
        )
    elif result.safety is SafetyVerdict.REFUSE_MANIPULATION:
        telegram_ok = await _answer_empty(
            query,
            bot,
            tg_deps,
            onboard=False,
            deep_link=(tg_deps.strings.inline_button_why_no_variants, "why"),
        )
        outcome = (
            InlineAnsweredOutcome.TELEGRAM_API if not telegram_ok else InlineAnsweredOutcome.EMPTY
        )
    else:
        articles = _articles(
            tg_deps,
            result.scenario,
            result.variants,
            applied_rules=result.applied_rules,
        )
        if not articles:
            telegram_ok = await _answer_empty(query, bot, tg_deps, onboard=False)
            outcome = (
                InlineAnsweredOutcome.TELEGRAM_API
                if not telegram_ok
                else InlineAnsweredOutcome.EMPTY
            )
        else:
            telegram_ok = await _send_inline_answer(
                bot,
                inline_query_id=query.id,
                results=articles,
                cache_time=tg_deps.inline_cache_seconds,
            )
            outcome = (
                InlineAnsweredOutcome.TELEGRAM_API if not telegram_ok else InlineAnsweredOutcome.OK
            )
    _log_answered(
        _AnsweredLog(
            started=started,
            monotonic=tg_deps.monotonic,
            scenario=result.scenario,
            outcome=outcome,
            reuse=reuse,
        )
    )


def _reuse_label(status: InlineReuseStatus | None) -> str:
    return "none" if status is None else status.value


def _reuse_from_error(
    error: (
        NotFound
        | AccessNotGranted
        | InlineQueryTooShort
        | IncomingTextTooLong
        | ScenarioQuotaExceeded
        | InlineComposeFailed
    ),
) -> str:
    if isinstance(error, InlineComposeFailed):
        return error.reuse.value
    return "none"


def _log_answered(fields: _AnsweredLog) -> None:
    ended = fields.monotonic.monotonic()
    latency_ms = max(0, int((ended - fields.started) * 1000))
    payload: dict[str, object] = {
        "outcome": fields.outcome.value,
        "reuse": fields.reuse,
        "answer_latency_ms": latency_ms,
    }
    if fields.scenario is not None:
        payload["scenario"] = fields.scenario.value
    logger.info("inline_answered", **payload)


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
) -> bool:
    if deep_link is None:
        if onboard:
            deep_link = (tg_deps.strings.inline_button_finish_setup, "start")
        else:
            deep_link = (tg_deps.strings.inline_button_how_to, "help")
    button_text, start_parameter = deep_link
    return await _send_inline_answer(
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
) -> bool:
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
        return False
    return True
