"""In-memory TelegramPublicUsername for tests."""

from __future__ import annotations

from dataclasses import dataclass, field

from svoi_pravila.domain.ids import TelegramUserId


@dataclass
class FakeTelegramPublicUsername:
    """Map telegram ids to usernames; missing → ``None``."""

    by_id: dict[int, str | None] = field(default_factory=dict)
    fail_ids: set[int] = field(default_factory=set)
    calls: list[int] = field(default_factory=list)

    async def username_for(self, telegram_user_id: TelegramUserId) -> str | None:
        self.calls.append(telegram_user_id.value)
        if telegram_user_id.value in self.fail_ids:
            return None
        return self.by_id.get(telegram_user_id.value)
