"""Inline query and chosen_inline_result handlers."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlencode

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
    WebAppInfo,
)

from svoi_pravila.adapters.channels.telegram.deps import TelegramDeps
from svoi_pravila.adapters.channels.telegram.localization import variant_label
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
    ServiceBudgetExhausted,
    UserQuotaExhausted,
)
from svoi_pravila.application.inline_result_ref import encode_inline_result_ref
from svoi_pravila.application.inline_reuse_status import InlineReuseStatus
from svoi_pravila.application.ports.generation import SafetyVerdict, Variant
from svoi_pravila.application.ports.monotonic import MonotonicClock
from svoi_pravila.application.prepared_ref import is_prepared_ref
from svoi_pravila.application.use_cases.inline_compose import (
    InlineComposeCommand,
    InlineComposeResult,
)
from svoi_pravila.application.use_cases.issue_compose_handoff import IssueComposeHandoffCommand
from svoi_pravila.application.use_cases.record_inline_choice import RecordInlineChoiceCommand
from svoi_pravila.domain.enums import UsageScenario, VariantKind
from svoi_pravila.domain.ids import TelegramUserId
from svoi_pravila.limits import load_limits_catalog
from svoi_pravila.limits.catalog import format_reset_hhmm

_PREVIEW_MAX = 256
_INLINE_TITLE_MAX = 64
_LIMIT_CACHE_SECONDS = 10
_PREPARED_PURPOSE = "prepared"
# Presentation order of rewrite results: softer, firmer, check (ADR-0016 §6). The use case
# and the port keep their own order; the Mini App has its own.
_INLINE_RESULT_ORDER: tuple[VariantKind, ...] = (
    VariantKind.SOFTER,
    VariantKind.FIRMER,
    VariantKind.CHECK,
)

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
        _ = bot
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
        logger.info("tone_signal", outcome=recorded.tone_outcome.value)

    return router


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
        UserQuotaExhausted,
        ServiceBudgetExhausted,
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
        | UserQuotaExhausted
        | ServiceBudgetExhausted
        | InlineComposeFailed
    ),
) -> None:
    user_id = query.from_user.id
    if not tg_deps.inline_queries.is_current_task(user_id):
        return
    limit_error = _limit_error(error)
    if limit_error is not None:
        telegram_ok = await _answer_limit_message(query, bot, tg_deps, error=limit_error)
    elif isinstance(error, (NotFound, AccessNotGranted)):
        telegram_ok = await _answer_empty(query, bot, tg_deps, onboard=True)
    else:
        button = await _compose_handoff_button(query, tg_deps)
        telegram_ok = await _send_inline_answer(
            bot,
            inline_query_id=query.id,
            results=[],
            cache_time=tg_deps.inline_cache_seconds,
            button=button,
        )
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
        | UserQuotaExhausted
        | ServiceBudgetExhausted
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
            button_text=tg_deps.strings.inline_button_need_support,
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
            button_text=tg_deps.strings.inline_button_why_no_variants,
        )
        outcome = (
            InlineAnsweredOutcome.TELEGRAM_API if not telegram_ok else InlineAnsweredOutcome.EMPTY
        )
    else:
        articles = _articles(
            tg_deps,
            result.scenario,
            _in_inline_order(result.variants),
        )
        button = await _compose_handoff_button(query, tg_deps)
        if not articles:
            telegram_ok = await _send_inline_answer(
                bot,
                inline_query_id=query.id,
                results=[],
                cache_time=tg_deps.inline_cache_seconds,
                button=button,
            )
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
                button=button,
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
        | UserQuotaExhausted
        | ServiceBudgetExhausted
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
        (Variant(text=variant.text, kind=variant.kind),),
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
) -> list[InlineQueryResultUnion]:
    articles: list[InlineQueryResultUnion] = []
    for variant in variants:
        if not variant.text:
            continue
        articles.append(
            InlineQueryResultArticle(
                id=encode_inline_result_ref(scenario, variant.kind),
                title=variant_label(tg_deps.strings, variant.kind),
                description=_preview(variant.text),
                input_message_content=InputTextMessageContent(
                    message_text=variant.text,
                    parse_mode=None,
                ),
            )
        )
    return articles


def _in_inline_order(variants: tuple[Variant, ...]) -> tuple[Variant, ...]:
    """Rewrite variants in the inline presentation order (stable for equal kinds)."""
    return tuple(sorted(variants, key=lambda variant: _INLINE_RESULT_ORDER.index(variant.kind)))


def _preview(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= _PREVIEW_MAX:
        return collapsed
    return collapsed[:_PREVIEW_MAX]


def _limit_error(
    error: (
        NotFound
        | AccessNotGranted
        | InlineQueryTooShort
        | IncomingTextTooLong
        | UserQuotaExhausted
        | ServiceBudgetExhausted
        | InlineComposeFailed
    ),
) -> UserQuotaExhausted | ServiceBudgetExhausted | None:
    cause = error.cause if isinstance(error, InlineComposeFailed) else error
    if isinstance(cause, (UserQuotaExhausted, ServiceBudgetExhausted)):
        return cause
    return None


async def _answer_limit_message(
    query: InlineQuery,
    bot: Bot,
    tg_deps: TelegramDeps,
    *,
    error: UserQuotaExhausted | ServiceBudgetExhausted,
) -> bool:
    """One personal article with shared catalog text; no prepared result."""
    reset = format_reset_hhmm(error.resets_at, str(tg_deps.display_timezone))
    catalog = load_limits_catalog()
    if isinstance(error, UserQuotaExhausted):
        text = catalog.user_quota_message(reset)
    else:
        text = catalog.service_budget_message(reset)
    article = InlineQueryResultArticle(
        id="limit",
        title=_preview(text)[:_INLINE_TITLE_MAX],
        description=_preview(text),
        input_message_content=InputTextMessageContent(
            message_text=text,
            parse_mode=None,
        ),
    )
    return await _send_inline_answer(
        bot,
        inline_query_id=query.id,
        results=[article],
        cache_time=min(tg_deps.inline_cache_seconds, _LIMIT_CACHE_SECONDS),
    )


async def _answer_empty(
    query: InlineQuery,
    bot: Bot,
    tg_deps: TelegramDeps,
    *,
    onboard: bool,
    button_text: str | None = None,
) -> bool:
    if button_text is None:
        button_text = (
            tg_deps.strings.inline_button_finish_setup
            if onboard
            else tg_deps.strings.inline_button_how_to
        )
    button = _web_app_results_button(tg_deps.miniapp_url, button_text)
    return await _send_inline_answer(
        bot,
        inline_query_id=query.id,
        results=[],
        cache_time=tg_deps.inline_cache_seconds,
        button=button,
    )


async def _compose_handoff_button(
    query: InlineQuery,
    tg_deps: TelegramDeps,
) -> InlineQueryResultsButton:
    """Issue a one-time handoff and build the web_app results button (never a contact)."""
    issued = await tg_deps.issue_compose_handoff.execute(
        IssueComposeHandoffCommand(TelegramUserId(query.from_user.id), query.query)
    )
    text = tg_deps.strings.inline_button_compose
    url = _compose_handoff_url(tg_deps.miniapp_url, issued.token)
    return _web_app_results_button(url, text)


def _compose_handoff_url(miniapp_url: str, token: str) -> str:
    base = miniapp_url.rstrip("/")
    query = urlencode({"view": "compose", "h": token})
    return f"{base}/?{query}"


def _web_app_results_button(url: str, text: str) -> InlineQueryResultsButton:
    return InlineQueryResultsButton(text=text, web_app=WebAppInfo(url=url))


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
