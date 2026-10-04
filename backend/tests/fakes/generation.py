"""In-memory TextGenerator for use-case tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from svoi_pravila.application.errors import (
    GenerationRefusedByProvider,
    GenerationUnavailable,
    InvalidGenerationOutput,
)
from svoi_pravila.application.ports.generation import (
    AnalysisChunk,
    DecodeCompleted,
    DecodeEvent,
    DecodeRequest,
    DecodeResult,
    GenerationMeta,
    HelpSayRequest,
    HelpSayResult,
    SafetyVerdict,
    SoftenRequest,
    SoftenResult,
    TextGenerator,
    TokenUsage,
    Variant,
)
from svoi_pravila.domain.enums import Firmness


def _meta(operation: str) -> GenerationMeta:
    return GenerationMeta(
        model="fake",
        prompt_version=f"{operation}@v1",
        latency_ms=1,
        attempts=1 if operation != "decode" else 2,
        usage=TokenUsage(input=1, output=1, precached=0),
    )


class FakeTextGenerator:
    """Configurable in-memory TextGenerator."""

    def __init__(
        self,
        *,
        soften_result: SoftenResult | None = None,
        help_say_result: HelpSayResult | None = None,
        decode_result: DecodeResult | None = None,
        stream_chunks: tuple[str, ...] = (),
        stream_error: (
            GenerationRefusedByProvider | InvalidGenerationOutput | GenerationUnavailable | None
        ) = None,
    ) -> None:
        self.soften_result = soften_result
        self.help_say_result = help_say_result
        self.decode_result = decode_result
        self.stream_chunks = stream_chunks
        self.stream_error = stream_error
        self.emit_completed = True
        self.soften_error: (
            GenerationRefusedByProvider | InvalidGenerationOutput | GenerationUnavailable | None
        ) = None
        self.help_say_error: (
            GenerationRefusedByProvider | InvalidGenerationOutput | GenerationUnavailable | None
        ) = None
        self.soften_block: asyncio.Event | None = None
        self.soften_started = asyncio.Event()
        self.soften_calls: list[SoftenRequest] = []
        self.help_say_calls: list[HelpSayRequest] = []
        self.decode_stream_calls: list[DecodeRequest] = []

    async def soften(self, request: SoftenRequest) -> SoftenResult:
        self.soften_calls.append(request)
        self.soften_started.set()
        if self.soften_block is not None:
            await self.soften_block.wait()
        if self.soften_error is not None:
            raise self.soften_error
        if self.soften_result is not None:
            return self.soften_result
        return SoftenResult(
            variants=(
                Variant(text="softened-a", firmness=Firmness.GENTLE),
                Variant(text="softened-b", firmness=Firmness.BALANCED),
            ),
            applied_rule_indexes=(),
            safety=SafetyVerdict.OK,
            meta=_meta("soften"),
        )

    async def help_say(self, request: HelpSayRequest) -> HelpSayResult:
        self.help_say_calls.append(request)
        if self.help_say_error is not None:
            raise self.help_say_error
        if self.help_say_result is not None:
            return self.help_say_result
        return HelpSayResult(
            variants=(
                Variant(text="help-a", firmness=Firmness.GENTLE),
                Variant(text="help-b", firmness=Firmness.FIRM),
            ),
            applied_rule_indexes=(),
            safety=SafetyVerdict.OK,
            meta=_meta("help_say"),
        )

    def _decode_result(self, request: DecodeRequest) -> DecodeResult:
        if self.decode_result is not None:
            return self.decode_result
        return DecodeResult(
            hypotheses=("hypothesis",),
            underlying_request="request",
            variants=(
                Variant(text="decode-g", firmness=Firmness.GENTLE),
                Variant(text="decode-b", firmness=Firmness.BALANCED),
                Variant(text="decode-f", firmness=Firmness.FIRM),
            ),
            applied_rule_indexes=(),
            safety=SafetyVerdict.OK,
            meta=_meta("decode"),
        )

    async def decode_stream(self, request: DecodeRequest) -> AsyncIterator[DecodeEvent]:
        self.decode_stream_calls.append(request)
        for chunk in self.stream_chunks:
            yield AnalysisChunk(text=chunk)
        if self.stream_error is not None:
            raise self.stream_error
        if not self.emit_completed:
            return
        result = self._decode_result(request)
        yield DecodeCompleted(analysis="".join(self.stream_chunks) or "analysis", result=result)


def as_text_generator(fake: FakeTextGenerator) -> TextGenerator:
    """Widen a fake to the TextGenerator protocol for typed injection."""
    return fake
