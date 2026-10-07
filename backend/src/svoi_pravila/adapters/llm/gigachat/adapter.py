"""GigaChat implementation of the TextGenerator port."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

from gigachat import GigaChat

from svoi_pravila.adapters.llm.gigachat.analysis_stream import (
    AnalysisPhase,
    AnalysisStreamParams,
)
from svoi_pravila.adapters.llm.gigachat.attempt_policy import (
    AttemptState,
    token_usage_from_state,
)
from svoi_pravila.adapters.llm.gigachat.estimate import max_billable_for_request
from svoi_pravila.adapters.llm.gigachat.prepared import (
    PreparedMessages,
    prepare_decode,
    prepare_decode_analysis,
    prepare_help_say,
    prepare_soften,
    prepare_suggest_rule,
)
from svoi_pravila.adapters.llm.gigachat.schemas import (
    DecodeOut,
    HelpSayOut,
    SoftenOut,
    SuggestRuleOut,
)
from svoi_pravila.adapters.llm.gigachat.structured import StructuredCallParams, structured_call
from svoi_pravila.adapters.llm.gigachat.validation import (
    MAX_TOKENS_DECODE,
    MAX_TOKENS_HELP_SAY,
    MAX_TOKENS_SOFTEN,
    MAX_TOKENS_SUGGEST,
    VariantValidation,
    to_decode_result,
    to_suggest_rule_result,
    validate_variants,
)
from svoi_pravila.application.errors import (
    GenerationRefusedByProvider,
    GenerationUnavailable,
    InvalidGenerationOutput,
    UnavailableKind,
)
from svoi_pravila.application.ports.generation import (
    DecodeCompleted,
    DecodeEvent,
    DecodeRequest,
    DecodeResult,
    GenerationMeta,
    GenerationRequest,
    HelpSayRequest,
    HelpSayResult,
    SoftenRequest,
    SoftenResult,
    SuggestRuleRequest,
    SuggestRuleResult,
    TokenUsage,
)
from svoi_pravila.config import GigaChatRuntimeSettings


def _deadline_at(deadline_seconds: float) -> float:
    return asyncio.get_running_loop().time() + deadline_seconds


def _combine_invalid(
    phase_a_usage: TokenUsage,
    phase_a_attempts: int,
    exc: InvalidGenerationOutput,
) -> InvalidGenerationOutput:
    return InvalidGenerationOutput(
        exc.reasons,
        usage=phase_a_usage + exc.usage,
        attempts=phase_a_attempts + exc.attempts,
        model=exc.model,
        prompt_version=exc.prompt_version,
    )


def _combine_refused(
    phase_a_usage: TokenUsage,
    phase_a_attempts: int,
    exc: GenerationRefusedByProvider,
) -> GenerationRefusedByProvider:
    return GenerationRefusedByProvider(
        usage=phase_a_usage + exc.usage,
        attempts=phase_a_attempts + exc.attempts,
        model=exc.model,
        prompt_version=exc.prompt_version,
    )


def _combine_unavailable(
    phase_a_usage: TokenUsage,
    phase_a_attempts: int,
    exc: GenerationUnavailable,
) -> GenerationUnavailable:
    return GenerationUnavailable(
        exc.kind,
        usage=phase_a_usage + exc.usage,
        attempts=phase_a_attempts + exc.attempts,
        model=exc.model,
        prompt_version=exc.prompt_version,
    )


def _timeout_unavailable(state: AttemptState) -> GenerationUnavailable:
    return GenerationUnavailable(
        UnavailableKind.TIMEOUT,
        usage=token_usage_from_state(state),
        attempts=state.attempts,
        model=state.model,
        prompt_version=state.prompt_version,
    )


class GigaChatTextGenerator:
    """TextGenerator backed by the official GigaChat async SDK."""

    def __init__(self, client: GigaChat, settings: GigaChatRuntimeSettings) -> None:
        self._client = client
        self._settings = settings

    def prepare_soften(self, request: SoftenRequest) -> PreparedMessages:
        """Build rendered system/user messages for soften (also used by tests)."""
        return prepare_soften(request)

    def prepare_help_say(self, request: HelpSayRequest) -> PreparedMessages:
        """Build rendered system/user messages for help-say."""
        return prepare_help_say(request)

    def prepare_decode(self, request: DecodeRequest, *, analysis: str) -> PreparedMessages:
        """Build rendered system/user messages for structured decode phase B."""
        return prepare_decode(request, analysis=analysis)

    def prepare_decode_analysis(self, request: DecodeRequest) -> PreparedMessages:
        """Build rendered system/user messages for phase-A analysis."""
        return prepare_decode_analysis(request)

    def prepare_suggest_rule(self, request: SuggestRuleRequest) -> PreparedMessages:
        """Build rendered system/user messages for suggest_rule."""
        return prepare_suggest_rule(request)

    def max_billable(self, request: GenerationRequest) -> int:
        """Worst-case billable tokens from rendered messages, caps, and retries."""
        return max_billable_for_request(request)

    async def soften(self, request: SoftenRequest) -> SoftenResult:
        prepared = self.prepare_soften(request)
        state = AttemptState(
            started=time.perf_counter(),
            model=self._settings.gigachat_model_soften,
            prompt_version=prepared.prompt_version,
        )

        def build(parsed: SoftenOut, meta: GenerationMeta) -> SoftenResult:
            variants, indexes, safety = validate_variants(
                parsed.variants,
                VariantValidation(
                    applied=list(parsed.applied_rule_indexes),
                    safety_raw=parsed.safety.value,
                    rule_count=len(request.rules),
                    min_variants=2,
                    max_variants=3,
                    require_all_firmness=False,
                    usage=meta.usage,
                    attempts=meta.attempts,
                    model=meta.model,
                    prompt_version=meta.prompt_version,
                    operation="soften",
                ),
            )
            return SoftenResult(
                variants=variants,
                applied_rule_indexes=indexes,
                safety=safety,
                meta=meta,
            )

        try:
            async with asyncio.timeout_at(_deadline_at(request.deadline_seconds)):
                return await structured_call(
                    StructuredCallParams(
                        client=self._client,
                        operation="soften",
                        model=self._settings.gigachat_model_soften,
                        prepared=prepared,
                        response_format=SoftenOut,
                        max_tokens=MAX_TOKENS_SOFTEN,
                    ),
                    build,
                    state=state,
                )
        except TimeoutError:
            raise _timeout_unavailable(state) from None

    async def help_say(self, request: HelpSayRequest) -> HelpSayResult:
        prepared = self.prepare_help_say(request)
        state = AttemptState(
            started=time.perf_counter(),
            model=self._settings.gigachat_model_help_say,
            prompt_version=prepared.prompt_version,
        )

        def build(parsed: HelpSayOut, meta: GenerationMeta) -> HelpSayResult:
            variants, indexes, safety = validate_variants(
                parsed.variants,
                VariantValidation(
                    applied=list(parsed.applied_rule_indexes),
                    safety_raw=parsed.safety.value,
                    rule_count=len(request.rules),
                    min_variants=2,
                    max_variants=3,
                    require_all_firmness=False,
                    usage=meta.usage,
                    attempts=meta.attempts,
                    model=meta.model,
                    prompt_version=meta.prompt_version,
                    operation="help_say",
                ),
            )
            return HelpSayResult(
                variants=variants,
                applied_rule_indexes=indexes,
                safety=safety,
                meta=meta,
            )

        try:
            async with asyncio.timeout_at(_deadline_at(request.deadline_seconds)):
                return await structured_call(
                    StructuredCallParams(
                        client=self._client,
                        operation="help_say",
                        model=self._settings.gigachat_model_help_say,
                        prepared=prepared,
                        response_format=HelpSayOut,
                        max_tokens=MAX_TOKENS_HELP_SAY,
                    ),
                    build,
                    state=state,
                )
        except TimeoutError:
            raise _timeout_unavailable(state) from None

    async def _decode_structured(
        self,
        request: DecodeRequest,
        *,
        analysis: str,
        state: AttemptState,
    ) -> DecodeResult:
        prepared = self.prepare_decode(request, analysis=analysis)

        def build(parsed: DecodeOut, meta: GenerationMeta) -> DecodeResult:
            return to_decode_result(parsed, meta, rule_count=len(request.rules))

        return await structured_call(
            StructuredCallParams(
                client=self._client,
                operation="decode_stream",
                model=self._settings.gigachat_model_decode,
                prepared=prepared,
                response_format=DecodeOut,
                max_tokens=MAX_TOKENS_DECODE,
                phase="structured",
            ),
            build,
            state=state,
        )

    async def decode_stream(self, request: DecodeRequest) -> AsyncIterator[DecodeEvent]:
        model = self._settings.gigachat_model_decode
        started = time.perf_counter()
        analysis_prepared = self.prepare_decode_analysis(request)
        decode_prompt_version = self.prepare_decode(request, analysis="").prompt_version
        composite_prompt = f"{analysis_prepared.prompt_version}+{decode_prompt_version}"
        phase = AnalysisPhase(
            AnalysisStreamParams(
                client=self._client,
                model=model,
                prepared=analysis_prepared,
                started=started,
                prompt_version=composite_prompt,
            )
        )
        phase_b_state = AttemptState(
            started=started,
            model=model,
            prompt_version=composite_prompt,
        )
        try:
            async with asyncio.timeout_at(_deadline_at(request.deadline_seconds)):
                async for chunk in phase.stream():
                    yield chunk
                phase_a = phase.result
                try:
                    structured = await self._decode_structured(
                        request,
                        analysis=phase_a.text,
                        state=phase_b_state,
                    )
                except InvalidGenerationOutput as exc:
                    raise _combine_invalid(phase_a.usage, phase_a.attempts, exc) from None
                except GenerationRefusedByProvider as exc:
                    raise _combine_refused(phase_a.usage, phase_a.attempts, exc) from None
                except GenerationUnavailable as exc:
                    raise _combine_unavailable(phase_a.usage, phase_a.attempts, exc) from None
                combined = GenerationMeta(
                    model=model,
                    prompt_version=(
                        f"{analysis_prepared.prompt_version}+{structured.meta.prompt_version}"
                    ),
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    attempts=phase_a.attempts + structured.meta.attempts,
                    usage=phase_a.usage + structured.meta.usage,
                )
                yield DecodeCompleted(
                    analysis=phase_a.text,
                    result=DecodeResult(
                        hypotheses=structured.hypotheses,
                        underlying_request=structured.underlying_request,
                        variants=structured.variants,
                        applied_rule_indexes=structured.applied_rule_indexes,
                        safety=structured.safety,
                        meta=combined,
                    ),
                )
        except TimeoutError:
            if phase.completed:
                phase_a = phase.result
                raise _combine_unavailable(
                    phase_a.usage,
                    phase_a.attempts,
                    _timeout_unavailable(phase_b_state),
                ) from None
            raise _timeout_unavailable(phase.state) from None

    async def suggest_rule(self, request: SuggestRuleRequest) -> SuggestRuleResult:
        prepared = self.prepare_suggest_rule(request)
        state = AttemptState(
            started=time.perf_counter(),
            model=self._settings.gigachat_model_suggest,
            prompt_version=prepared.prompt_version,
        )

        def build(parsed: SuggestRuleOut, meta: GenerationMeta) -> SuggestRuleResult:
            return to_suggest_rule_result(parsed, meta)

        try:
            async with asyncio.timeout_at(_deadline_at(request.deadline_seconds)):
                return await structured_call(
                    StructuredCallParams(
                        client=self._client,
                        operation="suggest_rule",
                        model=self._settings.gigachat_model_suggest,
                        prepared=prepared,
                        response_format=SuggestRuleOut,
                        max_tokens=MAX_TOKENS_SUGGEST,
                    ),
                    build,
                    state=state,
                )
        except TimeoutError:
            raise _timeout_unavailable(state) from None
