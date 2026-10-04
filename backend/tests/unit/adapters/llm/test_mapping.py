"""Unit tests for GigaChat finish-reason and exception mapping."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from gigachat import AuthenticationError, ForbiddenError, ResponseError
from gigachat.models.chat_completions import ChatUsage

from svoi_pravila.adapters.llm.gigachat.attempt_policy import (
    AttemptState,
    add_usage,
    token_usage_from_state,
)
from svoi_pravila.adapters.llm.gigachat.mapping import (
    map_provider_exception,
    raise_for_finish_reason,
    usage_tokens,
)
from svoi_pravila.application.errors import (
    GenerationRefusedByProvider,
    InvalidGenerationOutput,
    InvalidOutputReason,
    UnavailableKind,
)
from svoi_pravila.application.ports.generation import TokenUsage

_ZERO = TokenUsage()


@pytest.mark.unit
def test_raise_for_finish_reason_length() -> None:
    with pytest.raises(InvalidGenerationOutput) as exc_info:
        raise_for_finish_reason("length", usage=_ZERO, attempts=1, model="m", prompt_version="p")
    assert exc_info.value.reasons == (InvalidOutputReason.LENGTH,)
    assert exc_info.value.usage == _ZERO
    assert exc_info.value.attempts == 1


@pytest.mark.unit
def test_raise_for_finish_reason_blacklist() -> None:
    usage = TokenUsage(input=2, output=1)
    with pytest.raises(GenerationRefusedByProvider) as exc_info:
        raise_for_finish_reason("blacklist", usage=usage, attempts=1, model="m", prompt_version="p")
    assert exc_info.value.usage == usage
    assert exc_info.value.attempts == 1


@pytest.mark.unit
def test_raise_for_finish_reason_stop_is_noop() -> None:
    raise_for_finish_reason("stop", usage=_ZERO, attempts=0, model="m", prompt_version="p")
    raise_for_finish_reason(None, usage=_ZERO, attempts=0, model="m", prompt_version="p")


@pytest.mark.unit
def test_map_forbidden_logs_auth_failed(
    capture_log_events: Callable[[], list[dict[str, Any]]],
) -> None:
    mapped = map_provider_exception(
        ForbiddenError("https://example.test", 403, b"no", None),
        usage=_ZERO,
        attempts=0,
        model="m",
        prompt_version="p",
    )
    assert mapped.kind is UnavailableKind.AUTH
    assert any(e.get("event") == "generation_auth_failed" for e in capture_log_events())


@pytest.mark.unit
def test_map_response_error_status_codes() -> None:
    expected = {
        401: UnavailableKind.AUTH,
        403: UnavailableKind.AUTH,
        429: UnavailableKind.RATE_LIMITED,
        500: UnavailableKind.SERVER,
    }
    for status, kind in expected.items():
        mapped = map_provider_exception(
            ResponseError("https://example.test", status, b"x", None),
            usage=TokenUsage(input=1),
            attempts=2,
            model="m",
            prompt_version="p",
        )
        assert mapped.kind is kind
        assert mapped.usage.input == 1
        assert mapped.attempts == 2
    with pytest.raises(ResponseError) as unexpected:
        map_provider_exception(
            ResponseError("https://example.test", 404, b"missing", None),
            usage=_ZERO,
            attempts=0,
            model="m",
            prompt_version="p",
        )
    assert unexpected.value.status_code == 404
    with pytest.raises(ResponseError) as teapot:
        map_provider_exception(
            ResponseError("https://example.test", 418, b"teapot", None),
            usage=_ZERO,
            attempts=0,
            model="m",
            prompt_version="p",
        )
    assert teapot.value.status_code == 418


@pytest.mark.unit
def test_map_httpx_connect_error() -> None:
    mapped = map_provider_exception(
        httpx.ConnectError("boom"),
        usage=_ZERO,
        attempts=0,
        model="m",
        prompt_version="p",
    )
    assert mapped.kind is UnavailableKind.NETWORK


@pytest.mark.unit
def test_map_authentication_error() -> None:
    mapped = map_provider_exception(
        AuthenticationError("https://example.test", 401, b"no", None),
        usage=_ZERO,
        attempts=0,
        model="m",
        prompt_version="p",
    )
    assert mapped.kind is UnavailableKind.AUTH


@pytest.mark.unit
def test_map_unknown_exception_propagates() -> None:
    with pytest.raises(RuntimeError, match="mystery"):
        map_provider_exception(
            RuntimeError("mystery"),
            usage=_ZERO,
            attempts=0,
            model="m",
            prompt_version="p",
        )


@pytest.mark.unit
def test_usage_tokens_typed() -> None:
    assert usage_tokens(None) == (0, 0)
    assert usage_tokens(ChatUsage(input_tokens=2, output_tokens=4)) == (2, 4)
    assert usage_tokens(ChatUsage()) == (0, 0)


@pytest.mark.unit
def test_add_usage_precached_billable_derived() -> None:
    state = AttemptState(started=0.0, model="m", prompt_version="p")
    add_usage(
        state,
        ChatUsage(
            input_tokens=10,
            output_tokens=4,
            total_tokens=999,
            input_tokens_details={"prompt_tokens": 10, "cached_tokens": 7},
        ),
    )
    usage = token_usage_from_state(state)
    assert usage.input == 10
    assert usage.output == 4
    assert usage.precached == 7
    assert usage.billable == 14
