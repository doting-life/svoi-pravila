"""Telegram sleeper seam."""

from __future__ import annotations

import pytest

from svoi_pravila.adapters.channels.telegram.sleeper import AsyncioSleeper


@pytest.mark.unit
async def test_asyncio_sleeper_zero_delay() -> None:
    await AsyncioSleeper().sleep(0)
