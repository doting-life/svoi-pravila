"""Lexicon resources: the versioned loader used by the crisis screen."""

from __future__ import annotations

import pytest

from svoi_pravila.application.lexicons import load_lexicon
from svoi_pravila.domain.lexicon import lexicon_hit


@pytest.mark.unit
def test_load_lexicon_reads_the_named_versioned_resource() -> None:
    lexicon = load_lexicon("svoi_pravila.domain.safety", "crisis_patterns", "ru", "v2")
    assert lexicon_hit("не хочу жить", lexicon)
    assert all(pattern.pattern.startswith("\\b") for pattern in lexicon.patterns)


@pytest.mark.unit
def test_load_lexicon_fails_on_a_missing_version() -> None:
    with pytest.raises(FileNotFoundError):
        load_lexicon("svoi_pravila.domain.safety", "crisis_patterns", "ru", "v0")
