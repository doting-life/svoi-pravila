"""Shared helpers for quota / budget gating before LLM calls."""

from __future__ import annotations

from svoi_pravila.application.errors import (
    CacheErrorKind,
    CacheUnavailable,
    GenerationUnavailable,
    UnavailableKind,
)
from svoi_pravila.application.ports.generation import TokenUsage

_CACHE_TO_UNAVAILABLE = {
    CacheErrorKind.NETWORK: UnavailableKind.NETWORK,
    CacheErrorKind.TIMEOUT: UnavailableKind.TIMEOUT,
    CacheErrorKind.SERVER: UnavailableKind.SERVER,
}


def generation_unavailable_from_cache(exc: CacheUnavailable) -> GenerationUnavailable:
    """Fail closed: Valkey down surfaces as generation unavailable (no LLM call)."""
    return GenerationUnavailable(
        _CACHE_TO_UNAVAILABLE[exc.kind],
        usage=TokenUsage(),
        attempts=1,
        model="n/a",
        prompt_version="n/a",
    )
