"""HTTP-level adapter tests through the real GigaChat SDK client + respx."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from gigachat import GigaChat
from gigachat.models import AccessToken

from svoi_pravila.adapters.llm.gigachat.adapter import GigaChatTextGenerator
from svoi_pravila.adapters.llm.gigachat.client import create_gigachat_client
from svoi_pravila.adapters.llm.gigachat.validation import (
    MAX_TOKENS_ANALYSIS,
    MAX_TOKENS_DECODE,
    MAX_TOKENS_HELP_SAY,
    MAX_TOKENS_SOFTEN,
)
from svoi_pravila.application.errors import (
    GenerationRefusedByProvider,
    GenerationUnavailable,
    InvalidGenerationOutput,
)
from svoi_pravila.application.ports.generation import (
    AnalysisChunk,
    DecodeCompleted,
    DecodeRequest,
    HelpSayIntent,
    HelpSayRequest,
    SoftenRequest,
)
from svoi_pravila.domain.enums import RelationshipKind
from tests.factories import make_settings

_FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "gigachat"
_CHAT_URL_RE = r"https://api\.giga\.chat/.*/chat/completions"
_FIXTURE_TOKEN = "fixture-access-token"


def _load(name: str) -> dict[str, Any]:
    raw = json.loads((_FIXTURES / name).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"fixture {name} must be an object"
        raise TypeError(msg)
    return raw


def _client() -> GigaChat:
    settings = make_settings()
    client = create_gigachat_client(settings)
    # Skip live OAuth for fixture replay; value is synthetic and never sent to a real host.
    client._access_token = AccessToken(access_token=_FIXTURE_TOKEN, expires_at=0)
    return client


def _generator() -> GigaChatTextGenerator:
    return GigaChatTextGenerator(_client(), make_settings())


_AUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"


def _mock_oauth() -> respx.Route:
    return respx.post(_AUTH_URL).mock(
        return_value=httpx.Response(200, json={"tok": _FIXTURE_TOKEN, "exp": 4_102_444_800_000})
    )


def _mock_chat(fixture_name: str, *, stream_body: str | None = None) -> respx.Route:
    _mock_oauth()
    data = _load(fixture_name)
    status_raw = data["status"]
    if not isinstance(status_raw, int):
        msg = "fixture status must be int"
        raise TypeError(msg)
    status = status_raw
    if stream_body is not None:
        return respx.post(url__regex=_CHAT_URL_RE).mock(
            return_value=httpx.Response(
                status,
                content=stream_body.encode("utf-8"),
                headers={"content-type": "text/event-stream"},
            )
        )
    body = str(data["response_body"])
    return respx.post(url__regex=_CHAT_URL_RE).mock(
        return_value=httpx.Response(status, content=body.encode("utf-8"))
    )


def _request_body_text(request: httpx.Request) -> str:
    return request.content.decode("utf-8")


def _is_stream_request(request: httpx.Request) -> bool:
    body = _request_body_text(request)
    return '"stream": true' in body or '"stream":true' in body


def _analysis_sse(*parts: str) -> str:
    events: list[str] = []
    for index, text in enumerate(parts):
        chunk: dict[str, object] = {
            "messages": [{"content": [{"text": text}]}],
            "finish_reason": "stop" if index == len(parts) - 1 else None,
        }
        if index == len(parts) - 1:
            chunk["usage"] = {"input_tokens": 2, "output_tokens": 4}
        events.append(f"event: chunk\ndata: {json.dumps(chunk, ensure_ascii=False)}\n\n")
    return "".join(events)


def _mock_decode_stream_two_phase(
    *,
    analysis_parts: tuple[str, ...],
    parse_fixture: str = "decode_success.json",
    captured: list[dict[str, object]] | None = None,
) -> respx.Route:
    _mock_oauth()
    analysis_text = "".join(analysis_parts)
    stream_body = _analysis_sse(*analysis_parts)
    parse_data = _load(parse_fixture)
    parse_status = parse_data["status"]
    if not isinstance(parse_status, int):
        msg = "fixture status must be int"
        raise TypeError(msg)
    parse_body = str(parse_data["response_body"]).encode("utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(_request_body_text(request))
        if captured is not None:
            captured.append(payload)
        if _is_stream_request(request):
            assert payload["max_tokens"] == MAX_TOKENS_ANALYSIS
            return httpx.Response(
                parse_status,
                content=stream_body.encode("utf-8"),
                headers={"content-type": "text/event-stream"},
            )
        body = _request_body_text(request)
        assert analysis_text in body
        assert "analysis:" in body
        assert payload["max_tokens"] == MAX_TOKENS_DECODE
        return httpx.Response(parse_status, content=parse_body)

    return respx.post(url__regex=_CHAT_URL_RE).mock(side_effect=handler)


@pytest.mark.unit
@respx.mock
async def test_http_soften_success() -> None:
    route = _mock_chat("soften_success.json")
    result = await _generator().soften(
        SoftenRequest(
            draft="draft text",
            rules=(),
            relationship=RelationshipKind.FRIEND,
            deadline_seconds=5.0,
        )
    )
    assert 2 <= len(result.variants) <= 3
    body = json.loads(route.calls.last.request.content.decode("utf-8"))
    assert body["max_tokens"] == MAX_TOKENS_SOFTEN


@pytest.mark.unit
@respx.mock
async def test_http_help_say_success() -> None:
    route = _mock_chat("help_say_success.json")
    result = await _generator().help_say(
        HelpSayRequest(
            intent=HelpSayIntent.DECLINE,
            details="details",
            rules=(),
            relationship=RelationshipKind.WORK,
            deadline_seconds=5.0,
        )
    )
    assert 2 <= len(result.variants) <= 3
    body = json.loads(route.calls.last.request.content.decode("utf-8"))
    assert body["max_tokens"] == MAX_TOKENS_HELP_SAY


@pytest.mark.unit
@respx.mock
async def test_http_decode_success() -> None:
    _mock_decode_stream_two_phase(
        analysis_parts=("valid analysis",),
    )
    events = [
        event
        async for event in _generator().decode_stream(
            DecodeRequest(
                incoming="incoming",
                rules=(),
                relationship=RelationshipKind.PARTNER,
                deadline_seconds=5.0,
            )
        )
    ]
    completed = events[-1]
    assert isinstance(completed, DecodeCompleted)
    assert len(completed.result.variants) == 3


@pytest.mark.unit
@respx.mock
async def test_http_decode_stream_success() -> None:
    _mock_decode_stream_two_phase(
        analysis_parts=("vozmozhno partner ustal", " ot razgovora"),
    )
    events = [
        event
        async for event in _generator().decode_stream(
            DecodeRequest(
                incoming="incoming",
                rules=(),
                relationship=RelationshipKind.PARTNER,
                deadline_seconds=5.0,
            )
        )
    ]

    streamed = "".join(e.text for e in events if isinstance(e, AnalysisChunk))
    assert streamed == "vozmozhno partner ustal ot razgovora"
    completed = events[-1]
    assert isinstance(completed, DecodeCompleted)
    assert completed.analysis == streamed
    assert len(completed.result.variants) == 3
    assert completed.result.meta.prompt_version == "decode_analysis@v1+decode@v3"


@pytest.mark.unit
@respx.mock
async def test_http_decode_stream_phase_a_invalid_before_yield_retries() -> None:
    _mock_oauth()
    empty_stream = _analysis_sse("")
    good_stream = _analysis_sse("valid analysis after retry")
    parse_data = _load("decode_success.json")
    parse_status = parse_data["status"]
    if not isinstance(parse_status, int):
        msg = "fixture status must be int"
        raise TypeError(msg)
    parse_body = str(parse_data["response_body"]).encode("utf-8")
    stream_calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if _is_stream_request(request):
            stream_calls["count"] += 1
            body = empty_stream if stream_calls["count"] == 1 else good_stream
            return httpx.Response(
                parse_status,
                content=body.encode("utf-8"),
                headers={"content-type": "text/event-stream"},
            )
        assert "valid analysis after retry" in _request_body_text(request)
        return httpx.Response(parse_status, content=parse_body)

    respx.post(url__regex=_CHAT_URL_RE).mock(side_effect=handler)

    events = [
        event
        async for event in _generator().decode_stream(
            DecodeRequest(
                incoming="incoming",
                rules=(),
                relationship=RelationshipKind.PARTNER,
                deadline_seconds=5.0,
            )
        )
    ]
    assert stream_calls["count"] == 2
    assert isinstance(events[-1], DecodeCompleted)
    assert events[-1].analysis == "valid analysis after retry"


@pytest.mark.unit
@respx.mock
async def test_http_decode_stream_phase_a_invalid_after_yield_no_retry() -> None:
    _mock_decode_stream_two_phase(
        analysis_parts=("see https://example.test",),
    )
    with pytest.raises(InvalidGenerationOutput):
        async for _ in _generator().decode_stream(
            DecodeRequest(
                incoming="incoming",
                rules=(),
                relationship=RelationshipKind.PARTNER,
                deadline_seconds=5.0,
            )
        ):
            pass


@pytest.mark.unit
@respx.mock
async def test_http_blacklist() -> None:
    _mock_chat("blacklist.json")
    with pytest.raises(GenerationRefusedByProvider):
        await _generator().soften(
            SoftenRequest(
                draft="draft",
                rules=(),
                relationship=RelationshipKind.OTHER,
                deadline_seconds=5.0,
            )
        )


@pytest.mark.unit
@respx.mock
async def test_http_length() -> None:
    _mock_chat("length.json")
    with pytest.raises(InvalidGenerationOutput):
        await _generator().soften(
            SoftenRequest(
                draft="draft",
                rules=(),
                relationship=RelationshipKind.OTHER,
                deadline_seconds=5.0,
            )
        )


@pytest.mark.unit
@respx.mock
async def test_http_401() -> None:
    _mock_chat("http_401.json")
    with pytest.raises(GenerationUnavailable) as exc_info:
        await _generator().soften(
            SoftenRequest(
                draft="draft",
                rules=(),
                relationship=RelationshipKind.OTHER,
                deadline_seconds=5.0,
            )
        )
    assert exc_info.value.kind is not None


@pytest.mark.unit
@respx.mock
async def test_http_429() -> None:
    _mock_chat("http_429.json")
    with pytest.raises(GenerationUnavailable) as exc_info:
        await _generator().soften(
            SoftenRequest(
                draft="draft",
                rules=(),
                relationship=RelationshipKind.OTHER,
                deadline_seconds=5.0,
            )
        )
    assert exc_info.value.kind is not None


@pytest.mark.unit
@respx.mock
async def test_http_503() -> None:
    _mock_chat("http_503.json")
    with pytest.raises(GenerationUnavailable) as exc_info:
        await _generator().soften(
            SoftenRequest(
                draft="draft",
                rules=(),
                relationship=RelationshipKind.OTHER,
                deadline_seconds=5.0,
            )
        )
    assert exc_info.value.kind is not None


@pytest.mark.unit
@respx.mock
async def test_http_decode_stream_yields_analysis_before_stream_ends() -> None:
    analysis_first = "razbor vhodyashchego soobshcheniya bez utverzhdeniy. "
    analysis_second = "vozmozhno partner ustal."
    chunk1 = {
        "messages": [{"content": [{"text": analysis_first}]}],
        "finish_reason": None,
    }
    chunk2 = {
        "messages": [{"content": [{"text": analysis_second}]}],
        "finish_reason": "stop",
        "usage": {"input_tokens": 2, "output_tokens": 4},
    }
    first = f"event: chunk\ndata: {json.dumps(chunk1, ensure_ascii=False)}\n\n".encode()
    second = f"event: chunk\ndata: {json.dumps(chunk2, ensure_ascii=False)}\n\n".encode()
    parse_data = _load("decode_success.json")
    parse_status = parse_data["status"]
    if not isinstance(parse_status, int):
        msg = "fixture status must be int"
        raise TypeError(msg)
    parse_body = str(parse_data["response_body"]).encode("utf-8")
    analysis_text = analysis_first + analysis_second
    state = {"stream_finished": False}
    second_chunk_gate = asyncio.Event()

    class DelayedStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield first
            await second_chunk_gate.wait()
            yield second
            state["stream_finished"] = True

    _mock_oauth()

    def handler(request: httpx.Request) -> httpx.Response:
        if _is_stream_request(request):
            return httpx.Response(
                parse_status,
                stream=DelayedStream(),
                headers={"content-type": "text/event-stream"},
            )
        assert analysis_text in _request_body_text(request)
        return httpx.Response(parse_status, content=parse_body)

    respx.post(url__regex=_CHAT_URL_RE).mock(side_effect=handler)

    saw_analysis_before_end = False
    async for event in _generator().decode_stream(
        DecodeRequest(
            incoming="incoming",
            rules=(),
            relationship=RelationshipKind.PARTNER,
            deadline_seconds=5.0,
        )
    ):
        if isinstance(event, AnalysisChunk) and not state["stream_finished"]:
            saw_analysis_before_end = True
            second_chunk_gate.set()
    assert saw_analysis_before_end
