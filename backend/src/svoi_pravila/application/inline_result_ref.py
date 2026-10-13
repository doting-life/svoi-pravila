"""Encode and parse inline ``result_id`` values (scenario + variant kind, never text)."""

from __future__ import annotations

from svoi_pravila.application.errors import InvalidInlineResultRef
from svoi_pravila.domain.enums import UsageScenario, VariantKind

_MAX_RESULT_ID_BYTES = 64
_REF_PARTS = 4
_SCENARIOS = {item.value: item for item in UsageScenario}
_KINDS = {item.value: item for item in VariantKind}


def encode_inline_result_ref(scenario: UsageScenario, kind: VariantKind) -> str:
    """Return a ``result_id`` that encodes scenario and kind only."""
    return f"s:{scenario.value}:k:{kind.value}"


def parse_inline_result_ref(result_ref: str) -> tuple[UsageScenario, VariantKind]:
    """Parse a ``result_id``; never accept conversation text."""
    if len(result_ref.encode()) > _MAX_RESULT_ID_BYTES:
        raise InvalidInlineResultRef()
    parts = result_ref.split(":")
    if len(parts) != _REF_PARTS or parts[0] != "s" or parts[2] != "k":
        raise InvalidInlineResultRef()
    scenario = _SCENARIOS.get(parts[1])
    kind = _KINDS.get(parts[3])
    if scenario is None or kind is None:
        raise InvalidInlineResultRef()
    return scenario, kind
