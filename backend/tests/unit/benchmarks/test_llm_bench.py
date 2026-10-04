"""Unit tests for benchmark loading, aggregation, cost, and recording sanitization."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import httpx
import pytest
import respx
from gigachat import AuthenticationError, GigaChat, ServerError
from gigachat.models import AccessToken
from tests.factories import make_settings
from tests.fakes.generation import FakeTextGenerator

from svoi_pravila.adapters.llm.gigachat.client import (
    close_gigachat_client,
    create_gigachat_client,
)
from svoi_pravila.adapters.llm.gigachat.prepared import prepare_soften
from svoi_pravila.adapters.llm.gigachat.validation import MAX_TOKENS_SOFTEN
from svoi_pravila.application.errors import (
    GenerationRefusedByProvider,
    GenerationUnavailable,
    InvalidGenerationOutput,
    InvalidOutputReason,
    UnavailableKind,
)
from svoi_pravila.application.ports.generation import (
    DecodeCompleted,
    DecodeEvent,
    DecodeRequest,
    HelpSayIntent,
    HelpSayRequest,
    SoftenRequest,
    SoftenResult,
    TokenUsage,
)
from svoi_pravila.benchmarks.cases import (
    BenchCase,
    RunOperation,
    filter_cases,
    load_cases,
    parse_case,
    with_deadline,
)
from svoi_pravila.benchmarks.estimate import (
    chars_to_tokens,
    estimate_case_tokens,
    estimate_prepared_tokens,
    plan_calls,
)
from svoi_pravila.benchmarks.llm import _parse_ops, async_main, main
from svoi_pravila.benchmarks.out_writer import OutWriter
from svoi_pravila.benchmarks.recording import (
    RateLimitCapture,
    RecordedExchange,
    RecordingConfig,
    gigachat_http_clients,
    install_rate_limit_capture,
    install_recording,
    sanitize_body,
    sanitize_headers,
    write_fixture,
)
from svoi_pravila.benchmarks.report import (
    aggregate_row,
    aggregate_standard_row,
    default_models,
    format_reasons_line,
    format_report,
    percentile,
    phase_count,
    price_for,
    standard_report_header,
)
from svoi_pravila.benchmarks.runner import (
    BenchmarkParams,
    BenchmarkRuntime,
    CallRecord,
    CaseRunOptions,
    RateLimitedError,
    SpendTracker,
    TokenBudgetExceededError,
    run_benchmark,
    run_case,
    warmup,
)
from svoi_pravila.config import Settings
from svoi_pravila.domain.enums import RelationshipKind

_CHAT_URL_RE = r"https://api\.giga\.chat/.*/chat/completions"
_FIXTURE_TOKEN = "fixture-access-token"


class _StickyRateLimitCapture(RateLimitCapture):
    """Keep C0 headers across ``run_case`` clear() for fake (non-HTTP) generators."""

    def clear(self) -> None:
        return


@pytest.fixture(autouse=True)
def _noop_rate_limit_capture_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub clients lack real httpx clients; skip install unless a test restores it."""

    async def _uninstall() -> None:
        return None

    def _install(_client: object, _capture: RateLimitCapture) -> object:
        return _uninstall

    monkeypatch.setattr(
        "svoi_pravila.benchmarks.llm.install_rate_limit_capture",
        _install,
    )


def _clear_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "https_proxy",
        "http_proxy",
        "ALL_PROXY",
        "all_proxy",
    ):
        monkeypatch.delenv(key, raising=False)


def _set_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")


