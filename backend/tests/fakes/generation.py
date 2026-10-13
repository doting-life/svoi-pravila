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
    GenerationRequest,
    RewriteRequest,
    RewriteResult,
    SafetyVerdict,
    SuggestRuleProposed,
    SuggestRuleRequest,
    SuggestRuleResult,
    TextGenerator,
    TokenUsage,
    Variant,
)
from svoi_pravila.domain.enums import RuleCategory, VariantKind
from svoi_pravila.domain.text import RuleText


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
        rewrite_result: RewriteResult | None = None,
        decode_result: DecodeResult | None = None,
        stream_chunks: tuple[str, ...] = (),
        stream_error: (
            GenerationRefusedByProvider | InvalidGenerationOutput | GenerationUnavailable | None
        ) = None,
    ) -> None:
        self.rewrite_result = rewrite_result
        self.decode_result = decode_result
        self.suggest_rule_result: SuggestRuleResult | None = None
        self.stream_chunks = stream_chunks
        self.stream_error = stream_error
        self.emit_completed = True
        self.rewrite_error: (
            GenerationRefusedByProvider | InvalidGenerationOutput | GenerationUnavailable | None
        ) = None
        self.suggest_rule_error: (
            GenerationRefusedByProvider | InvalidGenerationOutput | GenerationUnavailable | None
        ) = None
        self.rewrite_block: asyncio.Event | None = None
        self.rewrite_started = asyncio.Event()
        self.rewrite_calls: list[RewriteRequest] = []
        self.decode_stream_calls: list[DecodeRequest] = []
        self.suggest_rule_calls: list[SuggestRuleRequest] = []
        self.max_billable_value: int = 1_000
        self.max_billable_calls: list[GenerationRequest] = []

    def max_billable(self, request: GenerationRequest) -> int:
        """Deterministic worst-case charge for cancel tests."""
        self.max_billable_calls.append(request)
        return self.max_billable_value

    @property
    def call_count(self) -> int:
        """Total rewrite invocations."""
        return len(self.rewrite_calls)

    async def rewrite(self, request: RewriteRequest) -> RewriteResult:
        self.rewrite_calls.append(request)
        self.rewrite_started.set()
        if self.rewrite_block is not None:
            await self.rewrite_block.wait()
        if self.rewrite_error is not None:
            raise self.rewrite_error
        if self.rewrite_result is not None:
            return self.rewrite_result
        return RewriteResult(
            variants=(
                Variant(text="checked", kind=VariantKind.CHECK),
                Variant(text="softened", kind=VariantKind.SOFTER),
                Variant(text="firmed", kind=VariantKind.FIRMER),
            ),
            applied_rule_indexes=(),
            safety=SafetyVerdict.OK,
            meta=_meta("rewrite"),
        )

    def _decode_result(self, request: DecodeRequest) -> DecodeResult:
        if self.decode_result is not None:
            return self.decode_result
        return DecodeResult(
            hypotheses=("hypothesis",),
            underlying_request="request",
            variants=(
                Variant(text="decode-s", kind=VariantKind.SOFTER),
                Variant(text="decode-f", kind=VariantKind.FIRMER),
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

    async def suggest_rule(self, request: SuggestRuleRequest) -> SuggestRuleResult:
        self.suggest_rule_calls.append(request)
        if self.suggest_rule_error is not None:
            raise self.suggest_rule_error
        if self.suggest_rule_result is not None:
            return self.suggest_rule_result
        return SuggestRuleProposed(
            category=RuleCategory.HOW_TO_ASK,
            text=RuleText("Мы говорим спокойно и без резких формулировок"),
            meta=_meta("suggest_rule"),
        )


def as_text_generator(fake: FakeTextGenerator) -> TextGenerator:
    """Widen a fake to the TextGenerator protocol for typed injection."""
    return fake
