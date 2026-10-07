"""Dependencies injected into the Telegram dispatcher workflow data."""

from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo

from svoi_pravila.adapters.channels.telegram.bot_username import BotUsernameCache
from svoi_pravila.adapters.channels.telegram.inline_scheduler import InlineQueryCoordinator
from svoi_pravila.adapters.channels.telegram.localization import TelegramStrings
from svoi_pravila.application.ports.clock import Clock
from svoi_pravila.application.ports.monotonic import MonotonicClock
from svoi_pravila.application.ports.prepared_results import PreparedResults
from svoi_pravila.application.ports.pseudonymizer import Pseudonymizer
from svoi_pravila.application.ports.rate_limiter import RateLimiter
from svoi_pravila.application.ports.update_deduplicator import UpdateDeduplicator
from svoi_pravila.application.ports.welcome_throttle import WelcomeThrottle
from svoi_pravila.application.use_cases.get_user_by_telegram_id import GetUserByTelegramId
from svoi_pravila.application.use_cases.inline_compose import InlineCompose
from svoi_pravila.application.use_cases.record_inline_choice import RecordInlineChoice


@dataclass(frozen=True, slots=True)
class TelegramDeps:
    """Application collaborators available to Telegram middlewares and handlers."""

    strings: TelegramStrings
    get_user_by_telegram_id: GetUserByTelegramId
    inline_compose: InlineCompose
    record_inline_choice: RecordInlineChoice
    prepared_results: PreparedResults
    inline_queries: InlineQueryCoordinator
    welcome_throttle: WelcomeThrottle
    bot_username: BotUsernameCache
    clock: Clock
    display_timezone: ZoneInfo
    deduplicator: UpdateDeduplicator
    rate_limiter: RateLimiter
    pseudonymizer: Pseudonymizer
    monotonic: MonotonicClock
    inline_cache_seconds: int
    miniapp_url: str | None
