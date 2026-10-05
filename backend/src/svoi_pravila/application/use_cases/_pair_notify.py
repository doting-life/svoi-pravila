"""Failure-isolated PairNotifier calls (never roll back domain changes)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable


async def notify_after_commit(call: Callable[[], Awaitable[None]]) -> None:
    """Run a notifier call; swallow failures so domain commit stays committed.

    Delivery failures are logged C0 inside the PairNotifier adapter.
    """
    try:
        await call()
    except Exception:
        return
