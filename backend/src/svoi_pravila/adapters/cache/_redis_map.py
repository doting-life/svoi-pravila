"""Map redis/OS failures to typed ``CacheUnavailable``."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from svoi_pravila.adapters.cache.errors import CacheErrorKind, CacheUnavailable


async def map_redis[T](call: Callable[[], Awaitable[T]]) -> T:
    """Await ``call``; translate Valkey failures to ``CacheUnavailable``."""
    try:
        return await call()
    except RedisTimeoutError as exc:
        raise CacheUnavailable(CacheErrorKind.TIMEOUT) from exc
    except RedisConnectionError as exc:
        raise CacheUnavailable(CacheErrorKind.NETWORK) from exc
    except (OSError, RedisError) as exc:
        raise CacheUnavailable(CacheErrorKind.SERVER) from exc
