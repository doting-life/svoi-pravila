"""Rewrite slot contract: an empty ``check`` slot means "no corrections"."""

from __future__ import annotations

from svoi_pravila.application.ports.generation import Variant
from svoi_pravila.domain.enums import VariantKind


def fill_empty_check(draft: str, variants: tuple[Variant, ...]) -> tuple[tuple[Variant, ...], bool]:
    """Replace an empty ``check`` variant with the draft; report whether that happened."""
    filled = False
    result: list[Variant] = []
    for variant in variants:
        if variant.kind is VariantKind.CHECK and not variant.text:
            result.append(Variant(text=draft, kind=VariantKind.CHECK))
            filled = True
        else:
            result.append(variant)
    return tuple(result), filled
