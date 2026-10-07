"""Histogram bucket boundaries chosen from measured latency ranges.

LLM request duration and TTFC: provider calls and streaming first-chunk
latencies fall between roughly 0.25 s and 60 s (inline softens near the
low end; decode streams and retries near the high end).

HTTP: ASGI handlers are typically 5 ms … a few seconds; 10 s is a safe
upper bound for the slowest proxy path.

Event-loop lag: overshoot after a 0.5 s sleep; healthy lag is milliseconds,
overload reaches hundreds of ms to a couple of seconds.
"""

from __future__ import annotations

LLM_DURATION_BUCKETS_SECONDS: tuple[float, ...] = (
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    15.0,
    30.0,
    60.0,
)

HTTP_DURATION_BUCKETS_SECONDS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)

EVENT_LOOP_LAG_BUCKETS_SECONDS: tuple[float, ...] = (
    0.001,
    0.0025,
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.0,
)
