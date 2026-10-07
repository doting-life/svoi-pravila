"""Map redis/OS failures to typed ``CacheUnavailable``."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from svoi_pravila.adapters.cache.errors import CacheErrorKind, CacheUnavailable
from svoi_pravila.observability.metrics import families
from svoi_pravila.observability.metrics.labels import cache_error_kind_label


async def map_redis[T](call: Callable[[], Awaitable[T]]) -> T:
    """Await ``call``; translate Valkey failures to ``CacheUnavailable``."""
    try:
        return await call()
    except RedisTimeoutError as exc:
        _record_cache_error(CacheErrorKind.TIMEOUT)
        raise CacheUnavailable(CacheErrorKind.TIMEOUT) from exc
    except RedisConnectionError as exc:
        _record_cache_error(CacheErrorKind.NETWORK)
        raise CacheUnavailable(CacheErrorKind.NETWORK) from exc
    except (OSError, RedisError) as exc:
        _record_cache_error(CacheErrorKind.SERVER)
        raise CacheUnavailable(CacheErrorKind.SERVER) from exc


def _record_cache_error(kind: CacheErrorKind) -> None:
    families.CACHE_ERRORS.labels(kind=cache_error_kind_label(kind)).inc()
