"""SSE framing and decode-stream mapping for ``POST /api/v1/decode``."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Mapping
from dataclasses import dataclass
from typing import Any

from svoi_pravila.api.miniapp.errors import MiniappErrorCode
from svoi_pravila.application.decode_sealing import (
    DecodeSealPorts,
    DecodeSealRequest,
    seal_decode_outcome,
)
from svoi_pravila.application.errors import (
    GenerationRefusedByProvider,
    GenerationUnavailable,
    IncomingTextTooLong,
    IncomingTextTooShort,
    InvalidGenerationOutput,
    ScenarioBusy,
    ScenarioQuotaExceeded,
)
from svoi_pravila.application.ports.generation import (
    AnalysisChunk,
    DecodeCompleted,
    SafetyVerdict,
)
from svoi_pravila.application.ports.prepared_results import PreparedResults
from svoi_pravila.application.ports.pseudonymizer import Pseudonymizer
from svoi_pravila.application.ports.rule_sources import RuleSources
from svoi_pravila.application.support_resources import (
    load_applied_rule_template,
    load_crisis_lead,
    load_support_resources,
)
from svoi_pravila.application.use_cases.decode_incoming import (
    DecodeIncoming,
    DecodeIncomingCommand,
)
from svoi_pravila.domain.enums import UsageSurface
from svoi_pravila.domain.ids import TelegramUserId
from svoi_pravila.domain.user import User


@dataclass(frozen=True, slots=True)
class DecodeStreamPorts:
    """Collaborators for the mini-app decode SSE stream."""

    decode_incoming: DecodeIncoming
    prepared_results: PreparedResults
    rule_sources: RuleSources
    pseudonymizer: Pseudonymizer


def format_sse(event: str, data: Mapping[str, Any] | list[Any] | None = None) -> str:
    """Format one SSE event with a JSON ``data`` payload (or empty object)."""
    payload = "{}" if data is None else json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


def _error_code_for(exc: BaseException) -> MiniappErrorCode | None:
    mapping: tuple[tuple[type[BaseException], MiniappErrorCode], ...] = (
        (IncomingTextTooShort, MiniappErrorCode.TEXT_TOO_SHORT),
        (IncomingTextTooLong, MiniappErrorCode.TEXT_TOO_LONG),
        (ScenarioQuotaExceeded, MiniappErrorCode.QUOTA_EXCEEDED),
        (ScenarioBusy, MiniappErrorCode.BUSY),
        (GenerationUnavailable, MiniappErrorCode.GENERATION_UNAVAILABLE),
        (InvalidGenerationOutput, MiniappErrorCode.INVALID_OUTPUT),
    )
    for error_type, code in mapping:
        if isinstance(exc, error_type):
            return code
    return None


def _completed_payload(
    completed: DecodeCompleted,
    *,
    insert_queries: tuple[str | None, ...],
    rule_source_token: str | None,
) -> dict[str, Any]:
    variants = [
        {
            "firmness": variant.firmness.value,
            "text": variant.text,
            "insert_query": insert_queries[index] if index < len(insert_queries) else None,
        }
        for index, variant in enumerate(completed.result.variants)
    ]
    applied = [
        {
            "category": rule.category.value,
            "text": rule.text,
            "effective_since": rule.effective_since.isoformat().replace("+00:00", "Z"),
        }
        for rule in completed.applied_rules
    ]
    return {
        "safety": completed.result.safety.value,
        "variants": variants,
        "applied_rules": applied,
        "applied_rule_template": load_applied_rule_template(),
        "rule_source_token": rule_source_token,
    }


async def _drain_use_case(agen: AsyncGenerator[object]) -> None:
    """Resume DecodeIncoming after ``DecodeCompleted`` so usage accounting finishes."""
    try:
        await agen.__anext__()
    except StopAsyncIteration:
        return


async def iter_decode_sse(
    ports: DecodeStreamPorts,
    *,
    actor: User,
    telegram_user_id: TelegramUserId,
    text: str,
) -> AsyncGenerator[str]:
    """Yield SSE frames for a mini-app decode (surface ``miniapp``)."""
    agen = ports.decode_incoming.execute(
        DecodeIncomingCommand(
            telegram_user_id=telegram_user_id,
            incoming_text=text,
            surface=UsageSurface.MINIAPP,
        )
    )
    try:
        while True:
            try:
                event = await agen.__anext__()
            except StopAsyncIteration:
                return
            if isinstance(event, AnalysisChunk):
                yield format_sse("analysis", {"chunk": event.text})
                continue
            # Persist usage before the terminal SSE frame: the client may close
            # the connection as soon as it receives the last event.
            await _drain_use_case(agen)
            safety = event.result.safety
            if safety is SafetyVerdict.CRISIS:
                yield format_sse(
                    "crisis",
                    {
                        "lead": load_crisis_lead(),
                        "resources": list(load_support_resources()),
                    },
                )
                return
            if safety is SafetyVerdict.REFUSE_MANIPULATION:
                yield format_sse("refused")
                return
            sealed = await seal_decode_outcome(
                DecodeSealPorts(
                    prepared_results=ports.prepared_results,
                    rule_sources=ports.rule_sources,
                    pseudonymizer=ports.pseudonymizer,
                ),
                DecodeSealRequest(
                    telegram_user_id=telegram_user_id.value,
                    incoming_text=text,
                    completed=event,
                    active_contact_id=actor.active_contact_id,
                ),
            )
            yield format_sse(
                "completed",
                _completed_payload(
                    event,
                    insert_queries=sealed.insert_queries,
                    rule_source_token=sealed.rule_source_token,
                ),
            )
            return
    except GenerationRefusedByProvider:
        yield format_sse("refused")
    except BaseException as exc:
        if isinstance(exc, (asyncio.CancelledError, GeneratorExit)):
            raise
        code = _error_code_for(exc)
        if code is None:
            raise
        yield format_sse("error", {"code": code.value})
    finally:
        await agen.aclose()
