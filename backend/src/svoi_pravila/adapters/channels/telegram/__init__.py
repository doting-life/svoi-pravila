"""Telegram channel adapter."""

from svoi_pravila.adapters.channels.telegram.factory import build_telegram_lifecycle
from svoi_pravila.adapters.channels.telegram.lifecycle import TelegramLifecycle

__all__ = ["TelegramLifecycle", "build_telegram_lifecycle"]
