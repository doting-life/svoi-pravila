"""Channel-neutral sealing of prepared insert tokens and rule-source tokens after decode."""

from __future__ import annotations

from dataclasses import dataclass

from svoi_pravila.application.ports.generation import DecodeCompleted, SafetyVerdict
from svoi_pravila.application.ports.prepared_results import PreparedResults, PreparedVariant
from svoi_pravila.application.ports.pseudonymizer import Pseudonymizer
from svoi_pravila.application.ports.rule_sources import RuleSources
from svoi_pravila.application.rule_source import RuleSourcePayload
from svoi_pravila.application.use_cases.suggest_rule_from_decode import RULE_SOURCE_PURPOSE
from svoi_pravila.domain.ids import ContactId

_PREPARED_PURPOSE = "prepared"


@dataclass(frozen=True, slots=True)
class DecodeSealPorts:
    """Ports required to seal prepared and rule-source tokens."""

    prepared_results: PreparedResults
    rule_sources: RuleSources
    pseudonymizer: Pseudonymizer


@dataclass(frozen=True, slots=True)
class DecodeSealRequest:
    """Inputs for sealing after an OK decode."""

    telegram_user_id: int
    incoming_text: str
    completed: DecodeCompleted
    active_contact_id: ContactId | None


@dataclass(frozen=True, slots=True)
class DecodeSealedTokens:
    """Insert queries (prepared-result tokens) and optional raw rule-source token."""

    insert_queries: tuple[str | None, ...]
    rule_source_token: str | None


async def seal_decode_outcome(
    ports: DecodeSealPorts,
    request: DecodeSealRequest,
) -> DecodeSealedTokens:
    """Store prepared variants and seal the rule-source payload for an OK decode.

    Returns opaque redeem tokens for the mini-app. Non-OK safety yields null tokens.
    """
    insert_queries = await _store_insert_tokens(ports, request)
    rule_source_token = await _store_rule_source_token(ports, request)
    return DecodeSealedTokens(
        insert_queries=insert_queries,
        rule_source_token=rule_source_token,
    )


async def _store_insert_tokens(
    ports: DecodeSealPorts,
    request: DecodeSealRequest,
) -> tuple[str | None, ...]:
    result = request.completed.result
    if result.safety is not SafetyVerdict.OK:
        return tuple(None for _ in result.variants)
    pseudonym = ports.pseudonymizer.pseudonymize(_PREPARED_PURPOSE, str(request.telegram_user_id))
    tokens: list[str | None] = []
    for variant in result.variants:
        if not variant.text:
            tokens.append(None)
            continue
        token = await ports.prepared_results.store(
            pseudonym,
            PreparedVariant(firmness=variant.firmness, text=variant.text),
        )
        tokens.append(token)
    return tuple(tokens)


async def _store_rule_source_token(
    ports: DecodeSealPorts,
    request: DecodeSealRequest,
) -> str | None:
    if request.completed.result.safety is not SafetyVerdict.OK:
        return None
    if request.active_contact_id is None:
        return None
    pseudonym = ports.pseudonymizer.pseudonymize(RULE_SOURCE_PURPOSE, str(request.telegram_user_id))
    return await ports.rule_sources.store(
        pseudonym,
        RuleSourcePayload(
            contact_id=request.active_contact_id,
            incoming_text=request.incoming_text,
        ),
    )
