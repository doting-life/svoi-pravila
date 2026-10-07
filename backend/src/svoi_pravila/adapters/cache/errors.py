"""Re-export typed cache failures from the application layer."""

from __future__ import annotations

from svoi_pravila.application.errors import CacheErrorKind, CacheUnavailable

__all__ = ["CacheErrorKind", "CacheUnavailable"]