def _apply_proxy_mode(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    if mode == "proxy":
        _set_proxy_env(monkeypatch)
    else:
        _clear_proxy_env(monkeypatch)


def _real_gigachat(monkeypatch: pytest.MonkeyPatch, *, proxy_mode: str) -> GigaChat:
    _apply_proxy_mode(monkeypatch, proxy_mode)
    client = create_gigachat_client(make_settings())
    client._access_token = AccessToken(
        access_token=_FIXTURE_TOKEN,
        expires_at=4_102_444_800_000,
    )
    return client


def _assert_one_fixture_contains(tmp_path: Path, needle: str) -> None:
    fixtures = sorted(tmp_path.glob("*.json"))
    assert len(fixtures) == 1
    assert needle in fixtures[0].read_text(encoding="utf-8")


def _cli_args(**overrides: object) -> argparse.Namespace:
    defaults: dict[str, object] = {
        "data": "unused.jsonl",
        "list_models": False,
        "models": None,
        "ops": None,
        "out": None,
        "record_fixtures": None,
        "repeat": 1,
        "deadline": 5.0,
        "show_outputs": False,
        "dry_run": False,
        "max_tokens": 1_000_000,
        "smoke": False,
        "cases": None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _opts(
    operation: RunOperation,
    *,
    deadline: float = 5.0,
    show_outputs: bool = False,
    rate_limits: RateLimitCapture | None = None,
) -> CaseRunOptions:
    return CaseRunOptions(
        operation=operation,
        deadline=deadline,
        show_outputs=show_outputs,
        rate_limits=rate_limits,
    )


@pytest.mark.unit
def test_percentile_and_price_for() -> None:
    assert percentile([], 50) == 0.0
    assert percentile([10.0, 20.0, 30.0], 50) == 20.0
    assert price_for("GigaChat-2-Max") == 0.65
    assert price_for("GigaChat-2-Pro") == 0.5
    assert price_for("GigaChat-2") == 0.065
    assert price_for("unknown-model") == 0.065


@pytest.mark.unit
def test_parse_case_and_load_v2(tmp_path: Path) -> None:
    path = Path("benchmarks/data/bench_v2.jsonl")
    cases = load_cases(path)
    assert len(cases) >= 60
    ops = {c.operation for c in cases}
    assert ops == {"soften", "help_say", "decode"}
    assert all(c.expected_safety == "ok" for c in cases)
    rels = set()
    for case in cases:
        if case.soften:
            rels.add(case.soften.relationship)
        if case.help_say:
            rels.add(case.help_say.relationship)
        if case.decode:
            rels.add(case.decode.relationship)
    assert rels == set(RelationshipKind)
    with pytest.raises(ValueError, match="unknown operation"):
        parse_case(
            {"id": "x", "operation": "nope", "relationship": "friend", "expected_safety": "ok"}
        )
    with pytest.raises(ValueError, match="expected_safety"):
        parse_case({"id": "x", "operation": "soften", "relationship": "friend", "draft": "x"})
    empty = tmp_path / "empty.jsonl"
    empty.write_text("\n", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        load_cases(empty)


@pytest.mark.unit
def test_aggregate_separates_quality_and_availability() -> None:
    records = [
        CallRecord("ok", 100, "ok", 1, (), None, 10, 5, actual_safety="ok"),
        CallRecord("ok", 200, "ok", 2, (), None, 10, 5, actual_safety="crisis"),
        CallRecord("invalid_output", 50, "ok", 2, ("empty_message",), None, 0, 0),
        CallRecord("refused", 40, "ok", 0, (), None, 0, 0),
        CallRecord("unavailable", 30, "ok", 0, (), "timeout", 0, 0),
    ]
    row, reasons = aggregate_standard_row(records, operation="soften", model="GigaChat-2")
    assert "soften" in row
    assert "ok:1/crisis:1/refuse_manipulation:0" in row
    assert "50" in row
    assert "33" in row or "timeout:20" in row
    assert reasons == {"empty_message": 1}
    header = standard_report_header()
    assert "ok-p50" in header
    assert "false-non-ok%" in header
    assert "safety ok/crisis/refuse" in header


@pytest.mark.unit
def test_format_report_incomplete() -> None:
    body = format_report(["| row |"], incomplete=True)
    assert "INCOMPLETE" in body
    assert "rate_limited" in body


@pytest.mark.unit
def test_parse_ops_default_and_subset() -> None:
    assert _parse_ops(None) == ("soften", "help_say", "decode_stream")
    assert _parse_ops(["decode_stream", "soften"]) == ("decode_stream", "soften")
    assert _parse_ops(["help_say", "decode_stream", "help_say"]) == (
        "help_say",
        "decode_stream",
    )
    with pytest.raises(SystemExit):
        _parse_ops(["nope"])
    with pytest.raises(SystemExit):
        _parse_ops(["decode"])


@pytest.mark.unit
def test_default_models_from_settings() -> None:
    settings = make_settings(
        gigachat_model_soften="GigaChat-2-Pro",
        gigachat_model_help_say="GigaChat-2-Pro",
        gigachat_model_decode="GigaChat-2-Max",
    )
    assert default_models(settings) == ["GigaChat-2-Pro", "GigaChat-2-Max"]


@pytest.mark.unit
async def test_run_case_ok_and_invalid_and_outputs_off() -> None:
    fake = FakeTextGenerator()
    soften = BenchCase(
        id="s1",
        operation="soften",
        expected_safety="ok",
        soften=SoftenRequest(
            draft="черновик",
            rules=(),
            relationship=RelationshipKind.FRIEND,
            deadline_seconds=1.0,
        ),
    )
    rec = await run_case(fake, soften, _opts("soften", show_outputs=False))
    assert rec.outcome == "ok"
    assert rec.actual_safety == "ok"
    assert rec.output_line is None
    rec_shown = await run_case(fake, soften, _opts("soften", show_outputs=True))
    assert rec_shown.output_line is not None

    class Boom(FakeTextGenerator):
        async def soften(self, request: SoftenRequest) -> SoftenResult:
            raise InvalidGenerationOutput(
                (InvalidOutputReason.VARIANT_COUNT,),
                usage=TokenUsage(),
                attempts=1,
            )

    rec_bad = await run_case(Boom(), soften, _opts("soften"))
    assert rec_bad.outcome == "invalid_output"
    assert rec_bad.reasons == ("variant_count",)


@pytest.mark.unit
async def test_run_case_refused_and_unavailable() -> None:
    soften = BenchCase(
        id="s1",
        operation="soften",
        expected_safety="ok",
        soften=SoftenRequest(
            draft="черновик",
            rules=(),
            relationship=RelationshipKind.FRIEND,
            deadline_seconds=1.0,
        ),
    )

    class Refuse(FakeTextGenerator):
        async def soften(self, request: SoftenRequest) -> SoftenResult:
            raise GenerationRefusedByProvider(usage=TokenUsage(), attempts=1)

    class Down(FakeTextGenerator):
        async def soften(self, request: SoftenRequest) -> SoftenResult:
            raise GenerationUnavailable(
                UnavailableKind.TIMEOUT,
                usage=TokenUsage(),
                attempts=1,
            )

    refused = await run_case(Refuse(), soften, _opts("soften"))
    assert refused.outcome == "refused"
    down = await run_case(Down(), soften, _opts("soften"))
    assert down.outcome == "unavailable"
    assert down.unavailable_kind == "timeout"


@pytest.mark.unit
async def test_run_case_rate_limited_stops() -> None:
    soften = BenchCase(
        id="s1",
        operation="soften",
        expected_safety="ok",
        soften=SoftenRequest(
            draft="черновик",
            rules=(),
            relationship=RelationshipKind.FRIEND,
            deadline_seconds=1.0,
        ),
    )

    class Limited(FakeTextGenerator):
        async def soften(self, request: SoftenRequest) -> SoftenResult:
            raise GenerationUnavailable(
                UnavailableKind.RATE_LIMITED,
                usage=TokenUsage(),
                attempts=1,
            )

    capture = _StickyRateLimitCapture()
    capture.http_status = 429
    capture.rate_limit_headers = (
        ("retry-after", "1"),
        ("x-ratelimit-remaining", "0"),
    )
    with pytest.raises(RateLimitedError) as exc_info:
        await run_case(
            Limited(),
            soften,
            _opts("soften", rate_limits=capture),
        )
    assert exc_info.value.record.unavailable_kind == "rate_limited"
    assert exc_info.value.http_status == 429
    assert exc_info.value.rate_limit_headers == (
        ("retry-after", "1"),
        ("x-ratelimit-remaining", "0"),
    )


@pytest.mark.unit
def test_cli_show_outputs_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    async def fake_async(args: argparse.Namespace) -> int:
        seen["show"] = args.show_outputs
        seen["data"] = args.data
        seen["ops"] = args.ops
        seen["out"] = args.out
        return 0

    monkeypatch.setattr("svoi_pravila.benchmarks.llm.async_main", fake_async)
    monkeypatch.setattr("sys.argv", ["svoi-pravila-bench-llm"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0
    assert seen["show"] is False
    assert seen["ops"] is None
    assert seen["out"] is None
    assert "bench_v2.jsonl" in str(seen["data"])


@pytest.mark.unit
def test_fixture_sanitization(tmp_path: Path) -> None:
    headers = sanitize_headers({"Authorization": "Bearer super-secret-token", "Accept": "*/*"})
    assert headers["Authorization"] == "<redacted>"
    assert "secret" not in sanitize_body('{"access_token": abcdefghijklmnop}')
    path = tmp_path / "fx.json"
    write_fixture(
        path,
        RecordedExchange(
            method="POST",
            url="https://api.giga.chat/v2/chat/completions",
            status=200,
            request_body='{"tok": "abcdefghijklmnop"}',
            response_body="ok",
            headers={"Authorization": "Bearer abcdefghijklmnop"},
        ),
    )
    raw = path.read_text(encoding="utf-8")
    assert "abcdefghijklmnop" not in raw
    assert "<redacted>" in raw


@pytest.mark.unit
@respx.mock
@pytest.mark.parametrize("proxy_mode", ["proxy", "no_proxy"])
async def test_recording_sse_streams_to_caller(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    proxy_mode: str,
) -> None:
    client = _real_gigachat(monkeypatch, proxy_mode=proxy_mode)
    uninstall = install_recording(
        client,
        RecordingConfig(out_dir=tmp_path, name_prefix="tee"),
    )
    aclient, _auth = gigachat_http_clients(client)
    first_seen: list[bytes] = []
    second_chunk_gate = asyncio.Event()
    stream_state = {"finished": False}

    class DelayedStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"event: chunk\ndata: one\n\n"
            await second_chunk_gate.wait()
            yield b"event: chunk\ndata: two\n\n"
            stream_state["finished"] = True

    respx.post(url__regex=_CHAT_URL_RE).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=DelayedStream(),
        )
    )
    try:
        async with aclient.stream(
            "POST",
            "https://api.giga.chat/v2/chat/completions",
            content=b"{}",
        ) as response:
            async for chunk in response.aiter_bytes():
                first_seen.append(chunk)
                if len(first_seen) == 1:
                    assert stream_state["finished"] is False
                    second_chunk_gate.set()
        assert first_seen[0] == b"event: chunk\ndata: one\n\n"
        assert stream_state["finished"] is True
    finally:
        await uninstall()
        await close_gigachat_client(client)
    _assert_one_fixture_contains(tmp_path, "data: one")


@pytest.mark.unit
async def test_async_main_list_models(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class _Client:
        async def aget_models(self) -> object:
            return SimpleNamespace(data=[SimpleNamespace(id_="GigaChat-2-Pro")])

    async def _close(_client: object) -> None:
        return None

    def _client_factory(_settings: object) -> _Client:
        return _Client()

    monkeypatch.setattr("svoi_pravila.benchmarks.llm.Settings", make_settings)
    monkeypatch.setattr(
        "svoi_pravila.benchmarks.llm.create_gigachat_client",
        _client_factory,
    )
    monkeypatch.setattr("svoi_pravila.benchmarks.llm.close_gigachat_client", _close)
    monkeypatch.setattr(
        "svoi_pravila.benchmarks.llm.load_cases",
        lambda _path: [
            BenchCase(
                id="s1",
                operation="soften",
                expected_safety="ok",
                soften=SoftenRequest(
                    draft="черновик",
                    rules=(),
                    relationship=RelationshipKind.FRIEND,
                    deadline_seconds=1.0,
                ),
            )
        ],
    )
    code = await async_main(_cli_args(list_models=True))
    assert code == 0
    captured = capsys.readouterr().out
    assert "GigaChat-2-Pro" in captured


@pytest.mark.unit
def test_parse_case_rejects_invalid_shapes() -> None:
    with pytest.raises(TypeError, match="JSON object"):
        parse_case([])
    with pytest.raises(ValueError, match="case id"):
        parse_case(
            {"id": "", "operation": "soften", "relationship": "friend", "expected_safety": "ok"}
        )
    with pytest.raises(TypeError, match="rules must be a list"):
        parse_case(
            {
                "id": "s",
                "operation": "soften",
                "relationship": "friend",
                "draft": "текст",
                "expected_safety": "ok",
                "rules": "nope",
            }
        )
    with pytest.raises(TypeError, match="each rule must be an object"):
        parse_case(
            {
                "id": "s",
                "operation": "soften",
                "relationship": "friend",
                "draft": "текст",
                "expected_safety": "ok",
                "rules": ["x"],
            }
        )


@pytest.mark.unit
def test_parse_case_null_rules_and_with_deadline_missing_decode() -> None:
    case = parse_case(
        {
            "id": "s-null-rules",
            "operation": "soften",
            "relationship": "friend",
            "draft": "текст",
            "expected_safety": "ok",
            "rules": None,
        }
    )
    assert case.soften is not None
    assert case.soften.rules == ()
    timed = with_deadline(case, 9.0)
    assert timed.soften is not None
    assert timed.soften.deadline_seconds == 9.0
    broken = BenchCase(id="d-missing", operation="decode", expected_safety="ok")
    with pytest.raises(ValueError, match="decode case missing request"):
        with_deadline(broken, 1.0)


@pytest.mark.unit
def test_load_cases_rejects_bad_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid case"):
        load_cases(path)


@pytest.mark.unit
async def test_run_case_help_say_and_decode_stream() -> None:
    fake = FakeTextGenerator(stream_chunks=("анализ ",))
    help_case = BenchCase(
        id="h1",
        operation="help_say",
        expected_safety="ok",
        help_say=HelpSayRequest(
            intent=HelpSayIntent.DECLINE,
            details="не могу прийти",
            rules=(),
            relationship=RelationshipKind.WORK,
            deadline_seconds=1.0,
        ),
    )
    help_rec = await run_case(fake, help_case, _opts("help_say", show_outputs=True))
    assert help_rec.outcome == "ok"
    assert help_rec.output_line is not None

    decode_case = BenchCase(
        id="d1",
        operation="decode",
        expected_safety="ok",
        decode=DecodeRequest(
            incoming="входящее",
            rules=(),
            relationship=RelationshipKind.PARTNER,
            deadline_seconds=1.0,
        ),
    )
    stream_rec = await run_case(fake, decode_case, _opts("decode_stream", show_outputs=True))
    assert stream_rec.outcome == "ok"
    assert stream_rec.ttfc_ms is not None
    assert stream_rec.phase_a_ms is not None
    assert stream_rec.phase_b_ms is not None
    assert len(fake.decode_stream_calls) == 1


@pytest.mark.unit
async def test_aggregate_decode_stream_row() -> None:
    records = [
        CallRecord(
            "ok",
            300,
            "ok",
            1,
            (),
            None,
            10,
            5,
            ttfc_ms=50,
            phase_a_ms=120,
            phase_b_ms=180,
            actual_safety="ok",
        ),
    ]
    row, _ = aggregate_row(records, operation="decode_stream", model="GigaChat-2")
    assert "decode_stream" in row
    assert "ttfc" not in row
    assert "50" in row
    assert "120" in row
    assert "180" in row


@pytest.mark.unit
async def test_async_main_runs_models(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    class _Client:
        async def aget_models(self) -> object:
            return SimpleNamespace(data=[SimpleNamespace(id_="GigaChat-2")])

    async def _close(_client: object) -> None:
        return None

    def _client_factory(_settings: object) -> _Client:
        return _Client()

    def _gen_factory(_client: object, _settings: object) -> FakeTextGenerator:
        return FakeTextGenerator(stream_chunks=("анализ ",))

    async def _uninstall() -> None:
        return None

    def _install(_client: object, _config: RecordingConfig) -> object:
        return _uninstall

    cases = [
        BenchCase(
            id="s1",
            operation="soften",
            expected_safety="ok",
            soften=SoftenRequest(
                draft="черновик",
                rules=(),
                relationship=RelationshipKind.FRIEND,
                deadline_seconds=1.0,
            ),
        ),
        BenchCase(
            id="h1",
            operation="help_say",
            expected_safety="ok",
            help_say=HelpSayRequest(
                intent=HelpSayIntent.DECLINE,
                details="детали",
                rules=(),
                relationship=RelationshipKind.WORK,
                deadline_seconds=1.0,
            ),
        ),
        BenchCase(
            id="d1",
            operation="decode",
            expected_safety="ok",
            decode=DecodeRequest(
                incoming="входящее",
                rules=(),
                relationship=RelationshipKind.FAMILY,
                deadline_seconds=1.0,
            ),
        ),
    ]
    monkeypatch.setattr("svoi_pravila.benchmarks.llm.Settings", make_settings)
    monkeypatch.setattr("svoi_pravila.benchmarks.llm.create_gigachat_client", _client_factory)
    monkeypatch.setattr("svoi_pravila.benchmarks.llm.close_gigachat_client", _close)
    monkeypatch.setattr("svoi_pravila.benchmarks.llm.GigaChatTextGenerator", _gen_factory)
    monkeypatch.setattr("svoi_pravila.benchmarks.llm.install_recording", _install)
    monkeypatch.setattr("svoi_pravila.benchmarks.llm.load_cases", lambda _path: cases)
    out_path = tmp_path / "report.md"
    code = await async_main(
        _cli_args(
            models=["GigaChat-2", "GigaChat-2"],
            ops=["soften", "decode_stream"],
            out=str(out_path),
            record_fixtures=str(tmp_path / "fixtures"),
            show_outputs=True,
        )
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "ok-p50" in out
    assert "soften" in out
    assert "decode_stream" in out
    assert "ttfc-p50" in out
    written = out_path.read_text(encoding="utf-8")
    assert "soften" in written
    assert "decode_stream" in written


@pytest.mark.unit
async def test_async_main_rate_limited_incomplete(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    class _Client:
        async def aget_models(self) -> object:
            return SimpleNamespace(data=[])

    async def _close(_client: object) -> None:
        return None

    class LimitedGen(FakeTextGenerator):
        async def soften(self, request: SoftenRequest) -> SoftenResult:
            raise GenerationUnavailable(
                UnavailableKind.RATE_LIMITED,
                usage=TokenUsage(),
                attempts=1,
            )

    cases = [
        BenchCase(
            id="s1",
            operation="soften",
            expected_safety="ok",
            soften=SoftenRequest(
                draft="черновик",
                rules=(),
                relationship=RelationshipKind.FRIEND,
                deadline_seconds=1.0,
            ),
        ),
    ]
    monkeypatch.setattr("svoi_pravila.benchmarks.llm.Settings", make_settings)
    monkeypatch.setattr("svoi_pravila.benchmarks.llm.create_gigachat_client", lambda _s: _Client())
    monkeypatch.setattr("svoi_pravila.benchmarks.llm.close_gigachat_client", _close)

    def _limited_factory(_c: object, _s: object) -> LimitedGen:
        return LimitedGen()

    monkeypatch.setattr("svoi_pravila.benchmarks.llm.GigaChatTextGenerator", _limited_factory)
    monkeypatch.setattr("svoi_pravila.benchmarks.llm.load_cases", lambda _path: cases)
    out_path = tmp_path / "partial.md"
    code = await async_main(
        _cli_args(
            models=["GigaChat-2"],
            ops=["soften"],
            out=str(out_path),
        )
    )
    assert code == 1
    captured = capsys.readouterr().out
    assert "INCOMPLETE" in captured
    written = out_path.read_text(encoding="utf-8")
    assert "INCOMPLETE" in written
    assert "soften" in written
    assert "http_status:" not in written


@pytest.mark.unit
@respx.mock
@pytest.mark.parametrize("proxy_mode", ["proxy", "no_proxy"])
async def test_recording_json_writes_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    proxy_mode: str,
) -> None:
    client = _real_gigachat(monkeypatch, proxy_mode=proxy_mode)
    uninstall = install_recording(
        client,
        RecordingConfig(out_dir=tmp_path, name_prefix="json"),
    )
    aclient, _auth = gigachat_http_clients(client)
    respx.post(url__regex=_CHAT_URL_RE).mock(return_value=httpx.Response(200, json={"ok": True}))
    try:
        response = await aclient.post(
            "https://api.giga.chat/v2/chat/completions",
            content=b"{}",
        )
        assert response.status_code == 200
    finally:
        await uninstall()
        await close_gigachat_client(client)
    _assert_one_fixture_contains(tmp_path, "ok")


@pytest.mark.unit
async def test_install_hooks_uninstall_restores_lists(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = _real_gigachat(monkeypatch, proxy_mode="no_proxy")
    aclient, auth_aclient = gigachat_http_clients(client)

    async def existing_api(response: httpx.Response) -> None:
        _ = response

    async def existing_auth(response: httpx.Response) -> None:
        _ = response

    aclient.event_hooks["response"] = [existing_api]
    auth_aclient.event_hooks["response"] = [existing_auth]
    api_before = list(aclient.event_hooks["response"])
    auth_before = list(auth_aclient.event_hooks["response"])
    capture = RateLimitCapture()
    uninstall_rl = install_rate_limit_capture(client, capture)
    assert existing_api in aclient.event_hooks["response"]
    assert len(aclient.event_hooks["response"]) == len(api_before) + 1
    await uninstall_rl()
    assert aclient.event_hooks["response"] == api_before
    assert auth_aclient.event_hooks["response"] == auth_before
    assert aclient.event_hooks["response"][0] is existing_api

    uninstall_rec = install_recording(
        client,
        RecordingConfig(out_dir=tmp_path, name_prefix="restore"),
    )
    assert len(aclient.event_hooks["response"]) == len(api_before) + 1
    await uninstall_rec()
    assert aclient.event_hooks["response"] == api_before
    assert aclient.event_hooks["response"][0] is existing_api
    await close_gigachat_client(client)


@pytest.mark.unit
def test_gigachat_http_clients_rejects_non_httpx() -> None:
    bad = cast(GigaChat, SimpleNamespace(**{"_aclient": object(), "_auth_aclient": object()}))
    with pytest.raises(TypeError, match=r"httpx\.AsyncClient"):
        gigachat_http_clients(bad)


def _soften_case(case_id: str = "s1") -> BenchCase:
    return BenchCase(
        id=case_id,
        operation="soften",
        expected_safety="ok",
        soften=SoftenRequest(
            draft="черновик",
            rules=(),
            relationship=RelationshipKind.FRIEND,
            deadline_seconds=1.0,
        ),
    )


@pytest.mark.unit
async def test_run_case_missing_request_raises() -> None:
    case = BenchCase(
        id="d1",
        operation="decode",
        expected_safety="ok",
        decode=DecodeRequest(
            incoming="входящее",
            rules=(),
            relationship=RelationshipKind.PARTNER,
            deadline_seconds=1.0,
        ),
    )
    with pytest.raises(ValueError, match="missing request"):
        await run_case(FakeTextGenerator(), case, _opts("soften"))


@pytest.mark.unit
async def test_decode_stream_empty_completion_is_invalid() -> None:
    decode_case = BenchCase(
        id="d1",
        operation="decode",
        expected_safety="ok",
        decode=DecodeRequest(
            incoming="входящее",
            rules=(),
            relationship=RelationshipKind.PARTNER,
            deadline_seconds=1.0,
        ),
    )

    class EmptyStream(FakeTextGenerator):
        async def decode_stream(self, request: DecodeRequest) -> AsyncIterator[DecodeEvent]:
            empty: tuple[DecodeEvent, ...] = ()
            for event in empty:
                yield event

    rec = await run_case(EmptyStream(), decode_case, _opts("decode_stream"))
    assert rec.outcome == "invalid_output"
    assert rec.reasons == ("empty_message",)
    assert rec.ttfc_ms is None

    class CompletedOnly(FakeTextGenerator):
        async def decode_stream(self, request: DecodeRequest) -> AsyncIterator[DecodeEvent]:
            yield DecodeCompleted(analysis="analysis", result=self._decode_result(request))

    multi = FakeTextGenerator(stream_chunks=("a", "b"))
    multi_rec = await run_case(multi, decode_case, _opts("decode_stream"))
    assert multi_rec.outcome == "ok"
    assert multi_rec.ttfc_ms is not None
    only = await run_case(CompletedOnly(), decode_case, _opts("decode_stream"))
    assert only.outcome == "ok"
    assert only.ttfc_ms is None


@pytest.mark.unit
async def test_warmup_skips_empty_ops_and_errors_when_none() -> None:
    decode_only = [
        BenchCase(
            id="d1",
            operation="decode",
            expected_safety="ok",
            decode=DecodeRequest(
                incoming="входящее",
                rules=(),
                relationship=RelationshipKind.FAMILY,
                deadline_seconds=1.0,
            ),
        )
    ]
    fake = FakeTextGenerator()
    runtime = BenchmarkRuntime(OutWriter(None), SpendTracker())
    await warmup(
        fake,
        decode_only,
        BenchmarkParams(
            operations=("soften", "decode_stream"),
            model="GigaChat-2",
            repeat=1,
            deadline=5.0,
            show_outputs=False,
        ),
        runtime,
    )
    assert len(fake.decode_stream_calls) == 1
    with pytest.raises(ValueError, match="no cases available"):
        await warmup(
            fake,
            decode_only,
            BenchmarkParams(
                operations=("soften", "help_say"),
                model="GigaChat-2",
                repeat=1,
                deadline=5.0,
                show_outputs=False,
            ),
            BenchmarkRuntime(OutWriter(None), SpendTracker()),
        )


@pytest.mark.unit
async def test_run_benchmark_incremental_out_and_rate_limit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cases = [_soften_case("s1"), _soften_case("s2")]
    out_path = tmp_path / "inc.md"
    writer = OutWriter(out_path)
    capture = _StickyRateLimitCapture()
    capture.http_status = 429
    capture.rate_limit_headers = (("x-ratelimit-remaining", "0"),)

    class Flaky(FakeTextGenerator):
        def __init__(self) -> None:
            super().__init__()
            self._soften_hits = 0

        async def soften(self, request: SoftenRequest) -> SoftenResult:
            self._soften_hits += 1
            if self._soften_hits >= 2:
                raise GenerationUnavailable(
                    UnavailableKind.RATE_LIMITED,
                    usage=TokenUsage(),
                    attempts=1,
                )
            return await super().soften(request)

    params = BenchmarkParams(
        operations=("soften", "help_say"),
        model="GigaChat-2",
        repeat=1,
        deadline=5.0,
        show_outputs=True,
    )
    _rows, incomplete, rate_error, budget_error = await run_benchmark(
        Flaky(),
        cases,
        params,
        BenchmarkRuntime(writer, SpendTracker(), capture),
    )
    assert incomplete is True
    assert rate_error is not None
    assert budget_error is None
    assert rate_error.http_status == 429
    written = out_path.read_text(encoding="utf-8")
    assert "| soften |" in written
    help_rows = [line for line in written.splitlines() if line.startswith("| help_say")]
    assert help_rows == []
    err = capsys.readouterr().err
    assert "s1" in err


@pytest.mark.unit
async def test_run_benchmark_skips_ops_without_cases_and_prints_reasons(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class _FailingStream:
        def __aiter__(self) -> _FailingStream:
            return self

        async def __anext__(self) -> DecodeEvent:
            raise InvalidGenerationOutput(
                (InvalidOutputReason.VARIANT_COUNT,),
                usage=TokenUsage(),
                attempts=1,
            )

    class AlwaysInvalid(FakeTextGenerator):
        def decode_stream(self, request: DecodeRequest) -> AsyncIterator[DecodeEvent]:
            _ = request
            return _FailingStream()

    cases = [
        BenchCase(
            id="d1",
            operation="decode",
            expected_safety="ok",
            decode=DecodeRequest(
                incoming="входящее",
                rules=(),
                relationship=RelationshipKind.FAMILY,
                deadline_seconds=1.0,
            ),
        )
    ]
    params = BenchmarkParams(
        operations=("soften", "decode_stream"),
        model="GigaChat-2",
        repeat=1,
        deadline=5.0,
        show_outputs=False,
    )
    rows, incomplete, rate_error, budget_error = await run_benchmark(AlwaysInvalid(), cases, params)
    assert incomplete is False
    assert rate_error is None
    assert budget_error is None
    assert any("decode_stream" in row for row in rows)
    assert not any(row.startswith("| soften |") for row in rows)
    assert "reasons decode_stream" in capsys.readouterr().out


@pytest.mark.unit
async def test_async_main_model_list_warning_continues(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class _Client:
        async def aget_models(self) -> object:
            raise ServerError("https://example.test", 503, b"down", None)

    async def _close(_client: object) -> None:
        return None

    monkeypatch.setattr("svoi_pravila.benchmarks.llm.Settings", make_settings)
    monkeypatch.setattr("svoi_pravila.benchmarks.llm.create_gigachat_client", lambda _s: _Client())
    monkeypatch.setattr("svoi_pravila.benchmarks.llm.close_gigachat_client", _close)
    monkeypatch.setattr(
        "svoi_pravila.benchmarks.llm.GigaChatTextGenerator",
        lambda _c, _s: FakeTextGenerator(),
    )
    monkeypatch.setattr("svoi_pravila.benchmarks.llm.load_cases", lambda _path: [_soften_case()])
    code = await async_main(_cli_args(ops=["soften"]))
    assert code == 0
    assert "Warning: could not list models" in capsys.readouterr().out


@pytest.mark.unit
async def test_async_main_list_models_maps_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Client:
        async def aget_models(self) -> object:
            raise AuthenticationError("https://example.test", 401, b"no", None)

    async def _close(_client: object) -> None:
        return None

    monkeypatch.setattr("svoi_pravila.benchmarks.llm.Settings", make_settings)
    monkeypatch.setattr("svoi_pravila.benchmarks.llm.create_gigachat_client", lambda _s: _Client())
    monkeypatch.setattr("svoi_pravila.benchmarks.llm.close_gigachat_client", _close)
    monkeypatch.setattr("svoi_pravila.benchmarks.llm.load_cases", lambda _path: [_soften_case()])
    with pytest.raises(GenerationUnavailable) as exc_info:
        await async_main(_cli_args(list_models=True))
    assert exc_info.value.kind == UnavailableKind.AUTH


@pytest.mark.unit
def test_out_writer_header_once_and_none_path(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "out.md"
    writer = OutWriter(path)
    writer.write_header_once("HEADER")
    writer.write_header_once("HEADER")
    writer.write_row("| row |")
    writer.write_reasons("reasons: (none)")
    writer.write_incomplete(http_status=None, rate_limit_headers=())
    writer.write_incomplete(
        http_status=429,
        rate_limit_headers=(("retry-after", "1"), ("x-ratelimit-remaining", "0")),
    )
    writer.write_incomplete(token_budget=(10, 20))
    text = path.read_text(encoding="utf-8")
    assert text.count("HEADER") == 1
    assert "| row |" in text
    assert "INCOMPLETE" in text
    assert "http_status: 429" in text
    assert "header retry-after: 1" in text
    assert "header x-ratelimit-remaining: 0" in text
    assert "token budget reached (spent 10 of 20)" in text
    assert "reasons: (none)" in text
    noop = OutWriter(None)
    noop.write_header_once("H")
    noop.write_row("R")
    noop.write_reasons("reasons: x:1")
    noop.write_incomplete(http_status=429, rate_limit_headers=(("retry-after", "1"),))
    noop.write_incomplete(token_budget=(1, 2))


@pytest.mark.unit
def test_phase_count_first_attempt_metric() -> None:
    assert phase_count("soften") == 1
    assert phase_count("decode_stream") == 2
    soften_ok = CallRecord("ok", 100, "ok", 1, (), None, 10, 5, billable_tokens=15)
    stream_ok = CallRecord(
        "ok",
        300,
        "ok",
        2,
        (),
        None,
        10,
        5,
        billable_tokens=15,
        ttfc_ms=50,
        phase_a_ms=100,
        phase_b_ms=200,
    )
    stream_retry = CallRecord(
        "ok",
        400,
        "ok",
        3,
        (),
        None,
        10,
        5,
        billable_tokens=20,
        ttfc_ms=50,
        phase_a_ms=100,
        phase_b_ms=200,
    )
    soft_row, _ = aggregate_standard_row([soften_ok], operation="soften", model="GigaChat-2")
    assert "100/100" in soft_row
    stream_row, _ = aggregate_row([stream_ok], operation="decode_stream", model="GigaChat-2")
    assert "100/100" in stream_row
    retry_row, _ = aggregate_row([stream_retry], operation="decode_stream", model="GigaChat-2")
    assert "0/100" in retry_row
    assert format_reasons_line({"variant_count": 2}) == "reasons: variant_count:2"
    assert format_reasons_line({}) == "reasons: (none)"


@pytest.mark.unit
async def test_failed_calls_carry_usage_and_mean_attempts() -> None:
    class FailSoft(FakeTextGenerator):
        async def soften(self, request: SoftenRequest) -> SoftenResult:
            raise InvalidGenerationOutput(
                (InvalidOutputReason.VARIANT_COUNT, InvalidOutputReason.VARIANT_COUNT),
                usage=TokenUsage(input=11, output=7, precached=2),
                attempts=2,
            )

    rec = await run_case(FailSoft(), _soften_case(), _opts("soften"))
    assert rec.outcome == "invalid_output"
    assert rec.attempts == 2
    assert rec.input_tokens == 11
    assert rec.billable_tokens == 18
    row, reasons = aggregate_standard_row([rec], operation="soften", model="GigaChat-2")
    assert "2.00" in row
    assert reasons == {"variant_count": 2}
    assert "11/7" in row


@pytest.mark.unit
def test_smoke_and_case_filters() -> None:
    cases = load_cases(Path("benchmarks/data/bench_v2.jsonl"))
    smoke = filter_cases(cases, smoke=True)
    assert len(smoke) == 18
    assert all(c.smoke for c in smoke)
    subset = filter_cases(cases, case_ids=("soften-1", "decode-1"))
    assert {c.id for c in subset} == {"soften-1", "decode-1"}
    with pytest.raises(ValueError, match="unknown case ids"):
        filter_cases(cases, case_ids=("nope",))


@pytest.mark.unit
def test_plan_calls_and_estimates() -> None:
    cases = [
        _soften_case("s1"),
        BenchCase(
            id="d1",
            operation="decode",
            expected_safety="ok",
            decode=DecodeRequest(
                incoming="входящее",
                rules=(),
                relationship=RelationshipKind.FAMILY,
                deadline_seconds=1.0,
            ),
        ),
        BenchCase(
            id="h1",
            operation="help_say",
            expected_safety="ok",
            help_say=HelpSayRequest(
                intent=HelpSayIntent.DECLINE,
                details="детали",
                rules=(),
                relationship=RelationshipKind.WORK,
                deadline_seconds=1.0,
            ),
        ),
    ]
    assert estimate_case_tokens(cases[0], "soften") > 0
    assert estimate_case_tokens(cases[1], "decode_stream") > 0
    assert estimate_case_tokens(cases[2], "help_say") > 0
    plan = plan_calls(
        cases,
        operations=("soften", "help_say", "decode_stream"),
        models=["GigaChat-3-Lightning"],
        repeat=1,
    )
    assert len(plan) == 3
    assert all(calls >= 2 for _m, _op, calls, _t in plan)
    assert chars_to_tokens("") == 0
    with pytest.raises(ValueError, match="missing request"):
        estimate_case_tokens(_soften_case(), "decode_stream")
    empty_plan = plan_calls(
        [_soften_case()],
        operations=("help_say",),
        models=["GigaChat-2"],
        repeat=1,
    )
    assert empty_plan == []


@pytest.mark.unit
async def test_dry_run_makes_no_network(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    created: list[object] = []

    def _forbid_client(_settings: object) -> object:
        created.append(object())
        msg = "create_gigachat_client must not run during dry-run"
        raise AssertionError(msg)

    with respx.mock(assert_all_called=False) as router:
        router.route(host="ngw.devices.sberbank.ru").respond(500)
        monkeypatch.setattr("svoi_pravila.benchmarks.llm.Settings", make_settings)
        monkeypatch.setattr("svoi_pravila.benchmarks.llm.create_gigachat_client", _forbid_client)
        monkeypatch.setattr(
            "svoi_pravila.benchmarks.llm.load_cases",
            lambda _path: load_cases(Path("benchmarks/data/bench_v2.jsonl")),
        )
        code = await async_main(
            _cli_args(
                dry_run=True,
                max_tokens=None,
                models=["GigaChat-3-Lightning"],
                ops=["decode_stream"],
                smoke=True,
                repeat=1,
            )
        )
        assert code == 0
        assert created == []
        assert router.calls.call_count == 0
    out = capsys.readouterr().out
    assert "Dry-run" in out
    assert "decode_stream" in out
    assert "est_tokens=" in out


@pytest.mark.unit
def test_max_tokens_required_for_live_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("svoi_pravila.benchmarks.llm.Settings", make_settings)
    monkeypatch.setattr("svoi_pravila.benchmarks.llm.load_cases", lambda _path: [_soften_case()])
    with pytest.raises(SystemExit, match="max-tokens"):
        asyncio.run(async_main(_cli_args(max_tokens=None, models=["GigaChat-2"], ops=["soften"])))


@pytest.mark.unit
async def test_token_budget_stops_before_call(tmp_path: Path) -> None:
    params = BenchmarkParams(
        operations=("soften",),
        model="GigaChat-2",
        repeat=1,
        deadline=5.0,
        show_outputs=False,
        max_tokens=1,
    )
    out = OutWriter(tmp_path / "budget.md")
    spend = SpendTracker()
    _rows, incomplete, rate_error, budget_error = await run_benchmark(
        FakeTextGenerator(),
        [_soften_case()],
        params,
        BenchmarkRuntime(out, spend),
    )
    assert incomplete is True
    assert rate_error is None
    assert budget_error is not None
    assert isinstance(budget_error, TokenBudgetExceededError)


@pytest.mark.unit
async def test_async_main_budget_incomplete(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    class _Client:
        async def aget_models(self) -> object:
            return SimpleNamespace(data=[])

    async def _close(_client: object) -> None:
        return None

    monkeypatch.setattr("svoi_pravila.benchmarks.llm.Settings", make_settings)
    monkeypatch.setattr("svoi_pravila.benchmarks.llm.create_gigachat_client", lambda _s: _Client())
    monkeypatch.setattr("svoi_pravila.benchmarks.llm.close_gigachat_client", _close)
    monkeypatch.setattr(
        "svoi_pravila.benchmarks.llm.GigaChatTextGenerator",
        lambda _c, _s: FakeTextGenerator(),
    )
    monkeypatch.setattr("svoi_pravila.benchmarks.llm.load_cases", lambda _path: [_soften_case()])
    out_path = tmp_path / "budget.md"
    code = await async_main(
        _cli_args(
            models=["GigaChat-2"],
            ops=["soften"],
            out=str(out_path),
            max_tokens=1,
        )
    )
    assert code == 1
    written = out_path.read_text(encoding="utf-8")
    assert "token budget reached" in written
    assert "Spent billable tokens:" in capsys.readouterr().out


@pytest.mark.unit
def test_estimate_uses_validator_output_caps() -> None:
    case = _soften_case()
    assert case.soften is not None
    prepared = prepare_soften(case.soften)
    one = estimate_prepared_tokens(prepared.system, prepared.user, output_cap=MAX_TOKENS_SOFTEN)
    assert estimate_case_tokens(case, "soften") == 2 * one
    assert MAX_TOKENS_SOFTEN > 0


@pytest.mark.unit
@respx.mock
@pytest.mark.parametrize("proxy_mode", ["proxy", "no_proxy"])
async def test_rate_limit_hooks_capture_c0_headers(
    monkeypatch: pytest.MonkeyPatch,
    proxy_mode: str,
) -> None:
    client = _real_gigachat(monkeypatch, proxy_mode=proxy_mode)
    capture = RateLimitCapture()
    uninstall = install_rate_limit_capture(client, capture)
    aclient, _auth = gigachat_http_clients(client)
    respx.post(url__regex=_CHAT_URL_RE).mock(
        return_value=httpx.Response(
            429,
            headers={
                "Retry-After": "3",
                "X-Ratelimit-Remaining": "0",
                "Authorization": "Bearer secret-token",
                "x-request-id": "abc",
            },
            content=b'{"error":"rate limit"}',
        )
    )
    try:
        response = await aclient.post(
            "https://api.giga.chat/v2/chat/completions",
            content=b"{}",
        )
        assert response.status_code == 429
        assert capture.http_status == 429
        assert capture.rate_limit_headers == (
            ("retry-after", "3"),
            ("x-ratelimit-remaining", "0"),
        )
    finally:
        await uninstall()
        await close_gigachat_client(client)


@pytest.mark.unit
@respx.mock
@pytest.mark.parametrize("proxy_mode", ["proxy", "no_proxy"])
async def test_rate_limit_hooks_ignore_non_429(
    monkeypatch: pytest.MonkeyPatch,
    proxy_mode: str,
) -> None:
    client = _real_gigachat(monkeypatch, proxy_mode=proxy_mode)
    capture = RateLimitCapture()
    uninstall = install_rate_limit_capture(client, capture)
    aclient, _auth = gigachat_http_clients(client)
    respx.post(url__regex=_CHAT_URL_RE).mock(return_value=httpx.Response(200, json={"ok": True}))
    try:
        ok = await aclient.post(
            "https://api.giga.chat/v2/chat/completions",
            content=b"{}",
        )
        assert ok.status_code == 200
        assert capture.http_status is None
        assert capture.rate_limit_headers == ()
    finally:
        await uninstall()
        await close_gigachat_client(client)


@pytest.mark.unit
@respx.mock
@pytest.mark.parametrize("proxy_mode", ["proxy", "no_proxy"])
async def test_rate_limit_capture_feeds_incomplete_out(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    proxy_mode: str,
) -> None:
    _apply_proxy_mode(monkeypatch, proxy_mode)
    monkeypatch.setattr(
        "svoi_pravila.benchmarks.llm.install_rate_limit_capture",
        install_rate_limit_capture,
    )

    def _client_factory(_settings: Settings) -> GigaChat:
        return _real_gigachat(monkeypatch, proxy_mode=proxy_mode)

    monkeypatch.setattr("svoi_pravila.benchmarks.llm.Settings", make_settings)
    monkeypatch.setattr("svoi_pravila.benchmarks.llm.create_gigachat_client", _client_factory)
    monkeypatch.setattr("svoi_pravila.benchmarks.llm.load_cases", lambda _path: [_soften_case()])
    respx.post(url__regex=_CHAT_URL_RE).mock(
        return_value=httpx.Response(
            429,
            content=b'{"error":"rate limit"}',
            headers={
                "Retry-After": "2",
                "x-ratelimit-remaining": "0",
            },
        )
    )
    out_path = tmp_path / "rl.md"
    code = await async_main(
        _cli_args(
            models=["GigaChat-2"],
            ops=["soften"],
            out=str(out_path),
        )
    )
    assert code == 1
    written = out_path.read_text(encoding="utf-8")
    assert "INCOMPLETE" in written
    assert "http_status: 429" in written
    assert "header retry-after: 2" in written
    assert "header x-ratelimit-remaining: 0" in written
